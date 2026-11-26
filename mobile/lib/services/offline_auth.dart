import 'dart:convert';
import 'dart:math';

import 'package:cryptography/cryptography.dart';
import 'package:flutter/foundation.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';

/*
  Offline sign-in for survey persons.

  After a successful ONLINE sign-in by a survey person, the phone keeps a verifier
  for that account in its secure store (iOS Keychain / Android Keystore):

      PBKDF2-HMAC-SHA256(password, random 16-byte salt, 100,000 rounds) -> 32 bytes

  plus the account (id, name, role, permissions as the server sent them) and the
  time of that online sign-in. The password itself is never stored.

  Offline, the same identifier + password is checked against that verifier.
  It is refused when:
    - the account never signed in online on this phone (no verifier),
    - the account is not a survey person (admins and the organization stay online-only),
    - the last online sign-in is older than [maxAge] (default 14 days,
      --dart-define=OFFLINE_LOGIN_DAYS=n), or
    - [maxFailures] wrong passwords in a row (then only an online sign-in unlocks it).

  The verifier is removed when the server says the account was disabled or its
  sessions were ended.
*/

const kOfflineRole = 'executive';

enum OfflineLoginProblem { neverVerified, notSurveyor, wrongPassword, expired, locked }

class OfflineLoginError implements Exception {
  OfflineLoginError(this.problem);

  final OfflineLoginProblem problem;

  String get message => switch (problem) {
    OfflineLoginProblem.neverVerified => "No internet, and this account hasn't signed in on this phone before. Connect to the internet for the first sign-in.",
    OfflineLoginProblem.notSurveyor => 'No internet. Only survey persons can sign in offline; connect to the internet.',
    OfflineLoginProblem.wrongPassword => 'The mobile/email or password is incorrect.',
    OfflineLoginProblem.expired =>
      'Offline sign-in has expired for this account. Connect to the internet and sign in once to continue offline.',
    OfflineLoginProblem.locked =>
      'Too many wrong passwords. Offline sign-in is locked until you sign in with internet.',
  };

  @override
  String toString() => message;
}

/// Same rules as the server (auth.py _parse_identifier): email lower-case,
/// mobile = the last 10 digits (a leading +91 dropped).
String normalizeIdentifier(String identifier) {
  final text = identifier.trim();
  if (text.contains('@')) return text.toLowerCase();
  var digits = text.replaceAll(RegExp(r'\D'), '');
  if (digits.length == 12 && digits.startsWith('91')) digits = digits.substring(2);
  return digits;
}

class OfflineAuth {
  OfflineAuth._();

  static const FlutterSecureStorage _secure = FlutterSecureStorage();
  static const String _prefix = 'fmcg.offline.cred.';

  static const int _defaultDays = int.fromEnvironment('OFFLINE_LOGIN_DAYS', defaultValue: 14);
  static Duration maxAge = const Duration(days: _defaultDays);
  static const int maxFailures = 5;

  /// PBKDF2 rounds for new verifiers (lowered in tests).
  @visibleForTesting
  static int iterations = 100000;

  /// Replaced in tests.
  @visibleForTesting
  static DateTime Function() now = DateTime.now;

  static String _key(String identifier) => '$_prefix${base64UrlEncode(utf8.encode(normalizeIdentifier(identifier)))}';

  static Future<List<int>> _derive(String password, List<int> salt, int rounds) async {
    final kdf = Pbkdf2(macAlgorithm: Hmac.sha256(), iterations: rounds, bits: 256);
    final key = await kdf.deriveKeyFromPassword(password: password, nonce: salt);
    return key.extractBytes();
  }

  static bool _sameBytes(List<int> a, List<int> b) {
    if (a.length != b.length) return false;
    var diff = 0;
    for (var i = 0; i < a.length; i++) {
      diff |= a[i] ^ b[i];
    }
    return diff == 0; // constant time
  }

  static Future<Map<String, dynamic>?> _read(String identifier) async {
    try {
      final raw = await _secure.read(key: _key(identifier));
      if (raw == null || raw.isEmpty) return null;
      final data = jsonDecode(raw);
      return data is Map ? Map<String, dynamic>.from(data) : null;
    } catch (e) {
      debugPrint('OfflineAuth: could not read ($e)');
      return null;
    }
  }

  static Future<void> _write(String identifier, Map<String, dynamic> data) =>
      _secure.write(key: _key(identifier), value: jsonEncode(data));

  /// After a successful online sign-in. Survey persons get offline access; for any
  /// other role an old verifier for this identifier is removed.
  static Future<void> remember({
    required String identifier,
    required String password,
    required Map<String, dynamic> user,
  }) async {
    final role = '${user['role'] ?? ''}'.trim().toLowerCase();
    if (role != kOfflineRole) {
      await forget(identifier);
      return;
    }
    final random = Random.secure();
    final salt = List<int>.generate(16, (_) => random.nextInt(256));
    final hash = await _derive(password, salt, iterations);
    await _write(identifier, {
      'v': 1,
      'salt': base64Encode(salt),
      'hash': base64Encode(hash),
      'iterations': iterations,
      'user': user,
      'verified_at': now().toUtc().toIso8601String(),
      'failures': 0,
    });
  }

  /// Checks an offline sign-in; returns the account as saved at the last online sign-in.
  static Future<Map<String, dynamic>> verify(String identifier, String password) async {
    final data = await _read(identifier);
    if (data == null) throw OfflineLoginError(OfflineLoginProblem.neverVerified);
    final user = data['user'] is Map ? Map<String, dynamic>.from(data['user'] as Map) : <String, dynamic>{};
    if ('${user['role'] ?? ''}'.toLowerCase() != kOfflineRole) {
      throw OfflineLoginError(OfflineLoginProblem.notSurveyor);
    }
    final failures = (data['failures'] as num?)?.toInt() ?? 0;
    if (failures >= maxFailures) throw OfflineLoginError(OfflineLoginProblem.locked);
    final verifiedAt = DateTime.tryParse('${data['verified_at']}');
    if (verifiedAt == null || now().toUtc().difference(verifiedAt) > maxAge) {
      throw OfflineLoginError(OfflineLoginProblem.expired);
    }
    final salt = base64Decode('${data['salt']}');
    final expected = base64Decode('${data['hash']}');
    final rounds = (data['iterations'] as num?)?.toInt() ?? iterations;
    final actual = await _derive(password, salt, rounds);
    if (!_sameBytes(actual, expected)) {
      data['failures'] = failures + 1;
      await _write(identifier, data);
      throw OfflineLoginError(
        failures + 1 >= maxFailures ? OfflineLoginProblem.locked : OfflineLoginProblem.wrongPassword,
      );
    }
    if (failures != 0) {
      data['failures'] = 0;
      await _write(identifier, data);
    }
    return user;
  }

  /// Is offline sign-in still allowed for this account (used when the app restarts
  /// into an offline session)?
  static Future<bool> stillValid(String identifier, int userId) async {
    final data = await _read(identifier);
    if (data == null) return false;
    final user = data['user'] is Map ? data['user'] as Map : const {};
    if ((user['id'] as num?)?.toInt() != userId) return false;
    if (((data['failures'] as num?)?.toInt() ?? 0) >= maxFailures) return false;
    final verifiedAt = DateTime.tryParse('${data['verified_at']}');
    return verifiedAt != null && now().toUtc().difference(verifiedAt) <= maxAge;
  }

  /// The account saved for offline sign-in, without the password: used when this phone also holds
  /// a valid session (refresh token) for the same number. Null when expired or locked.
  static Future<Map<String, dynamic>?> savedUser(String identifier) async {
    final data = await _read(identifier);
    if (data == null || data['user'] is! Map) return null;
    if (((data['failures'] as num?)?.toInt() ?? 0) >= maxFailures) return null;
    final verifiedAt = DateTime.tryParse('${data['verified_at']}');
    if (verifiedAt == null || now().toUtc().difference(verifiedAt) > maxAge) return null;
    return Map<String, dynamic>.from(data['user'] as Map);
  }

  /// The server confirmed the session again (refresh): offline sign-in stays allowed for another period.
  static Future<void> touch(String identifier) async {
    final data = await _read(identifier);
    if (data == null) return;
    data['verified_at'] = now().toUtc().toIso8601String();
    await _write(identifier, data);
  }

  static Future<void> forget(String identifier) async {
    try {
      await _secure.delete(key: _key(identifier));
    } catch (_) {}
  }
}