import 'dart:async';
import 'dart:convert';

import 'package:dio/dio.dart';
import 'package:flutter/foundation.dart';

import 'api_service.dart';
import 'session.dart';

/* ============================================================
   Notifications (the bell in the app bar)
   ============================================================
   Backend: app/api/notifications.py
     GET  /notifications/unread-count  -> {unread_count, latest_id}
     GET  /notifications               -> {unread_count, latest_id, notifications: [...]}
     POST /notifications/{id}/read
     POST /notifications/read-all

   Survey persons get "product_assigned" when an admin gives them a product.
   Admins (if they use the app) also get "survey_missing_video".

   The unread count is checked every 30 seconds while the app is open and
   whenever it comes back to the foreground (HomeShell calls start / stop / refresh).
*/

class AppNotification {
  const AppNotification({
    required this.id,
    required this.kind,
    required this.title,
    required this.message,
    required this.data,
    required this.isRead,
    required this.resolved,
    this.createdAt,
  });

  static const productAssigned = 'product_assigned';
  static const missingVideo = 'survey_missing_video';

  final int id;
  final String kind;
  final String title;
  final String message;
  final Map<String, dynamic> data;
  final bool isRead;
  final bool resolved;
  final DateTime? createdAt;

  /// Text from `data`, or null when empty.
  String? text(String key) {
    final v = data[key];
    if (v == null) return null;
    final s = '$v'.trim();
    return s.isEmpty ? null : s;
  }

  /// A time from `data` (UTC on the server), in the phone's local time.
  DateTime? time(String key) => _toLocal(data[key]);

  AppNotification copyWith({bool? isRead}) => AppNotification(
        id: id,
        kind: kind,
        title: title,
        message: message,
        data: data,
        isRead: isRead ?? this.isRead,
        resolved: resolved,
        createdAt: createdAt,
      );

  factory AppNotification.fromJson(Map<String, dynamic> j) => AppNotification(
        id: (j['id'] as num?)?.toInt() ?? 0,
        kind: '${j['kind'] ?? ''}',
        title: '${j['title'] ?? ''}',
        message: '${j['message'] ?? ''}',
        data: j['data'] is Map ? Map<String, dynamic>.from(j['data'] as Map) : const {},
        isRead: j['is_read'] == true,
        resolved: j['resolved'] == true,
        createdAt: _toLocal(j['created_at']),
      );
}

DateTime? _toLocal(dynamic v) {
  if (v == null) return null;
  final s = '$v'.trim();
  if (s.isEmpty) return null;
  final hasZone = RegExp(r'([zZ]|[+-]\d{2}:?\d{2})$').hasMatch(s);
  return DateTime.tryParse(hasZone ? s : '${s}Z')?.toLocal();
}

class Notifications {
  Notifications._();

  static const pollEvery = Duration(seconds: 30);

  /// Number shown on the bell.
  static final ValueNotifier<int> unread = ValueNotifier<int>(0);

  /// The list shown on the notifications screen (newest first).
  static final ValueNotifier<List<AppNotification>> items = ValueNotifier<List<AppNotification>>(const []);

  /// Fires with the newest notification when one arrives while the app is open.
  static final ValueNotifier<AppNotification?> arrived = ValueNotifier<AppNotification?>(null);

  static Timer? _timer;
  static int _latestSeen = -1; // -1: nothing checked yet (don't announce old ones)
  static bool _busy = false;

  static final Dio _dio = Dio(
    BaseOptions(
      baseUrl: ApiConfig.baseUrl,
      connectTimeout: const Duration(seconds: 10),
      receiveTimeout: const Duration(seconds: 20),
      headers: {'Accept': 'application/json'},
    ),
  )..interceptors.add(InterceptorsWrapper(onRequest: (options, handler) {
      final token = Session.token;
      if (token != null) options.headers['Authorization'] = 'Bearer $token';
      handler.next(options);
    }));

  static dynamic _json(dynamic data) => data is String ? jsonDecode(data) : data;

  /// Start checking (signed in, app in the foreground).
  static void start() {
    _timer?.cancel();
    refresh();
    _timer = Timer.periodic(pollEvery, (_) => refresh());
  }

  /// Stop checking (app in the background, or signed out).
  static void stop() {
    _timer?.cancel();
    _timer = null;
  }

  /// Forget everything (sign-out), so the next account starts clean.
  static void clear() {
    stop();
    unread.value = 0;
    items.value = const [];
    arrived.value = null;
    _latestSeen = -1;
  }

  /// Checks the unread count. When something new arrived, loads the list too
  /// and announces the newest one.
  static Future<void> refresh() async {
    if (!Session.signedIn || _busy) return;
    _busy = true;
    try {
      final res = await _dio.get('/notifications/unread-count');
      final data = _json(res.data);
      if (data is! Map) return;
      unread.value = (data['unread_count'] as num?)?.toInt() ?? 0;
      final latest = (data['latest_id'] as num?)?.toInt() ?? 0;
      if (latest != _latestSeen) {
        final first = _latestSeen < 0;
        final previous = _latestSeen;
        _latestSeen = latest;
        await load(quiet: true);
        if (!first && latest > previous) {
          final newest = items.value.where((n) => n.id > previous && !n.isRead).toList();
          if (newest.isNotEmpty) arrived.value = newest.first;
        }
      }
    } catch (_) {
      // Offline or the server is restarting: try again at the next check.
    } finally {
      _busy = false;
    }
  }

  /// Loads the newest notifications. Throws ApiException unless [quiet].
  static Future<void> load({bool quiet = false}) async {
    try {
      final res = await _dio.get('/notifications', queryParameters: {'limit': 50});
      final data = _json(res.data);
      if (data is! Map) return;
      final list = data['notifications'];
      items.value = list is List
          ? list.whereType<Map>().map((m) => AppNotification.fromJson(Map<String, dynamic>.from(m))).toList()
          : const [];
      unread.value = (data['unread_count'] as num?)?.toInt() ?? unread.value;
      final latest = (data['latest_id'] as num?)?.toInt();
      if (latest != null && _latestSeen >= 0) _latestSeen = latest;
    } catch (e) {
      if (quiet) return;
      final offline = e is DioException && e.type == DioExceptionType.connectionError;
      throw ApiException(offline
          ? "Can't reach the server at ${ApiConfig.baseUrl}."
          : 'Notifications could not be loaded. Pull down to try again.');
    }
  }

  static Future<void> markRead(AppNotification n) async {
    if (n.isRead) return;
    // Show it as read straight away; the server confirms the count.
    items.value = [for (final x in items.value) x.id == n.id ? x.copyWith(isRead: true) : x];
    final left = unread.value - 1;
    unread.value = left < 0 ? 0 : left;
    try {
      final res = await _dio.post('/notifications/${n.id}/read');
      final data = _json(res.data);
      if (data is Map) unread.value = (data['unread_count'] as num?)?.toInt() ?? unread.value;
    } catch (_) {
      // The next check corrects the count.
    }
  }

  static Future<void> markAllRead() async {
    items.value = [for (final x in items.value) x.copyWith(isRead: true)];
    unread.value = 0;
    try {
      await _dio.post('/notifications/read-all');
    } catch (_) {
      refresh();
    }
  }
}
