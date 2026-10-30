import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:dio/dio.dart';
import 'package:flutter/foundation.dart';
import 'package:path_provider/path_provider.dart';

import 'session.dart';
import 'survey_api.dart';

/*
  Offline-first survey queue.

  Every submitted survey is saved on the phone first, then uploaded in the
  background: respondent -> survey -> answers -> submit -> recording (+ analysis).
  Each step is remembered, so a dropped connection resumes where it stopped.
  The executive never waits for the analysis; History shows each survey's status.

  Privacy: once a survey is uploaded, its personal details (full name, mobile,
  address) and the recording are deleted from the phone. Only a short label
  ("Priya S.") and the area are kept for History; the server has the rest.
*/

enum SurveyStatus { waiting, uploading, analyzing, analyzed, failed }

SurveyStatus _status(String s) =>
    SurveyStatus.values.firstWhere((v) => v.name == s, orElse: () => SurveyStatus.waiting);

class QueuedSurvey {
  QueuedSurvey({
    required this.id,
    required this.createdAt,
    required this.productId,
    required this.productName,
    required this.categoryName,
    required this.respondentLabel,
    required this.area,
    required this.respondent,
    required this.response,
    required this.answers,
    required this.mediaPath,
    required this.mediaName,
    required this.mediaKind,
    required this.durationSeconds,
    this.status = SurveyStatus.waiting,
    this.error,
    this.respondentId,
    this.responseId,
    this.submitted = false,
    this.jobId,
    this.feedbackId,
    this.rating,
    this.sentiment,
    this.summary,
    this.analyzedAt,
  });

  final String id;
  final DateTime createdAt;
  final int productId;
  final String productName;
  final String categoryName;
  final String respondentLabel; // "Priya S." - kept after upload
  final String area; // city / locality - kept after upload

  Map<String, dynamic>? respondent; // full details: deleted after upload
  final Map<String, dynamic> response;
  final List<Map<String, dynamic>> answers;

  String? mediaPath; // deleted after upload
  final String mediaName;
  final String mediaKind; // audio | video
  final double durationSeconds;

  SurveyStatus status;
  String? error;
  int? respondentId;
  int? responseId;
  bool submitted;
  int? jobId; // analysis job on the server, once the recording is uploaded
  int? feedbackId;
  double? rating;
  String? sentiment;
  String? summary;
  DateTime? analyzedAt;

  bool get needsUpload => status == SurveyStatus.waiting || status == SurveyStatus.failed;

  Map<String, dynamic> toJson() => {
        'id': id,
        'createdAt': createdAt.toIso8601String(),
        'productId': productId,
        'productName': productName,
        'categoryName': categoryName,
        'respondentLabel': respondentLabel,
        'area': area,
        'respondent': respondent,
        'response': response,
        'answers': answers,
        'mediaPath': mediaPath,
        'mediaName': mediaName,
        'mediaKind': mediaKind,
        'durationSeconds': durationSeconds,
        // An upload interrupted by closing the app resumes as "waiting".
        'status': (status == SurveyStatus.uploading || status == SurveyStatus.analyzing)
            ? SurveyStatus.waiting.name
            : status.name,
        'error': error,
        'respondentId': respondentId,
        'responseId': responseId,
        'submitted': submitted,
        'jobId': jobId,
        'feedbackId': feedbackId,
        'rating': rating,
        'sentiment': sentiment,
        'summary': summary,
        'analyzedAt': analyzedAt?.toIso8601String(),
      };

  factory QueuedSurvey.fromJson(Map<String, dynamic> j) => QueuedSurvey(
        id: '${j['id']}',
        createdAt: DateTime.tryParse('${j['createdAt']}') ?? DateTime.now(),
        productId: (j['productId'] as num?)?.toInt() ?? 0,
        productName: '${j['productName'] ?? ''}',
        categoryName: '${j['categoryName'] ?? ''}',
        respondentLabel: '${j['respondentLabel'] ?? ''}',
        area: '${j['area'] ?? ''}',
        respondent: j['respondent'] is Map ? Map<String, dynamic>.from(j['respondent'] as Map) : null,
        response: Map<String, dynamic>.from((j['response'] as Map?) ?? {}),
        answers: ((j['answers'] as List?) ?? const [])
            .whereType<Map>()
            .map((m) => Map<String, dynamic>.from(m))
            .toList(),
        mediaPath: j['mediaPath'] as String?,
        mediaName: '${j['mediaName'] ?? 'recording'}',
        mediaKind: '${j['mediaKind'] ?? 'audio'}',
        durationSeconds: (j['durationSeconds'] as num?)?.toDouble() ?? 0,
        status: _status('${j['status']}'),
        error: j['error'] as String?,
        respondentId: (j['respondentId'] as num?)?.toInt(),
        responseId: (j['responseId'] as num?)?.toInt(),
        submitted: j['submitted'] == true,
        jobId: (j['jobId'] as num?)?.toInt(),
        feedbackId: (j['feedbackId'] as num?)?.toInt(),
        rating: (j['rating'] as num?)?.toDouble(),
        sentiment: j['sentiment'] as String?,
        summary: j['summary'] as String?,
        analyzedAt: j['analyzedAt'] == null ? null : DateTime.tryParse('${j['analyzedAt']}'),
      );
}

/// "Priya Sharma" -> "Priya S."
String shortName(String full) {
  final parts = full.trim().split(RegExp(r'\s+')).where((p) => p.isNotEmpty).toList();
  if (parts.isEmpty) return 'Respondent';
  if (parts.length == 1) return parts.first;
  return '${parts.first} ${parts.last[0].toUpperCase()}.';
}

class SurveyQueue {
  SurveyQueue._();

  /// Every survey on this phone, newest first. UI listens to this.
  static final ValueNotifier<List<QueuedSurvey>> items = ValueNotifier<List<QueuedSurvey>>([]);

  /// Upload progress (0..1) of the survey being uploaded, by id.
  static final ValueNotifier<Map<String, double>> progress = ValueNotifier<Map<String, double>>({});

  static final SurveyApi _api = SurveyApi();
  static bool _loaded = false;
  static bool _running = false;
  static Timer? _retryTimer;

  static Future<Directory> _dir() async {
    final base = await getApplicationDocumentsDirectory();
    final dir = Directory('${base.path}${Platform.pathSeparator}surveys');
    if (!await dir.exists()) await dir.create(recursive: true);
    return dir;
  }

  static Future<File> _indexFile() async => File('${(await _dir()).path}${Platform.pathSeparator}index.json');

  static Future<void> load() async {
    if (_loaded) return;
    _loaded = true;
    try {
      final f = await _indexFile();
      if (await f.exists()) {
        final data = jsonDecode(await f.readAsString());
        if (data is List) {
          items.value = data
              .whereType<Map>()
              .map((m) => QueuedSurvey.fromJson(Map<String, dynamic>.from(m)))
              .toList()
            ..sort((a, b) => b.createdAt.compareTo(a.createdAt));
        }
      }
    } catch (e) {
      debugPrint('Survey queue: could not load ($e)');
    }
    // Try waiting uploads every minute while the app is open.
    _retryTimer ??= Timer.periodic(const Duration(minutes: 1), (_) => process());
    process();
  }

  static Future<void> _save() async {
    final f = await _indexFile();
    await f.writeAsString(jsonEncode(items.value.map((s) => s.toJson()).toList()));
  }

  static void _changed() {
    items.value = List<QueuedSurvey>.from(items.value); // notify listeners
    _save();
  }

  /// Saves a completed survey on the phone (copying the recording into app
  /// storage so it survives) and starts uploading it.
  static Future<QueuedSurvey> add({
    required int productId,
    required String productName,
    required String categoryName,
    required Map<String, dynamic> respondent,
    required Map<String, dynamic> response,
    required List<Map<String, dynamic>> answers,
    required String mediaSourcePath,
    required String mediaName,
    required String mediaKind,
    required double durationSeconds,
  }) async {
    await load();
    final id = DateTime.now().microsecondsSinceEpoch.toString();
    final ext = mediaName.contains('.') ? mediaName.split('.').last : (mediaKind == 'video' ? 'mp4' : 'm4a');
    final target = '${(await _dir()).path}${Platform.pathSeparator}$id.$ext';
    await File(mediaSourcePath).copy(target);

    final survey = QueuedSurvey(
      id: id,
      createdAt: DateTime.now(),
      productId: productId,
      productName: productName,
      categoryName: categoryName,
      respondentLabel: shortName('${respondent['name'] ?? ''}'),
      area: '${respondent['location'] ?? ''}',
      respondent: respondent,
      response: response,
      answers: answers,
      mediaPath: target,
      mediaName: mediaName,
      mediaKind: mediaKind,
      durationSeconds: durationSeconds,
    );
    items.value = [survey, ...items.value];
    await _save();
    process();
    return survey;
  }

  static int get pendingCount => items.value.where((s) => s.needsUpload).length;

  /// Sends waiting surveys, then follows their analyses until they finish.
  /// Safe to call any time; only one run happens at once.
  static Future<void> process({bool includeFailed = false}) async {
    await load();
    if (_running) return;
    _running = true;
    final tried = <String>{}; // each survey sent at most once per run (no endless retry loop)
    try {
      var online = true;
      while (online) {
        // 1. Send surveys that haven't been uploaded yet (each takes seconds).
        final toSend = items.value
            .where((s) => !tried.contains(s.id))
            .where((s) => s.status == SurveyStatus.waiting || (includeFailed && s.status == SurveyStatus.failed))
            .toList()
          ..sort((a, b) => a.createdAt.compareTo(b.createdAt));
        for (final s in toSend) {
          tried.add(s.id);
          if (!await _send(s)) {
            online = false;
            break;
          }
        }
        if (!online) break;

        // 2. Follow analyses still running on the server.
        final running = items.value.where((s) => s.status == SurveyStatus.analyzing && s.jobId != null).toList();
        if (running.isEmpty) break;
        for (final s in running) {
          if (!await _poll(s)) {
            online = false;
            break;
          }
        }
        if (online) await Future.delayed(const Duration(seconds: 5));
      }
    } finally {
      _running = false;
    }
  }

  /// Respondent -> survey -> answers -> recording (queued for analysis).
  /// Returns false when the connection is down (stop and retry later).
  static Future<bool> _send(QueuedSurvey s) async {
    // Already uploaded before the app closed: just follow the analysis.
    if (s.jobId != null) {
      s
        ..status = SurveyStatus.analyzing
        ..error = null;
      _changed();
      return true;
    }
    s
      ..status = SurveyStatus.uploading
      ..error = null;
    _changed();
    try {
      if (s.respondentId == null) {
        if (s.respondent == null) throw StateError('The respondent details are missing on this phone.');
        s.respondentId = await _api.createRespondent(s.respondent!);
        _changed();
      }
      if (s.responseId == null) {
        s.responseId = await _api.createResponse({...s.response, 'respondent_id': s.respondentId});
        _changed();
        // Count it under this executive's account. Not critical: older backends skip it.
        try {
          await _api.claimResponse(s.responseId!);
        } catch (_) {}
      }
      if (!s.submitted) {
        await _api.saveAnswers(s.responseId!, s.answers);
        await _api.submit(s.responseId!);
        s.submitted = true;
        _changed();
      }
      final path = s.mediaPath;
      if (path == null || !await File(path).exists()) {
        throw StateError('The recording is no longer on this phone.');
      }
      void onProgress(double p) => progress.value = {...progress.value, s.id: p};
      try {
        final job = await _api.startAnalysis(
          path: path,
          filename: s.mediaName,
          responseId: s.responseId!,
          onProgress: onProgress,
        );
        s
          ..jobId = (job['job_id'] as num?)?.toInt()
          ..status = SurveyStatus.analyzing;
        progress.value = Map<String, double>.from(progress.value)..remove(s.id);
        _changed();
        return true;
      } on DioException catch (e) {
        if (e.response?.statusCode != 404) rethrow;
        // Older backend without the analysis queue: upload and wait, as before.
        final result = await _api.uploadRecording(
          path: path,
          filename: s.mediaName,
          responseId: s.responseId!,
          onProgress: (p) {
            onProgress(p);
            if (p >= 1 && s.status != SurveyStatus.analyzing) {
              s.status = SurveyStatus.analyzing;
              _changed();
            }
          },
        );
        await _complete(s, result);
        return true;
      }
    } catch (e) {
      progress.value = Map<String, double>.from(progress.value)..remove(s.id);
      if (isConnectionProblem(e)) {
        s
          ..status = SurveyStatus.waiting
          ..error = 'Waiting for a connection.';
        _changed();
        return false;
      }
      s
        ..status = SurveyStatus.failed
        ..error = apiErrorText(e);
      _changed();
      return true; // a problem with this survey only: carry on with the others
    }
  }

  /// Checks one running analysis. Returns false when the connection is down.
  static Future<bool> _poll(QueuedSurvey s) async {
    try {
      final job = await _api.analysisJob(s.jobId!);
      final status = '${job['status'] ?? ''}';
      if (status == 'done') {
        final result = job['result'] is Map
            ? Map<String, dynamic>.from(job['result'] as Map)
            : <String, dynamic>{'feedback_id': job['feedback_id']};
        await _complete(s, result);
      } else if (status == 'failed') {
        s
          ..status = SurveyStatus.failed
          ..jobId = null // "Try again" uploads it again
          ..error = '${job['error'] ?? 'The analysis failed on the server.'}';
        _changed();
      }
      return true;
    } catch (e) {
      if (isConnectionProblem(e)) return false; // keep the job; follow it later
      if (e is DioException && e.response?.statusCode == 404) {
        // The job is gone (e.g. database cleared): upload again.
        s
          ..jobId = null
          ..status = SurveyStatus.waiting;
        _changed();
      }
      return true;
    }
  }

  /// A finished analysis: keep the short result, delete personal data and the recording.
  static Future<void> _complete(QueuedSurvey s, Map<String, dynamic> result) async {
    final summary = result['summary'];
    s
      ..status = SurveyStatus.analyzed
      ..feedbackId = (result['feedback_id'] as num?)?.toInt()
      ..rating = (result['overall_rating'] as num?)?.toDouble()
      ..sentiment = summary is Map ? '${summary['sentiment'] ?? ''}' : null
      ..summary = summary is Map ? '${summary['summary'] ?? ''}' : (summary is String ? summary : null)
      ..analyzedAt = DateTime.now();
    final path = s.mediaPath;
    if (path != null) {
      try {
        await File(path).delete();
      } catch (_) {}
    }
    s
      ..mediaPath = null
      ..respondent = null;
    progress.value = Map<String, double>.from(progress.value)..remove(s.id);
    _changed();
    ServerHistory.refresh();
    Session.refreshStats(); // my numbers include it now
  }

  /// Removes finished surveys from this phone that were deleted on the server.
  static Future<void> reconcileWithServer() async {
    await load();
    final done = items.value.where((s) => s.status == SurveyStatus.analyzed && s.responseId != null).toList();
    if (done.isEmpty || !Session.signedIn) return;
    try {
      final existing = await _api.existingSurveys(done.map((s) => s.responseId!).toList());
      final gone = done.where((s) => !existing.contains(s.responseId)).map((s) => s.id).toSet();
      if (gone.isNotEmpty) {
        items.value = items.value.where((s) => !gone.contains(s.id)).toList();
        await _save();
      }
    } catch (_) {
      // Offline or older backend: try again next time.
    }
  }

  static Future<void> retry(String id) async {
    final s = items.value.where((x) => x.id == id).firstOrNull;
    if (s == null) return;
    s
      ..status = SurveyStatus.waiting
      ..error = null;
    _changed();
    await process();
  }

  /// Deletes a survey from the phone (and its recording, if still here).
  static Future<void> remove(String id) async {
    final s = items.value.where((x) => x.id == id).firstOrNull;
    if (s?.mediaPath != null) {
      try {
        await File(s!.mediaPath!).delete();
      } catch (_) {}
    }
    items.value = items.value.where((x) => x.id != id).toList();
    await _save();
  }

  /// Removes analyzed surveys from this phone's History (they stay on the server).
  static Future<void> clearAnalyzed() async {
    items.value = items.value.where((x) => x.status != SurveyStatus.analyzed).toList();
    await _save();
  }

  /// Space used on the phone by recordings still waiting to upload.
  static Future<int> storageBytes() async {
    var total = 0;
    for (final s in items.value) {
      if (s.mediaPath != null) {
        try {
          total += await File(s.mediaPath!).length();
        } catch (_) {}
      }
    }
    return total;
  }
}

/* ============================================================
   Executive profile and recent products (saved on the phone)
   ============================================================ */

class RecentProduct {
  const RecentProduct({required this.id, required this.name, required this.categoryId, required this.categoryName});

  final int id;
  final String name;
  final int categoryId;
  final String categoryName;

  Map<String, dynamic> toJson() => {'id': id, 'name': name, 'categoryId': categoryId, 'categoryName': categoryName};

  factory RecentProduct.fromJson(Map<String, dynamic> j) => RecentProduct(
        id: (j['id'] as num?)?.toInt() ?? 0,
        name: '${j['name'] ?? ''}',
        categoryId: (j['categoryId'] as num?)?.toInt() ?? 0,
        categoryName: '${j['categoryName'] ?? ''}',
      );
}

class AppProfile {
  AppProfile._();

  static final ValueNotifier<String> executiveName = ValueNotifier<String>('');
  static final ValueNotifier<String> lastSurveyLocation = ValueNotifier<String>('');
  static final ValueNotifier<List<RecentProduct>> recentProducts = ValueNotifier<List<RecentProduct>>([]);

  /// Appearance: 'system' | 'light' | 'dark'.
  static final ValueNotifier<String> themeMode = ValueNotifier<String>('system');

  /// True once the permissions screen has been shown after sign-in.
  static bool permissionsDone = false;

  /// "Signing in as" on the login screen (executive | admin | client): only the
  /// wording; the server's role decides where the account goes.
  static String loginRole = 'executive';
  static bool _loaded = false;

  static Future<File> _file() async {
    final base = await getApplicationDocumentsDirectory();
    return File('${base.path}${Platform.pathSeparator}profile.json');
  }

  static Future<void> load() async {
    if (_loaded) return;
    _loaded = true;
    try {
      final f = await _file();
      if (await f.exists()) {
        final j = jsonDecode(await f.readAsString());
        if (j is Map) {
          executiveName.value = '${j['executiveName'] ?? ''}';
          themeMode.value = '${j['themeMode'] ?? 'system'}';
          permissionsDone = j['permissionsDone'] == true;
          final role = '${j['loginRole'] ?? ''}';
          if (const {'executive', 'admin', 'client'}.contains(role)) loginRole = role;
          lastSurveyLocation.value = '${j['lastSurveyLocation'] ?? ''}';
          recentProducts.value = ((j['recentProducts'] as List?) ?? const [])
              .whereType<Map>()
              .map((m) => RecentProduct.fromJson(Map<String, dynamic>.from(m)))
              .toList();
        }
      }
    } catch (e) {
      debugPrint('Profile: could not load ($e)');
    }
  }

  static Future<void> save() async {
    final f = await _file();
    await f.writeAsString(jsonEncode({
      'executiveName': executiveName.value,
      'themeMode': themeMode.value,
      'permissionsDone': permissionsDone,
      'loginRole': loginRole,
      'lastSurveyLocation': lastSurveyLocation.value,
      'recentProducts': recentProducts.value.map((p) => p.toJson()).toList(),
    }));
  }

  static Future<void> rememberProduct(RecentProduct p) async {
    recentProducts.value = [p, ...recentProducts.value.where((x) => x.id != p.id)].take(6).toList();
    await save();
  }

  /// "Survey again" only offers products this person may still survey. The admin can
  /// give a product to other executives (or deactivate it); the server's product
  /// list for this account is the truth. Offline: keeps the list as it is.
  static Future<void> refreshRecentProducts() async {
    if (recentProducts.value.isEmpty || !Session.signedIn) return;
    try {
      final allowed = {for (final p in await SurveyApi().products()) p.id};
      final kept = recentProducts.value.where((p) => allowed.contains(p.id)).toList();
      if (kept.length != recentProducts.value.length) {
        recentProducts.value = kept;
        await save();
      }
    } catch (_) {
      // Offline or server unavailable: try again next time.
    }
  }
}

/* ============================================================
   Server history + one combined list for Home and History
   ============================================================ */

/// Surveys stored on the server (made on any phone or on the web).
class ServerHistory {
  ServerHistory._();

  static final ValueNotifier<List<ServerSurvey>> items = ValueNotifier<List<ServerSurvey>>([]);
  static final ValueNotifier<bool> loading = ValueNotifier<bool>(false);

  /// Every survey on the server (the list above holds the most recent ones).
  static final ValueNotifier<int> total = ValueNotifier<int>(0);

  /// Last error, or null when the last refresh worked.
  static final ValueNotifier<String?> error = ValueNotifier<String?>(null);
  static DateTime? lastUpdated;

  static final SurveyApi _api = SurveyApi();

  static Future<void> refresh() async {
    if (loading.value || !Session.canViewHistory) return;
    loading.value = true;
    try {
      final (list, count) = await _api.recentSurveys();
      items.value = list;
      total.value = count;
      error.value = null;
      lastUpdated = DateTime.now();
    } catch (e) {
      error.value = apiErrorText(e);
    } finally {
      loading.value = false;
    }
  }
}

/// One row in Home / History: a server survey or a survey still on this phone.
class HistoryEntry {
  const HistoryEntry({
    required this.key,
    required this.createdAt,
    required this.productName,
    required this.respondentLabel,
    required this.area,
    required this.executiveName,
    required this.status,
    this.local,
    this.feedbackId,
    this.rating,
    this.sentiment,
    this.summary,
    this.serverStatus,
  });

  final String key;
  final DateTime createdAt;
  final String productName;
  final String respondentLabel;
  final String area;
  final String executiveName;
  final SurveyStatus status;
  final QueuedSurvey? local; // set while the survey is still on this phone
  final int? feedbackId;
  final double? rating;
  final String? sentiment;
  final String? summary;

  /// For server surveys that never got a recording (e.g. abandoned on the web).
  final String? serverStatus;

  bool get noRecording =>
      local == null && status != SurveyStatus.analyzed && status != SurveyStatus.analyzing && status != SurveyStatus.failed;
}

/// Server surveys, plus this phone's surveys that aren't finished on the server
/// yet (waiting, uploading, analyzing, failed). No duplicates.
List<HistoryEntry> buildHistory(List<QueuedSurvey> local, List<ServerSurvey> server) {
  final entries = <HistoryEntry>[];
  final activeLocal = local.where((s) => s.status != SurveyStatus.analyzed).toList();
  final activeIds = {for (final s in activeLocal) if (s.responseId != null) s.responseId};
  final serverIds = <int>{};

  for (final s in server) {
    serverIds.add(s.responseId);
    if (activeIds.contains(s.responseId)) continue; // the phone's copy shows live progress
    entries.add(HistoryEntry(
      key: 'server-${s.responseId}',
      createdAt: s.createdAt,
      productName: s.productName,
      respondentLabel: s.respondentLabel,
      area: s.area,
      executiveName: s.executiveName,
      status: s.isAnalyzed
          ? SurveyStatus.analyzed
          : s.isAnalyzing
              ? SurveyStatus.analyzing
              : s.analysisStatus == 'failed'
                  ? SurveyStatus.failed
                  : SurveyStatus.waiting,
      feedbackId: s.feedbackId,
      rating: s.rating,
      sentiment: s.tone ?? s.sentiment,
      summary: s.summary,
      serverStatus: s.status,
    ));
  }

  for (final s in local) {
    final onServer = s.responseId != null && serverIds.contains(s.responseId);
    // Analyzed surveys come from the server list; keep the phone's copy only if
    // the server list couldn't be loaded (offline) or doesn't include it yet.
    if (s.status == SurveyStatus.analyzed && onServer) continue;
    entries.add(HistoryEntry(
      key: 'local-${s.id}',
      createdAt: s.createdAt,
      productName: s.productName,
      respondentLabel: s.respondentLabel,
      area: s.area,
      executiveName: '${s.response['executive_name'] ?? ''}',
      status: s.status,
      local: s,
      feedbackId: s.feedbackId,
      rating: s.rating,
      sentiment: s.sentiment,
      summary: s.summary,
    ));
  }

  entries.sort((a, b) => b.createdAt.compareTo(a.createdAt));
  return entries;
}