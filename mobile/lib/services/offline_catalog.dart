import 'dart:convert';

import 'package:flutter/foundation.dart';
import 'package:sqflite_sqlcipher/sqflite.dart';

import 'connectivity_service.dart';
import 'local_db.dart';
import 'lov.dart';
import 'recording_vault.dart';
import 'session.dart';
import 'survey_api.dart';

/*
  Survey data downloaded for offline use (survey persons).

  While online, the products this account may survey, their survey questions, the
  master lists (respondent type, occupation, class) and the person's action plans
  are downloaded into the encrypted local database (LocalDb.offline_cache), per
  account. The survey screens read through this class:

    online  -> from the server; the answer also refreshes the offline copy
    offline -> from the offline copy; nothing downloaded -> a clear message

  Only what the server returned for this account is stored, so offline the person
  sees exactly the assignments they were authorised for at the last download.
*/

class OfflineDataMissing implements Exception {
  const OfflineDataMissing();

  @override
  String toString() =>
      "This survey data isn't on the phone yet. Connect to the internet once and tap "
      "'Download for offline use' (Sync screen).";
}

class OfflineDownloadResult {
  const OfflineDownloadResult({required this.products, required this.questionSets});

  final int products;
  final int questionSets;
}

class OfflineCatalog {
  OfflineCatalog._();

  static final ValueNotifier<DateTime?> downloadedAt = ValueNotifier<DateTime?>(null);
  static final ValueNotifier<bool> downloading = ValueNotifier<bool>(false);

  static const _metaKey = 'meta:downloaded_at';

  @visibleForTesting
  static SurveyApi api = SurveyApi();

  static int get _owner => Session.user.value?.id ?? 0;

  /// The server is worth trying: the phone has a network and the session has a server token.
  static bool get _tryServer => ConnectivityService.online.value && Session.hasServerSession;

  static Future<void> _put(DatabaseExecutor db, int owner, String key, Object? data) => db.insert('offline_cache', {
    'owner_user_id': owner,
    'cache_key': key,
    'data': jsonEncode(data),
    'updated_at': DateTime.now().toUtc().toIso8601String(),
  }, conflictAlgorithm: ConflictAlgorithm.replace);

  static Future<dynamic> _get(int owner, String key) async {
    final db = await LocalDb.instance;
    final rows = await db.query(
      'offline_cache',
      columns: ['data'],
      where: 'owner_user_id = ? AND cache_key = ?',
      whereArgs: [owner, key],
      limit: 1,
    );
    if (rows.isEmpty) return null;
    return jsonDecode(rows.first['data'] as String);
  }

  /// Server first (refreshing the offline copy), else the offline copy.
  static Future<dynamic> _read(String key, String path) async {
    final owner = _owner;
    if (_tryServer) {
      try {
        final data = await api.rawGet(path);
        if (Session.isExecutive) await _put(await LocalDb.instance, owner, key, data);
        return data;
      } catch (e) {
        if (!isConnectionProblem(e)) rethrow;
      }
    }
    final cached = await _get(owner, key);
    if (cached == null) throw const OfflineDataMissing();
    return cached;
  }

  static Future<List<CatalogCategory>> categories() async =>
      SurveyApi.parseCategories(await _read('categories', '/categories'));

  static Future<List<CatalogProduct>> products() async => SurveyApi.parseProducts(await _read('products', '/products'));

  static Future<List<SurveyQuestionItem>> questions(int productId) async =>
      SurveyApi.parseQuestions(await _read('questions:$productId', '/products/$productId/questions'));

  /// Master lists ({respondent_type: [...], occupation: [...], ...}), as GET /masters.
  static Future<Map<String, dynamic>> masters() async {
    final data = await _read('masters', '/masters');
    return data is Map ? Map<String, dynamic>.from(data) : <String, dynamic>{};
  }

  /// Country / state / city lists (GET /lov), for the respondent's place.
  static Future<Lov> lov() async => Lov.parse(await _read('lov', '/lov'));

  /// This person's action plans (cities per product), as GET /my/action-plans.
  static Future<List<Map<String, dynamic>>> myActionPlans() async {
    final data = await _read('my_action_plans', '/my/action-plans');
    final plans = data is Map ? data['plans'] : null;
    return [
      for (final p in (plans is List ? plans : const []))
        if (p is Map) Map<String, dynamic>.from(p),
    ];
  }

  static Future<void> loadMeta() async {
    try {
      final value = await _get(_owner, _metaKey);
      downloadedAt.value = value == null ? null : DateTime.tryParse('$value')?.toLocal();
    } catch (e) {
      debugPrint('OfflineCatalog: could not read ($e)');
    }
  }

  /// Downloads everything a survey needs, for every product this account may survey.
  /// Replaces the previous copy in one transaction (products no longer assigned disappear).
  static Future<OfflineDownloadResult> downloadAll() async {
    if (!Session.isExecutive) throw StateError('Offline mode is for survey persons.');
    if (!Session.hasServerSession) throw StateError('Sign in with internet to download survey data.');
    downloading.value = true;
    try {
      final owner = _owner;
      final categoriesData = await api.rawGet('/categories');
      final productsData = await api.rawGet('/products');
      final ready = SurveyApi.parseProducts(productsData).where((p) => p.surveyReady).toList();
      final questions = <int, dynamic>{};
      for (final p in ready) {
        questions[p.id] = await api.rawGet('/products/${p.id}/questions');
      }
      // Optional on older servers: a failure here doesn't stop the download.
      dynamic masters;
      dynamic plans;
      dynamic lov;
      try {
        masters = await api.rawGet('/masters');
      } catch (e) {
        if (isConnectionProblem(e)) rethrow;
      }
      try {
        plans = await api.rawGet('/my/action-plans');
      } catch (e) {
        if (isConnectionProblem(e)) rethrow;
      }
      try {
        lov = await api.rawGet('/lov');
      } catch (e) {
        if (isConnectionProblem(e)) rethrow;
      }
      // The key recordings are encrypted with on this phone, offline too (services/recording_vault.dart)
      await RecordingVault.refresh(api.rawGet);

      final stamp = DateTime.now().toUtc().toIso8601String();
      final db = await LocalDb.instance;
      await db.transaction((txn) async {
        await txn.delete('offline_cache', where: 'owner_user_id = ?', whereArgs: [owner]);
        await _put(txn, owner, 'categories', categoriesData);
        await _put(txn, owner, 'products', productsData);
        for (final entry in questions.entries) {
          await _put(txn, owner, 'questions:${entry.key}', entry.value);
        }
        if (masters != null) await _put(txn, owner, 'masters', masters);
        if (plans != null) await _put(txn, owner, 'my_action_plans', plans);
        if (lov != null) await _put(txn, owner, 'lov', lov);
        await _put(txn, owner, _metaKey, stamp);
      });
      downloadedAt.value = DateTime.parse(stamp).toLocal();
      return OfflineDownloadResult(products: ready.length, questionSets: questions.length);
    } finally {
      downloading.value = false;
    }
  }

  /// Best effort after sign-in / when back online: keeps the offline copy fresh.
  static Future<void> refreshInBackground() async {
    if (!Session.isExecutive || !_tryServer || downloading.value) return;
    try {
      await downloadAll();
    } catch (e) {
      debugPrint('OfflineCatalog: background download skipped ($e)');
    }
  }
}
