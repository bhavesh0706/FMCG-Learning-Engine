import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:dio/dio.dart';
import 'package:flutter/foundation.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:path_provider/path_provider.dart';

import 'api_service.dart';
import 'notification_service.dart';
import 'offline_auth.dart';
import 'recording_vault.dart';
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

  /// Signed in on this phone WITHOUT the server (offline sign-in of a survey person,
  /// checked against the verifier saved at their last online sign-in). No server token:
  /// nothing is uploaded until [restoreOnline] succeeds.
  static final ValueNotifier<bool> offlineMode = ValueNotifier<bool>(false);

  /// The server needs the password again before uploads can continue (an offline
  /// session after the app restarted, or the password was changed on the server).
  static final ValueNotifier<bool> needsOnlineSignIn = ValueNotifier<bool>(false);

  /// Kept in memory only (never stored) during an offline session, so the app can sign
  /// in to the server by itself as soon as the connection is back.
  static String? _pendingIdentifier;
  static String? _pendingPassword;

  /// Normalised identifier of the offline session (saved in session.json, not secret).
  static String? _offlineIdentifier;

  /// A server token is available: uploads and online-only features can work.
  static bool get hasServerSession => token != null && user.value != null;

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

  static bool get signedIn => user.value != null && (token != null || offlineMode.value);

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
  // The device's refresh token (rotated at every renewal) and the number it belongs to: the phone
  // renews its session in the background without the password. Never the password itself.
  static const String _refreshKey = 'fmcg.session.refresh';
  static const String _refreshOwnerKey = 'fmcg.session.refresh.owner';

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
      } else if (stored == null && saved != null && saved['offline'] == true && saved['user'] is Map) {
        // An offline session (no server token): reopened only while offline sign-in is
        // still allowed for this account; otherwise the person signs in again.
        final restored = SessionUser.fromJson(Map<String, dynamic>.from(saved['user'] as Map));
        final identifier = '${saved['identifier'] ?? ''}';
        if (restored.role == kRoleExecutive &&
            identifier.isNotEmpty &&
            await OfflineAuth.stillValid(identifier, restored.id)) {
          _offlineIdentifier = identifier;
          offlineMode.value = true;
          // With a refresh token the session renews by itself once online; without one (older
          // sign-ins) the password is asked to upload.
          needsOnlineSignIn.value = (await _readRefresh()) == null;
          user.value = restored;
        } else if (await f.exists()) {
          await f.delete();
        }
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
    if (user.value != null && token == null && offlineMode.value) {
      await _deleteToken();
      await f.writeAsString(jsonEncode({'user': user.value!.toJson(), 'offline': true, 'identifier': _offlineIdentifier}));
      return;
    }
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
    final refresh = data['refresh_token'];
    if (refresh is String && refresh.isNotEmpty) await _secure.write(key: _refreshKey, value: refresh);
    // The public key recordings are encrypted with, kept for offline surveys.
    unawaited(RecordingVault.refresh((path) => SurveyApi().rawGet(path)));
    offlineMode.value = false;
    needsOnlineSignIn.value = false;
    _offlineIdentifier = null;
    _pendingIdentifier = null;
    _pendingPassword = null;
    await _save();
    // The executive's name fills every survey automatically.
    if (!isClient) {
      AppProfile.executiveName.value = user.value!.name;
      await AppProfile.save();
    }
  }

  /// Throws a readable message (String) on failure.
  ///
  /// Without a connection, a survey person who signed in online on this phone before
  /// is signed in OFFLINE ([offlineMode]); everyone else needs the internet.
  static Future<void> login(String identifier, String password) async {
    if (password.isEmpty) return _deviceLogin(identifier);
    Map<String, dynamic> data;
    try {
      final r = await _dio.post('/auth/login',
          data: {'identifier': identifier.trim(), 'password': password, 'remember_device': true, 'device': 'FMCG mobile app'});
      data = Map<String, dynamic>.from(r.data as Map);
    } catch (e) {
      if (isConnectionProblem(e)) return _offlineLogin(identifier, password);
      if (e is DioException && e.response?.statusCode == 403) {
        await OfflineAuth.forget(identifier); // account disabled: no offline access either
      }
      throw _message(e);
    }
    await _signedIn(data); // throws a readable message for an unknown role
    await _secure.write(key: _refreshOwnerKey, value: normalizeIdentifier(identifier));
    await _rememberForOffline(identifier, password);
  }

  /// Sign-in with the mobile number only: allowed when this phone holds a valid session (refresh
  /// token) for that number and its saved account. Works offline; online, the session renews at once.
  static Future<void> _deviceLogin(String identifier) async {
    final owner = await _secure.read(key: _refreshOwnerKey);
    final hasRefresh = (await _readRefresh()) != null;
    final saved = await OfflineAuth.savedUser(identifier);
    if (!hasRefresh || owner != normalizeIdentifier(identifier) || saved == null) throw 'Enter your password.';
    final savedUser = SessionUser.fromJson(saved);
    if (savedUser.role != kRoleExecutive) throw 'Enter your password.';
    token = null;
    _offlineIdentifier = normalizeIdentifier(identifier);
    _pendingIdentifier = identifier.trim();
    _pendingPassword = null;
    offlineMode.value = true;
    needsOnlineSignIn.value = false;
    user.value = savedUser;
    await _save();
    AppProfile.executiveName.value = savedUser.name;
    await AppProfile.save();
    unawaited(refreshSession()); // online: renews now; offline: later (restoreOnline)
  }

  static Future<String?> _readRefresh() async {
    try {
      final v = await _secure.read(key: _refreshKey);
      return v == null || v.isEmpty ? null : v;
    } catch (_) {
      return null;
    }
  }

  static Future<void> _deleteRefresh() async {
    try {
      await _secure.delete(key: _refreshKey);
      await _secure.delete(key: _refreshOwnerKey);
    } catch (_) {}
  }

  static Future<bool>? _refreshing;

  /// Renews the session with the refresh token (no password): a new access token and a new refresh
  /// token. True when signed in online again. Offline: false (try again later). Ended on the server
  /// (logout elsewhere, password changed, disabled, expired): the refresh token is dropped, offline
  /// sign-in ends, and the password is asked (surveys on the phone are kept).
  static Future<bool> refreshSession() => _refreshing ??= _doRefresh().whenComplete(() => _refreshing = null);

  static Future<bool> _doRefresh() async {
    final rt = await _readRefresh();
    if (rt == null) return false;
    final expectedId = user.value?.id;
    try {
      final r = await _dio.post('/auth/refresh', data: {'refresh_token': rt});
      final data = Map<String, dynamic>.from(r.data as Map);
      final serverUser = data['user'] is Map ? data['user'] as Map : const {};
      if (expectedId != null && (serverUser['id'] as num?)?.toInt() != expectedId) {
        await _deleteRefresh();
        return false;
      }
      await _signedIn(data);
      final u = user.value;
      for (final id in [u?.mobile, u?.email]) {
        if (id != null && id.isNotEmpty) await OfflineAuth.touch(id);
      }
      return true;
    } catch (e) {
      if (isConnectionProblem(e)) return false;
      final code = e is DioException ? e.response?.statusCode : null;
      if (code == 401 || code == 403) {
        await _deleteRefresh();
        final u = user.value;
        for (final id in [u?.mobile, u?.email]) {
          if (id != null && id.isNotEmpty) await OfflineAuth.forget(id);
        }
        signedOutReason.value = code == 403
            ? 'This account has been disabled. Contact your admin.'
            : 'Your session ended. Please log in again to upload your surveys.';
        needsOnlineSignIn.value = true;
      }
      return false;
    }
  }

  /// For every API client: a request refused with 401 renews the session once (refresh token) and
  /// is sent again, so the person never sees an expired token.
  /// Sign-in calls themselves are never renewed and retried (a 401 there is the answer); the
  /// person's own data under /auth/me/... is.
  static bool _isSignInCall(String path) => RegExp(
          r'/auth/(login|refresh|logout|logout-all|signup|setup|change-password|password-reset)(/|$|\?)')
      .hasMatch(path);

  static Interceptor refreshOnUnauthorized() => InterceptorsWrapper(onError: (e, handler) async {
        final opts = e.requestOptions;
        if (e.response?.statusCode == 401 && opts.extra['fmcg_retried'] != true && !_isSignInCall(opts.path) &&
            await refreshSession()) {
          opts.extra['fmcg_retried'] = true;
          opts.headers['Authorization'] = 'Bearer $token';
          try {
            return handler.resolve(await Dio().fetch(opts));
          } on DioException catch (again) {
            return handler.next(again);
          }
        }
        handler.next(e);
      });

  /// Survey persons keep a password verifier for offline sign-in (see offline_auth.dart).
  static Future<void> _rememberForOffline(String identifier, String password) async {
    final u = user.value;
    if (u == null) return;
    try {
      if (u.role == kRoleExecutive && !u.mustChangePassword) {
        await OfflineAuth.remember(identifier: identifier, password: password, user: u.toJson());
      } else {
        await OfflineAuth.forget(identifier);
      }
    } catch (e) {
      debugPrint('Session: offline sign-in not saved ($e)');
    }
  }

  static Future<void> _offlineLogin(String identifier, String password) async {
    final Map<String, dynamic> saved;
    try {
      saved = await OfflineAuth.verify(identifier, password);
    } on OfflineLoginError catch (e) {
      throw e.message;
    }
    final offlineUser = SessionUser.fromJson(saved);
    if (offlineUser.role != kRoleExecutive) throw OfflineLoginError(OfflineLoginProblem.notSurveyor).message;
    token = null;
    _offlineIdentifier = normalizeIdentifier(identifier);
    _pendingIdentifier = identifier.trim();
    _pendingPassword = password;
    offlineMode.value = true;
    needsOnlineSignIn.value = false;
    user.value = offlineUser;
    await _save();
    AppProfile.executiveName.value = offlineUser.name;
    await AppProfile.save();
  }

  /// Back online during an offline session: sign in to the server with the password
  /// typed at the offline sign-in (kept in memory only). Returns true when uploads can
  /// run. Without that password (the app was restarted) [needsOnlineSignIn] asks for it.
  static Future<bool> restoreOnline() async {
    if (!offlineMode.value || user.value == null) return hasServerSession;
    if (await refreshSession()) return true; // the refresh token: no password needed
    if (!offlineMode.value) return hasServerSession;
    final identifier = _pendingIdentifier;
    final password = _pendingPassword;
    if (identifier == null || password == null) {
      // No refresh token (older sign-in) and no password in memory: ask for it, unless the phone
      // is simply still offline (then the refresh above returned false without ending anything).
      if ((await _readRefresh()) == null) needsOnlineSignIn.value = true;
      return false;
    }
    final offlineUserId = user.value!.id;
    try {
      final r = await _dio.post('/auth/login', data: {'identifier': identifier, 'password': password});
      final data = Map<String, dynamic>.from(r.data as Map);
      final serverUser = data['user'] is Map ? data['user'] as Map : const {};
      if ((serverUser['id'] as num?)?.toInt() != offlineUserId) {
        throw StateError('A different account answered for this sign-in.');
      }
      await _signedIn(data);
      await _rememberForOffline(identifier, password);
      return true;
    } catch (e) {
      if (isConnectionProblem(e)) return false; // still no server: try again later
      // The password was changed on the server, or the account was disabled: the saved
      // verifier is out of date. Keep the surveys on the phone; ask for the password.
      await OfflineAuth.forget(identifier);
      _pendingPassword = null;
      needsOnlineSignIn.value = true;
      if (e is DioException && e.response?.statusCode == 403) {
        signedOutReason.value = 'This account has been disabled. Contact your admin.';
      }
      return false;
    }
  }

  /// The person typed their password again (needsOnlineSignIn). Throws a readable message.
  static Future<void> reauthenticate(String password) async {
    final u = user.value;
    final identifier = _pendingIdentifier ?? _offlineIdentifier ?? u?.mobile ?? u?.email;
    if (u == null || identifier == null) throw 'Sign in again.';
    final r = await _tryLogin(identifier, password);
    final serverUser = r['user'] is Map ? r['user'] as Map : const {};
    if ((serverUser['id'] as num?)?.toInt() != u.id) {
      throw 'These details belong to a different account. Sign out first to switch accounts.';
    }
    await _signedIn(r);
    await _rememberForOffline(identifier, password);
  }

  static Future<Map<String, dynamic>> _tryLogin(String identifier, String password) async {
    try {
      final r = await _dio.post('/auth/login', data: {'identifier': identifier.trim(), 'password': password});
      return Map<String, dynamic>.from(r.data as Map);
    } catch (e) {
      throw _message(e);
    }
  }

  // ---- Forgot password (Admin and Super Admin; survey persons are reset by their admin)

  /// "simple" (outside production: mobile number or email + new password) or
  /// "email" (a 6-digit code is emailed first). The server decides.
  static Future<String> passwordResetMode() async {
    try {
      final r = await _dio.get('/auth/setup-status');
      final mode = r.data is Map ? (r.data as Map)['password_reset'] : null;
      return mode == 'simple' ? 'simple' : 'email';
    } catch (e) {
      throw _message(e);
    }
  }

  static Future<String> _resetCall(String path, Map<String, dynamic> body) async {
    try {
      final r = await _dio.post(path, data: body);
      final detail = r.data is Map ? (r.data as Map)['detail'] : null;
      return detail is String ? detail : 'Done.';
    } catch (e) {
      if (e is String) rethrow;
      throw _message(e);
    }
  }

  static Future<String> resetPasswordSimple(String identifier, String newPassword) =>
      _resetCall('/auth/password-reset/simple', {'identifier': identifier.trim(), 'new_password': newPassword});

  static Future<String> requestPasswordCode(String email) =>
      _resetCall('/auth/password-reset/request', {'email': email.trim()});

  static Future<String> resetPasswordWithCode(String email, String code, String newPassword) =>
      _resetCall('/auth/password-reset/confirm', {'email': email.trim(), 'code': code.trim(), 'new_password': newPassword});

  static Future<void> signup(String name, String identifier, String password) async {
    try {
      final r = await _dio.post('/auth/signup', data: {'name': name.trim(), 'identifier': identifier.trim(), 'password': password});
      await _signedIn(Map<String, dynamic>.from(r.data as Map));
      await _rememberForOffline(identifier, password);
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
      final u = user.value;
      final identifier = u?.mobile ?? u?.email;
      if (identifier != null) await _rememberForOffline(identifier, next);
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
      if (e.response?.statusCode == 401 && await refreshSession()) {
        return; // renewed with the refresh token (no password)
      }
      if (e.response?.statusCode == 401) {
        // Ended on the server (signed out everywhere, disabled, expired): offline
        // sign-in must not outlive it.
        final u = user.value;
        for (final id in [u?.mobile, u?.email]) {
          if (id != null && id.isNotEmpty) await OfflineAuth.forget(id);
        }
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
    final oldRefresh = await _readRefresh();
    await _deleteRefresh();
    if (endOnServer && (old != null || oldRefresh != null)) {
      // Not awaited: signing out never waits for the network (works offline too).
      Future(() async {
        try {
          await _dio.post('/auth/logout',
              data: {if (oldRefresh != null) 'refresh_token': oldRefresh},
              options: Options(headers: {if (old != null) 'Authorization': 'Bearer $old'}));
        } catch (_) {
          // Offline or server unreachable: the token still expires on its own.
        }
      });
    }
    Notifications.clear(); // the next account on this phone starts with an empty bell
    token = null;
    offlineMode.value = false;
    needsOnlineSignIn.value = false;
    _offlineIdentifier = null;
    _pendingIdentifier = null;
    _pendingPassword = null;
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