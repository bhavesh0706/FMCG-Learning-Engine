import 'dart:convert';
import 'dart:io';
import 'dart:math';

import 'package:flutter/foundation.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:path_provider/path_provider.dart';
import 'package:sqflite_sqlcipher/sqflite.dart';

/*
  The phone's own database for offline mode (survey persons only).

  SQLite encrypted with SQLCipher (AES-256). The database key is 32 random bytes
  created on first use and kept in the phone's secure store (iOS Keychain /
  Android Keystore), never in a file. The database file lives in the app's
  private support folder (not visible in the Files app, not shared).

  Tables
    submissions    one row per survey collected on this phone (customer details,
                   answers, recording path, upload progress). `data` is the
                   survey as JSON; the other columns are for filtering.
    offline_cache  survey data downloaded for offline use, per account
                   (products, questions, master lists, action plans).
*/

typedef DbOpener = Future<Database> Function(
  String path, {
  required String password,
  required int version,
  required OnDatabaseCreateFn onCreate,
  required OnDatabaseVersionChangeFn onUpgrade,
});

class LocalDb {
  LocalDb._();

  static const int schemaVersion = 1;
  static const String _keyName = 'fmcg.localdb.key';
  static const FlutterSecureStorage _secure = FlutterSecureStorage();

  /// Replaced in tests (an unencrypted in-memory database on the computer).
  @visibleForTesting
  static DbOpener opener = _openEncrypted;

  /// Replaced in tests.
  @visibleForTesting
  static Future<String> Function() pathProvider = _defaultPath;

  static Database? _db;
  static Future<Database>? _opening;

  static Future<Database> get instance {
    final db = _db;
    if (db != null) return Future.value(db);
    return _opening ??= _open().whenComplete(() => _opening = null);
  }

  static Future<String> _defaultPath() async {
    final dir = await getApplicationSupportDirectory();
    if (!await dir.exists()) await dir.create(recursive: true);
    return '${dir.path}${Platform.pathSeparator}fmcg_offline.db';
  }

  static Future<Database> _openEncrypted(
    String path, {
    required String password,
    required int version,
    required OnDatabaseCreateFn onCreate,
    required OnDatabaseVersionChangeFn onUpgrade,
  }) => openDatabase(path, password: password, version: version, onCreate: onCreate, onUpgrade: onUpgrade);

  static Future<String> _databaseKey(String path) async {
    var key = await _secure.read(key: _keyName);
    if (key != null && key.isNotEmpty) return key;
    final file = File(path);
    if (await file.exists()) {
      // The key is gone (e.g. the Keychain was reset) but the file is still here. It can't be
      // read without its key; keep it aside rather than delete it, and start a new database.
      final aside = '$path.unreadable-${DateTime.now().millisecondsSinceEpoch}';
      await file.rename(aside);
      debugPrint('LocalDb: database key missing; the old database was kept as $aside');
    }
    final random = Random.secure();
    key = base64UrlEncode(List<int>.generate(32, (_) => random.nextInt(256)));
    await _secure.write(key: _keyName, value: key);
    return key;
  }

  static Future<Database> _open() async {
    final path = await pathProvider();
    final db = await opener(
      path,
      password: await _databaseKey(path),
      version: schemaVersion,
      onCreate: (db, version) async => _create(db),
      onUpgrade: (db, from, to) async {
        // Future schema changes go here (from < N -> apply step N).
      },
    );
    _db = db;
    return db;
  }

  static Future<void> _create(Database db) async {
    await db.execute('''
      CREATE TABLE submissions (
        id TEXT PRIMARY KEY,
        owner_user_id INTEGER,
        status TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        data TEXT NOT NULL
      )''');
    await db.execute('CREATE INDEX ix_submissions_owner ON submissions (owner_user_id, status)');
    await db.execute('''
      CREATE TABLE offline_cache (
        owner_user_id INTEGER NOT NULL,
        cache_key TEXT NOT NULL,
        data TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        PRIMARY KEY (owner_user_id, cache_key)
      )''');
  }

  /// Closes the database (tests; the app keeps it open).
  @visibleForTesting
  static Future<void> close() async {
    await _opening;
    final db = _db;
    _db = null;
    _opening = null;
    if (db != null) await db.close();
  }
}
