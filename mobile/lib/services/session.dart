import 'dart:convert';
import 'dart:io';

import 'package:dio/dio.dart';
import 'package:flutter/foundation.dart';
import 'package:path_provider/path_provider.dart';

import 'api_service.dart';
import 'notification_service.dart';
import 'survey_api.dart';
import 'survey_queue.dart';

/// The signed-in survey executive.
class SessionUser {
  const SessionUser({
    required this.id,
    required this.name,
    this.mobile,
    this.email,
    this.role = 'executive',
    this.canViewHistory = false,
  });

  final int id;
  final String name;
  final String? mobile;
  final String? email;
  final String role;

  /// Set by an admin: may this executive see feedback history and reports?
  final bool canViewHistory;

  String get contact => mobile ?? email ?? '';

  Map<String, dynamic> toJson() =>
      {'id': id, 'name': name, 'mobile': mobile, 'email': email, 'role': role, 'can_view_history': canViewHistory};

  factory SessionUser.fromJson(Map<String, dynamic> j) => SessionUser(
        id: (j['id'] as num?)?.toInt() ?? 0,
        name: '${j['name'] ?? ''}',
        mobile: j['mobile'] as String?,
        email: j['email'] as String?,
        role: '${j['role'] ?? 'executive'}',
        canViewHistory: j['can_view_history'] == true,
      );
}

/// This executive's own survey numbers (from the server).
class MyStats {
  const MyStats({this.total = 0, this.today = 0, this.thisWeek = 0, this.analyzed = 0});

  final int total;
  final int today;
  final int thisWeek;
  final int analyzed;

  factory MyStats.fromJson(Map<String, dynamic> j) => MyStats(
        total: (j['total'] as num?)?.toInt() ?? 0,
        today: (j['today'] as num?)?.toInt() ?? 0,
        thisWeek: (j['this_week'] as num?)?.toInt() ?? 0,
        analyzed: (j['analyzed'] as num?)?.toInt() ?? 0,
      );
}

/// Sign-up, login and the saved session (kept in the app's private storage).
class Session {
  Session._();

  static final ValueNotifier<SessionUser?> user = ValueNotifier<SessionUser?>(null);
  static String? token;

  /// My numbers (null until loaded).
  static final ValueNotifier<MyStats?> stats = ValueNotifier<MyStats?>(null);

  /// Set when the server says this account was disabled or the login expired.
  static final ValueNotifier<String?> signedOutReason = ValueNotifier<String?>(null);

  static bool get canViewHistory => user.value?.canViewHistory ?? false;

  /// Admins see everyone's surveys and product reports; executives only their own surveys.
  static bool get isAdmin => user.value?.role == 'admin';

  static Options get _auth => Options(headers: {if (token != null) 'Authorization': 'Bearer $token'});

  static bool get signedIn => token != null && user.value != null;

  static final Dio _dio = Dio(BaseOptions(
    baseUrl: ApiConfig.baseUrl,
    connectTimeout: const Duration(seconds: 12),
    receiveTimeout: const Duration(seconds: 30),
    headers: {'Accept': 'application/json'},
  ));

  static Future<File> _file() async {
    final base = await getApplicationDocumentsDirectory();
    return File('${base.path}${Platform.pathSeparator}session.json');
  }

  static Future<void> load() async {
    try {
      final f = await _file();
      if (await f.exists()) {
        final j = jsonDecode(await f.readAsString());
        if (j is Map && j['token'] is String && j['user'] is Map) {
          token = j['token'] as String;
          user.value = SessionUser.fromJson(Map<String, dynamic>.from(j['user'] as Map));
        }
      }
    } catch (e) {
      debugPrint('Session: could not load ($e)');
    }
  }

  static Future<void> _save() async {
    final f = await _file();
    if (token == null || user.value == null) {
      if (await f.exists()) await f.delete();
      return;
    }
    await f.writeAsString(jsonEncode({'token': token, 'user': user.value!.toJson()}));
  }

  static Future<void> _signedIn(Map<String, dynamic> data) async {
    token = '${data['token']}';
    user.value = SessionUser.fromJson(Map<String, dynamic>.from(data['user'] as Map));
    await _save();
    // The executive's name fills every survey automatically.
    AppProfile.executiveName.value = user.value!.name;
    await AppProfile.save();
  }

  /// Throws a readable message (String) on failure.
  static Future<void> login(String identifier, String password) async {
    try {
      final r = await _dio.post('/auth/login', data: {'identifier': identifier.trim(), 'password': password});
      await _signedIn(Map<String, dynamic>.from(r.data as Map));
    } catch (e) {
      throw _message(e);
    }
  }

  static Future<void> signup(String name, String identifier, String password) async {
    try {
      final r = await _dio.post('/auth/signup', data: {'name': name.trim(), 'identifier': identifier.trim(), 'password': password});
      await _signedIn(Map<String, dynamic>.from(r.data as Map));
    } catch (e) {
      throw _message(e);
    }
  }

  /// Latest permissions from the server (an admin may have changed them).
  static Future<void> refreshMe() async {
    if (token == null) return;
    try {
      final r = await _dio.get('/auth/me', options: _auth);
      final data = Map<String, dynamic>.from(r.data as Map);
      user.value = SessionUser.fromJson(Map<String, dynamic>.from(data['user'] as Map));
      await _save();
    } on DioException catch (e) {
      if (e.response?.statusCode == 401) {
        signedOutReason.value = 'Your session ended or your account was disabled. Please log in again.';
        await logout();
      }
      // Offline: keep the saved permissions.
    } catch (_) {}
  }

  /// My own survey numbers; "today" uses the phone's time zone.
  static Future<void> refreshStats() async {
    if (token == null) return;
    try {
      final r = await _dio.get('/auth/me/stats',
          queryParameters: {'tz_offset': DateTime.now().timeZoneOffset.inMinutes}, options: _auth);
      stats.value = MyStats.fromJson(Map<String, dynamic>.from(r.data as Map));
    } catch (_) {
      // Offline: keep the last numbers.
    }
  }

  static Future<void> logout() async {
    Notifications.clear(); // the next account on this phone starts with an empty bell
    token = null;
    user.value = null;
    stats.value = null;
    AppProfile.permissionsDone = false;
    await AppProfile.save();
    await _save();
  }

  static String _message(Object e) {
    if (e is DioException && e.response?.statusCode == 404) {
      return 'The server doesn\'t support accounts yet. Update the backend (auth router).';
    }
    if (isConnectionProblem(e)) {
      return "Can't reach the server (${ApiConfig.baseUrl}). Check the connection and try again.";
    }
    return apiErrorText(e);
  }
}