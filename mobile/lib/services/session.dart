import 'dart:convert';
import 'dart:io';

import 'package:dio/dio.dart';
import 'package:flutter/foundation.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:path_provider/path_provider.dart';

import 'api_service.dart';
import 'notification_service.dart';
import 'survey_api.dart';
import 'survey_queue.dart';

/// The signed-in survey executive.
/// The three roles the server uses (users.role). The role always comes from the
/// server's user record; the app never guesses it or falls back to one.
const kRoleAdmin = 'admin';
const kRoleExecutive = 'executive';
const kRoleClient = 'client'; // the organization
const kKnownRoles = {kRoleAdmin, kRoleExecutive, kRoleClient};

class SessionUser {
  const SessionUser({
    required this.id,
    required this.name,
    required this.role,
    this.mobile,
    this.email,
    this.canViewHistory = false,
    this.mustChangePassword = false,
  });

  final int id;
  final String name;
  final String? mobile;
  final String? email;

  /// As sent by the server ("admin", "executive", "client"); "" when missing.
  final String role;

  /// The server requires a new password before anything else works
  /// (the organization's first sign-in, or after a password reset).
  final bool mustChangePassword;

  bool get hasKnownRole => kKnownRoles.contains(role);

  /// Set by an admin: may this executive see feedback history and reports?
  final bool canViewHistory;

  String get contact => mobile ?? email ?? '';

  Map<String, dynamic> toJson() => {
        'id': id,
        'name': name,
        'mobile': mobile,
        'email': email,
        'role': role,
        'can_view_history': canViewHistory,
        'must_change_password': mustChangePassword,
      };

  factory SessionUser.fromJson(Map<String, dynamic> j) => SessionUser(
        id: (j['id'] as num?)?.toInt() ?? 0,
        name: '${j['name'] ?? ''}',
        mobile: j['mobile'] as String?,
        email: j['email'] as String?,
        // No default: a missing role stays "" and is refused (see Session._signedIn).
        role: (j['role'] ?? '').toString().trim().toLowerCase(),
        canViewHistory: j['can_view_history'] == true,
        mustChangePassword: j['must_change_password'] == true,
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
  static bool get isAdmin => user.value?.role == kRoleAdmin;

  /// The organization's login (role "client"): uses OrgShell instead of the survey app.
  static bool get isClient => user.value?.role == kRoleClient;

  static bool get isExecutive => user.value?.role == kRoleExecutive;

  /// The server requires a new password before the account can be used.
  static bool get mustChangePassword => user.value?.mustChangePassword == true;

  /// How the account's role is shown (Settings, profile).
  static String get roleLabel => switch (user.value?.role) {
        kRoleAdmin => 'Admin',
        kRoleClient => 'Super Admin',
        kRoleExecutive => 'Survey executive',
        _ => 'Unknown role',
      };

  static const _unknownRole =
      "This account's role isn't recognised by the app. Ask your admin to check the account.";

  static Options get _auth => Options(headers: {if (token != null) 'Authorization': 'Bearer $token'});

  static bool get signedIn => token != null && user.value != null;

  static final Dio _dio = Dio(BaseOptions(
    baseUrl: ApiConfig.baseUrl,
    connectTimeout: const Duration(seconds: 12),
    receiveTimeout: const Duration(seconds: 30),
    headers: {'Accept': 'application/json'},
  ));

  // The sign-in token lives in the phone's secure store (iOS Keychain, Android
  // Keystore), never in a plain file: plain files in the app folder are included
  // in device backups (MASVS-STORAGE). session.json keeps only the account's
  // name, role and permissions, which are not secret.
  static const FlutterSecureStorage _secure = FlutterSecureStorage();
  static const String _tokenKey = 'fmcg.session.token';

  static Future<File> _file() async {
    final base = await getApplicationDocumentsDirectory();
    return File('${base.path}${Platform.pathSeparator}session.json');
  }

  static Future<void> load() async {
    try {
      final f = await _file();
      Map? saved;
      if (await f.exists()) {
        final j = jsonDecode(await f.readAsString());
        if (j is Map) saved = j;
      }
      var stored = await _readToken();
      // One-time move from older app versions, which kept the token in session.json.
      final legacy = saved?['token'];
      if (legacy is String && legacy.isNotEmpty) {
        stored ??= legacy;
        await _writeToken(stored);
      }
      if (stored != null && saved != null && saved['user'] is Map) {
        final restored = SessionUser.fromJson(Map<String, dynamic>.from(saved['user'] as Map));
        if (!restored.hasKnownRole) {
          // Saved by an older version without the role (or a role this version doesn't
          // know): never guess it. Sign out; the next sign-in gets the role from the server.
          await _deleteToken();
          if (await f.exists()) await f.delete();
          return;
        }
        token = stored;
        user.value = restored;
        if (legacy != null) await _save(); // rewrite session.json without the token
      } else if (stored != null && (saved == null || saved['user'] is! Map)) {
        // A token without its account (e.g. app reinstalled: the Keychain survives): sign out cleanly.
        await _deleteToken();
      }
    } catch (e) {
      debugPrint('Session: could not load ($e)');
    }
  }

  static Future<void> _save() async {
    final f = await _file();
    if (token == null || user.value == null) {
      if (await f.exists()) await f.delete();
      await _deleteToken();
      return;
    }
    await _writeToken(token!);
    await f.writeAsString(jsonEncode({'user': user.value!.toJson()}));
  }

  static Future<String?> _readToken() async {
    try {
      final v = await _secure.read(key: _tokenKey);
      return (v == null || v.isEmpty) ? null : v;
    } catch (e) {
      debugPrint('Session: secure storage unavailable ($e)');
      return null;
    }
  }

  static Future<void> _writeToken(String value) async {
    try {
      await _secure.write(key: _tokenKey, value: value);
    } catch (e) {
      debugPrint('Session: could not store the token securely ($e)');
    }
  }

  static Future<void> _deleteToken() async {
    try {
      await _secure.delete(key: _tokenKey);
    } catch (_) {}
  }

  static Future<void> _signedIn(Map<String, dynamic> data) async {
    // The role comes only from the server's user record. Survey persons and admins
    // get the survey app; the organization (role "client") gets OrgShell.
    final signedInUser = SessionUser.fromJson(Map<String, dynamic>.from(data['user'] as Map));
    if (!signedInUser.hasKnownRole) throw _unknownRole; // never fall back to a role
    token = '${data['token']}';
    user.value = signedInUser;
    await _save();
    // The executive's name fills every survey automatically.
    if (!isClient) {
      AppProfile.executiveName.value = user.value!.name;
      await AppProfile.save();
    }
  }

  /// Throws a readable message (String) on failure.
  static Future<void> login(String identifier, String password) async {
    try {
      final r = await _dio.post('/auth/login', data: {'identifier': identifier.trim(), 'password': password});
      await _signedIn(Map<String, dynamic>.from(r.data as Map));
    } catch (e) {
      if (e is String) rethrow; // already a readable message
      throw _message(e);
    }
  }

  static Future<void> signup(String name, String identifier, String password) async {
    try {
      final r = await _dio.post('/auth/signup', data: {'name': name.trim(), 'identifier': identifier.trim(), 'password': password});
      await _signedIn(Map<String, dynamic>.from(r.data as Map));
    } catch (e) {
      if (e is String) rethrow; // already a readable message
      throw _message(e);
    }
  }

  /// Choose a new password (required when [mustChangePassword]). The server signs
  /// out every other device and returns a new token for this one.
  /// Throws a readable message (String) on failure.
  static Future<void> changePassword(String current, String next) async {
    try {
      final r = await _dio.post('/auth/change-password',
          data: {'current_password': current, 'new_password': next}, options: _auth);
      await _signedIn(Map<String, dynamic>.from(r.data as Map));
    } catch (e) {
      if (e is String) rethrow;
      throw _message(e);
    }
  }

  /// Latest permissions from the server (an admin may have changed them).
  static Future<void> refreshMe() async {
    if (token == null) return;
    try {
      final r = await _dio.get('/auth/me', options: _auth);
      final data = Map<String, dynamic>.from(r.data as Map);
      final latest = SessionUser.fromJson(Map<String, dynamic>.from(data['user'] as Map));
      if (!latest.hasKnownRole) {
        await logout(endOnServer: false);
        return;
      }
      user.value = latest; // a role changed on the server takes effect here
      // The server sends a fresh token once the current one is a day old, so
      // people who use the app regularly stay signed in.
      final renewed = data['token'];
      if (renewed is String && renewed.isNotEmpty) token = renewed;
      await _save();
    } on DioException catch (e) {
      if (e.response?.statusCode == 401) {
        signedOutReason.value = 'Your session ended or your account was disabled. Please log in again.';
        await logout(endOnServer: false); // the server has already ended it
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

  /// Signs out on this phone. [endOnServer]: also end the token on the server, so it
  /// stops working at once (best effort: signing out works offline too).
  static Future<void> logout({bool endOnServer = true}) async {
    final old = token;
    if (endOnServer && old != null) {
      // Not awaited: signing out never waits for the network (works offline too).
      Future(() async {
        try {
          await _dio.post('/auth/logout', options: Options(headers: {'Authorization': 'Bearer $old'}));
        } catch (_) {
          // Offline or server unreachable: the token still expires on its own.
        }
      });
    }
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