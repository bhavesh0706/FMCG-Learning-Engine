import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'dart:math';

import 'package:cryptography/cryptography.dart';
import 'package:dio/dio.dart';
import 'package:flutter/foundation.dart';
import 'package:path_provider/path_provider.dart';
import 'package:sqflite_sqlcipher/sqflite.dart';

import 'connectivity_service.dart';
import 'local_db.dart';
import 'recording_vault.dart';
import 'session.dart';
import 'survey_api.dart';

/*
  Offline-first survey queue (survey persons).

  Every completed survey is saved on the phone first, then uploaded:
    respondent -> survey -> answers -> submit -> recording (resumable) -> analysis.

  Storage
    The survey (customer details, answers, references, upload progress) is a row in
    the encrypted local database (local_db.dart). The recording is copied into the
    app's private folder. Both survive closing the app, a restart of the phone and
    lost connections. Each survey belongs to the account that collected it, and only
    that account's surveys are shown and uploaded.

  Never duplicated
    Each survey has a UUID (clientUuid) sent with the customer and the survey, so a
    retry after a lost reply returns the records the server already created. The
    recording is sent in 2 MB pieces under its own upload id; after an interruption
    the upload continues from the bytes the server confirms, and completing it
    queues exactly one analysis. Every finished step is saved before the next starts.

  Status (sync dashboard)
    waiting    Pending upload (no connection, or a retry is scheduled)
    uploading  Uploading
    analyzing  Synced: the server confirmed everything; its analysis is running
    analyzed   Synced and analyzed
    failed     Upload failed (the server refused it: "Try again" after fixing it)

  Retries: connection problems wait for the network; server errors (5xx, 429)
  retry automatically with growing pauses (1, 2, 4 ... 60 minutes).

  Privacy: once a survey is analyzed on the server, its personal details (full name,
  mobile, address) and the recording are deleted from the phone. Only a short label
  ("Priya S.") and the area are kept for History; the server has the rest.
*/

enum SurveyStatus { waiting, uploading, analyzing, analyzed, failed }

SurveyStatus _status(String s) =>
    SurveyStatus.values.firstWhere((v) => v.name == s, orElse: () => SurveyStatus.waiting);

/// Sync dashboard wording for a status.
String syncLabel(SurveyStatus s) => switch (s) {
  SurveyStatus.waiting => 'Pending upload',
  SurveyStatus.uploading => 'Uploading',
  SurveyStatus.analyzing || SurveyStatus.analyzed => 'Synced',
  SurveyStatus.failed => 'Upload failed',
};

/// A random (version 4) UUID.
String newUuid() {
  final r = Random.secure();
  final b = List<int>.generate(16, (_) => r.nextInt(256));
  b[6] = (b[6] & 0x0f) | 0x40;
  b[8] = (b[8] & 0x3f) | 0x80;
  final h = b.map((x) => x.toRadixString(16).padLeft(2, '0')).join();
  return '${h.substring(0, 8)}-${h.substring(8, 12)}-${h.substring(12, 16)}-${h.substring(16, 20)}-${h.substring(20)}';
}

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
    this.ownerUserId,
    String? clientUuid,
    this.status = SurveyStatus.waiting,
    this.error,
    this.respondentId,
    this.responseId,
    this.submitted = false,
    this.uploadId,
    this.uploadedBytes = 0,
    this.mediaBytes,
    this.mediaSha256,
    this.encKeyId,
    this.encWrappedKey,
    this.attempts = 0,
    this.nextAttemptAt,
    this.syncedAt,
    this.jobId,
    this.feedbackId,
    this.rating,
    this.sentiment,
    this.summary,
    this.analyzedAt,
  }) : clientUuid = clientUuid ?? newUuid();

  final String id;
  final DateTime createdAt;
  final int productId;
  final String productName;
  final String categoryName;
  final String respondentLabel; // "Priya S." - kept after upload
  final String area; // city / locality - kept after upload

  /// The account that collected this survey (only it uploads it).
  int? ownerUserId;

  /// Sent with the customer and the survey: the server's duplicate check.
  final String clientUuid;

  Map<String, dynamic>? respondent; // full details: deleted after analysis
  final Map<String, dynamic> response;
  final List<Map<String, dynamic>> answers;

  String? mediaPath; // deleted after analysis
  final String mediaName;
  final String mediaKind; // audio | video
  final double durationSeconds;

  SurveyStatus status;
  String? error;
  int? respondentId;
  int? responseId;
  bool submitted;

  // Resumable recording upload
  String? uploadId;
  int uploadedBytes;
  int? mediaBytes;
  String? mediaSha256;

  /// The recording is encrypted (services/recording_vault.dart): its data key, wrapped with
  /// the server's public key. The phone can't unwrap it. Null = an older plaintext recording.
  int? encKeyId;
  String? encWrappedKey;
  bool get isEncrypted => encWrappedKey != null && encKeyId != null;

  // Automatic retries after server errors
  int attempts;
  DateTime? nextAttemptAt;

  /// When the server confirmed the whole survey and its recording.
  DateTime? syncedAt;

  int? jobId; // analysis job on the server, once the recording is uploaded
  int? feedbackId;
  double? rating;
  String? sentiment;
  String? summary;
  DateTime? analyzedAt;

  bool get needsUpload => status == SurveyStatus.waiting || status == SurveyStatus.failed;
  bool get isSynced => status == SurveyStatus.analyzing || status == SurveyStatus.analyzed;

  Map<String, dynamic> toJson() => {
    'id': id,
    'createdAt': createdAt.toIso8601String(),
    'productId': productId,
    'productName': productName,
    'categoryName': categoryName,
    'respondentLabel': respondentLabel,
    'area': area,
    'ownerUserId': ownerUserId,
    'clientUuid': clientUuid,
    'respondent': respondent,
    'response': response,
    'answers': answers,
    'mediaPath': mediaPath,
    'mediaName': mediaName,
    'mediaKind': mediaKind,
    'durationSeconds': durationSeconds,
    // An upload interrupted by closing the app resumes as "waiting".
    'status': (status == SurveyStatus.uploading) ? SurveyStatus.waiting.name : status.name,
    'error': error,
    'respondentId': respondentId,
    'responseId': responseId,
    'submitted': submitted,
    'uploadId': uploadId,
    'uploadedBytes': uploadedBytes,
    'mediaBytes': mediaBytes,
    'mediaSha256': mediaSha256,
    'encKeyId': encKeyId,
    'encWrappedKey': encWrappedKey,
    'attempts': attempts,
    'nextAttemptAt': nextAttemptAt?.toIso8601String(),
    'syncedAt': syncedAt?.toIso8601String(),
    'jobId': jobId,
    'feedbackId': feedbackId,
    'rating': rating,
    'sentiment': sentiment,
    'summary': summary,
    'analyzedAt': analyzedAt?.toIso8601String(),
  };

  factory QueuedSurvey.fromJson(Map<String, dynamic> j) {
    final saved = _status('${j['status']}');
    return QueuedSurvey(
      id: '${j['id']}',
      createdAt: DateTime.tryParse('${j['createdAt']}') ?? DateTime.now(),
      productId: (j['productId'] as num?)?.toInt() ?? 0,
      productName: '${j['productName'] ?? ''}',
      categoryName: '${j['categoryName'] ?? ''}',
      respondentLabel: '${j['respondentLabel'] ?? ''}',
      area: '${j['area'] ?? ''}',
      ownerUserId: (j['ownerUserId'] as num?)?.toInt(),
      clientUuid: j['clientUuid'] is String && (j['clientUuid'] as String).isNotEmpty
          ? j['clientUuid'] as String
          : null,
      respondent: j['respondent'] is Map ? Map<String, dynamic>.from(j['respondent'] as Map) : null,
      response: Map<String, dynamic>.from((j['response'] as Map?) ?? {}),
      answers: ((j['answers'] as List?) ?? const []).whereType<Map>().map((m) => Map<String, dynamic>.from(m)).toList(),
      mediaPath: j['mediaPath'] as String?,
      mediaName: '${j['mediaName'] ?? 'recording'}',
      mediaKind: '${j['mediaKind'] ?? 'audio'}',
      durationSeconds: (j['durationSeconds'] as num?)?.toDouble() ?? 0,
      status: saved == SurveyStatus.uploading ? SurveyStatus.waiting : saved,
      error: j['error'] as String?,
      respondentId: (j['respondentId'] as num?)?.toInt(),
      responseId: (j['responseId'] as num?)?.toInt(),
      submitted: j['submitted'] == true,
      uploadId: j['uploadId'] as String?,
      uploadedBytes: (j['uploadedBytes'] as num?)?.toInt() ?? 0,
      mediaBytes: (j['mediaBytes'] as num?)?.toInt(),
      mediaSha256: j['mediaSha256'] as String?,
      encKeyId: (j['encKeyId'] as num?)?.toInt(),
      encWrappedKey: j['encWrappedKey'] as String?,
      attempts: (j['attempts'] as num?)?.toInt() ?? 0,
      nextAttemptAt: j['nextAttemptAt'] == null ? null : DateTime.tryParse('${j['nextAttemptAt']}'),
      syncedAt: j['syncedAt'] == null ? null : DateTime.tryParse('${j['syncedAt']}'),
      jobId: (j['jobId'] as num?)?.toInt(),
      feedbackId: (j['feedbackId'] as num?)?.toInt(),
      rating: (j['rating'] as num?)?.toDouble(),
      sentiment: j['sentiment'] as String?,
      summary: j['summary'] as String?,
      analyzedAt: j['analyzedAt'] == null ? null : DateTime.tryParse('${j['analyzedAt']}'),
    );
  }
}

/// "Priya Sharma" -> "Priya S."
String shortName(String full) {
  final parts = full.trim().split(RegExp(r'\s+')).where((p) => p.isNotEmpty).toList();
  if (parts.isEmpty) return 'Respondent';
  if (parts.length == 1) return parts.first;
  return '${parts.first} ${parts.last[0].toUpperCase()}.';
}

/// Counts for the sync dashboard.
class SyncCounts {
  const SyncCounts({this.total = 0, this.pending = 0, this.uploading = 0, this.synced = 0, this.failed = 0});

  final int total;
  final int pending;
  final int uploading;
  final int synced;
  final int failed;

  factory SyncCounts.of(List<QueuedSurvey> items) => SyncCounts(
    total: items.length,
    pending: items.where((s) => s.status == SurveyStatus.waiting).length,
    uploading: items.where((s) => s.status == SurveyStatus.uploading).length,
    synced: items.where((s) => s.isSynced).length,
    failed: items.where((s) => s.status == SurveyStatus.failed).length,
  );
}

class SurveyQueue {
  SurveyQueue._();

  /// The signed-in account's surveys on this phone, newest first. UI listens to this.
  static final ValueNotifier<List<QueuedSurvey>> items = ValueNotifier<List<QueuedSurvey>>([]);

  /// Upload progress (0..1) of the survey being uploaded, by id.
  static final ValueNotifier<Map<String, double>> progress = ValueNotifier<Map<String, double>>({});

  /// True while a synchronisation is running.
  static final ValueNotifier<bool> syncing = ValueNotifier<bool>(false);

  /// When the last synchronisation finished.
  static final ValueNotifier<DateTime?> lastSyncAt = ValueNotifier<DateTime?>(null);

  /// Every survey on this phone (all accounts).
  static List<QueuedSurvey> _all = [];

  /// Size of one piece of a resumable upload.
  static const int pieceBytes = 2 * 1024 * 1024;

  @visibleForTesting
  static SurveyApi api = SurveyApi();

  /// Folder for recordings waiting to upload (replaced in tests).
  @visibleForTesting
  static Future<Directory> Function() mediaDir = _defaultMediaDir;

  /// Folder of older app versions (index.json + recordings), moved into the database once.
  @visibleForTesting
  static Future<Directory> Function() legacyDir = _defaultLegacyDir;

  @visibleForTesting
  static DateTime Function() now = DateTime.now;

  static bool _loaded = false;
  static Future<void>? _loading;
  static Future<void>? _background;
  static bool _running = false;
  static Timer? _retryTimer;

  static Future<Directory> _defaultMediaDir() async {
    final base = await getApplicationSupportDirectory();
    final dir = Directory('${base.path}${Platform.pathSeparator}offline_media');
    if (!await dir.exists()) await dir.create(recursive: true);
    return dir;
  }

  static Future<Directory> _defaultLegacyDir() async {
    final base = await getApplicationDocumentsDirectory();
    return Directory('${base.path}${Platform.pathSeparator}surveys');
  }

  static int? get _owner => Session.user.value?.id;

  static void _publish() {
    final owner = _owner;
    items.value = _all.where((s) => owner != null && s.ownerUserId == owner).toList()
      ..sort((a, b) => b.createdAt.compareTo(a.createdAt));
  }

  /// Loads the surveys saved on this phone and starts uploading what's waiting.
  static Future<void> load({bool startUploads = true}) async {
    if (!_loaded) {
      await (_loading ??= _loadOnce());
    }
    if (startUploads) {
      _retryTimer ??= Timer.periodic(const Duration(minutes: 1), (_) => process());
      unawaited(process());
    }
  }

  static Future<void> _loadOnce() async {
    try {
      final db = await LocalDb.instance;
      await _migrateLegacy(db);
      final rows = await db.query('submissions', columns: ['data']);
      _all = [
        for (final r in rows) QueuedSurvey.fromJson(Map<String, dynamic>.from(jsonDecode(r['data'] as String) as Map)),
      ];
      // Surveys saved before accounts were separated belong to whoever is signed in now.
      final owner = _owner;
      if (owner != null) {
        for (final s in _all.where((s) => s.ownerUserId == null)) {
          s.ownerUserId = owner;
          await _persist(s);
        }
      }
    } catch (e) {
      debugPrint('Survey queue: could not load ($e)');
    }
    _loaded = true;
    _publish();
    Session.user.addListener(_onUserChanged);
  }

  static void _onUserChanged() {
    final owner = _owner;
    if (owner != null) {
      for (final s in _all.where((s) => s.ownerUserId == null)) {
        s.ownerUserId = owner;
        unawaited(_persist(s));
      }
    }
    _publish();
  }

  /// Older app versions kept the queue in surveys/index.json (unencrypted). Moved into the
  /// encrypted database once; the JSON file (with personal details) is then deleted.
  static Future<void> _migrateLegacy(Database db) async {
    try {
      final dir = await legacyDir();
      final index = File('${dir.path}${Platform.pathSeparator}index.json');
      if (!await index.exists()) return;
      final data = jsonDecode(await index.readAsString());
      if (data is List) {
        final owner = _owner;
        await db.transaction((txn) async {
          for (final m in data.whereType<Map>()) {
            final s = QueuedSurvey.fromJson(Map<String, dynamic>.from(m));
            s.ownerUserId ??= owner;
            await _write(txn, s, ConflictAlgorithm.ignore);
          }
        });
      }
      await index.delete();
      debugPrint('Survey queue: moved ${data is List ? data.length : 0} survey(s) into the encrypted database');
    } catch (e) {
      debugPrint('Survey queue: could not move the old queue ($e)');
    }
  }

  static Future<void> _write(DatabaseExecutor db, QueuedSurvey s, [ConflictAlgorithm c = ConflictAlgorithm.replace]) =>
      db.insert('submissions', {
        'id': s.id,
        'owner_user_id': s.ownerUserId,
        'status': s.status.name,
        'created_at': s.createdAt.toIso8601String(),
        'updated_at': now().toIso8601String(),
        'data': jsonEncode(s.toJson()),
      }, conflictAlgorithm: c);

  static Future<void> _persist(QueuedSurvey s) async => _write(await LocalDb.instance, s);

  /// Saves the survey, then tells the UI. Awaited before every next upload step, so a
  /// step the server confirmed is never forgotten.
  static Future<void> _changed(QueuedSurvey s) async {
    await _persist(s);
    _publish();
  }

  /// Saves a completed survey on the phone (copying the recording into app
  /// storage so it survives) and starts uploading it. Works without a connection.
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
    int? encKeyId,
    String? encWrappedKey,
  }) async {
    await load(startUploads: false);
    final id = newUuid();
    final encrypted = encWrappedKey != null && encKeyId != null;
    final ext = encrypted ? 'enc' : (mediaName.contains('.') ? mediaName.split('.').last : (mediaKind == 'video' ? 'mp4' : 'm4a'));
    final target = '${(await mediaDir()).path}${Platform.pathSeparator}$id.$ext';
    // An encrypted recording is moved (no second copy); an older plaintext one is copied as before.
    final copied = encrypted ? await _move(File(mediaSourcePath), target) : await File(mediaSourcePath).copy(target);

    final survey = QueuedSurvey(
      id: id,
      clientUuid: id,
      ownerUserId: _owner,
      createdAt: now(),
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
      mediaBytes: await copied.length(),
      encKeyId: encrypted ? encKeyId : null,
      encWrappedKey: encrypted ? encWrappedKey : null,
    );
    _all.add(survey);
    await _changed(survey); // on the phone before anything else happens
    unawaited(_background = process());
    return survey;
  }

  static Future<File> _move(File source, String target) async {
    try {
      return await source.rename(target);
    } on FileSystemException {
      final copy = await source.copy(target); // another volume
      await source.delete();
      return copy;
    }
  }

  /// A recording queued by an older app version is still plaintext: seal it now (when the
  /// phone has the server's public key), so only encrypted recordings stay on the phone.
  static Future<void> _sealIfPlain(QueuedSurvey s) async {
    final path = s.mediaPath;
    if (s.isEncrypted || path == null || !await File(path).exists()) return;
    final key = await RecordingVault.cachedKey();
    if (key == null) return; // uploaded as it is; the server encrypts it at rest
    final sealed = await RecordingVault.seal(path, key: key);
    final target = '${(await mediaDir()).path}${Platform.pathSeparator}${s.id}.enc';
    await _move(File(sealed.path), target);
    s
      ..mediaPath = target
      ..mediaBytes = sealed.bytes
      ..mediaSha256 = null
      ..uploadId = null
      ..uploadedBytes = 0
      ..encKeyId = sealed.keyId
      ..encWrappedKey = sealed.wrappedKey;
    await _changed(s);
  }

  static int get pendingCount => items.value.where((s) => s.needsUpload).length;

  static SyncCounts get counts => SyncCounts.of(items.value);

  /// "Sync now": uploads everything waiting or failed, ignoring scheduled retry pauses.
  static Future<void> syncNow() => process(includeFailed: true, force: true);

  /// Uploads waiting surveys (oldest first), then follows their analyses in the
  /// background. Safe to call any time: a call during a running upload waits for that
  /// run instead of starting a second one. Returns when the uploads are done.
  static Future<void> process({bool includeFailed = false, bool force = false}) async {
    await load(startUploads: false);
    final running = _currentRun;
    if (running != null) return running;
    final run = _upload(includeFailed: includeFailed, force: force);
    _currentRun = run;
    try {
      await run;
    } finally {
      if (identical(_currentRun, run)) _currentRun = null;
    }
    unawaited(_followAnalyses());
  }

  static Future<void>? _currentRun;
  static bool _following = false;
  static bool _stopped = false;

  static Future<void> _upload({required bool includeFailed, required bool force}) async {
    if (!ConnectivityService.online.value && !force) return;
    if (Session.offlineMode.value) {
      // Signed in offline: sign in to the server first (with the password from this session).
      if (!await Session.restoreOnline()) return;
    }
    if (!Session.hasServerSession || _owner == null) return;
    _running = true;
    syncing.value = true;
    final tried = <String>{}; // each survey sent at most once per run (no endless retry loop)
    try {
      var more = true;
      while (more) {
        final owner = _owner;
        final t = now();
        // Includes surveys saved while this run was busy.
        final toSend = _all
            .where((s) => s.ownerUserId == owner && !tried.contains(s.id))
            .where((s) => s.status == SurveyStatus.waiting || (includeFailed && s.status == SurveyStatus.failed))
            .where((s) => force || s.nextAttemptAt == null || !s.nextAttemptAt!.isAfter(t))
            .toList()
          ..sort((a, b) => a.createdAt.compareTo(b.createdAt));
        if (toSend.isEmpty) break;
        for (final s in toSend) {
          tried.add(s.id);
          if (!await _send(s)) {
            more = false; // no connection: stop and retry later
            break;
          }
        }
      }
    } finally {
      _running = false;
      syncing.value = false;
      lastSyncAt.value = now();
    }
  }

  /// Follows analyses still running on the server until they finish or the connection drops.
  static Future<void> _followAnalyses() async {
    if (_following) return;
    _following = true;
    try {
      while (!_stopped && Session.hasServerSession) {
        final owner = _owner;
        final running =
            _all.where((s) => s.ownerUserId == owner && s.status == SurveyStatus.analyzing && s.jobId != null).toList();
        if (running.isEmpty) break;
        var online = true;
        for (final s in running) {
          if (_stopped || !await _poll(s)) {
            online = false;
            break;
          }
        }
        if (!online) break;
        for (var i = 0; i < 50 && !_stopped; i++) {
          await Future<void>.delayed(const Duration(milliseconds: 100)); // 5 s between checks
        }
      }
    } finally {
      _following = false;
    }
  }

  static int? _statusCode(Object e) => e is DioException ? e.response?.statusCode : null;

  /// The server is temporarily unable (5xx, 429, 408): retry on its own later.
  static bool _isTemporary(Object e) {
    final code = _statusCode(e);
    if (code == 409 && e is DioException && _receivedFrom(e) != null) return true; // upload incomplete
    return code != null && (code >= 500 || code == 429 || code == 408);
  }

  static Duration _backoff(int attempts) => Duration(minutes: min(60, 1 << min(attempts - 1, 6)));

  /// Respondent -> survey -> answers -> recording (queued for analysis).
  /// Returns false when the connection is down (stop and retry later).
  static Future<bool> _send(QueuedSurvey s) async {
    // Already uploaded before the app closed: just follow the analysis.
    if (s.jobId != null) {
      s
        ..status = SurveyStatus.analyzing
        ..error = null;
      await _changed(s);
      return true;
    }
    s
      ..status = SurveyStatus.uploading
      ..error = null;
    await _changed(s);
    try {
      if (s.respondentId == null) {
        if (s.respondent == null) throw StateError('The respondent details are missing on this phone.');
        s.respondentId = await api.createRespondent({...s.respondent!, 'client_uuid': s.clientUuid});
        await _changed(s);
      }
      if (s.responseId == null) {
        s.responseId = await api.createResponse({
          ...s.response,
          'respondent_id': s.respondentId,
          'client_uuid': s.clientUuid,
        });
        await _changed(s);
        // Count it under this executive's account. Not critical: older backends skip it.
        try {
          await api.claimResponse(s.responseId!);
        } catch (_) {}
      }
      if (!s.submitted) {
        await api.saveAnswers(s.responseId!, s.answers);
        await api.submit(s.responseId!);
        s.submitted = true;
        await _changed(s);
      }
      await _sealIfPlain(s);
      final path = s.mediaPath;
      if (path == null || !await File(path).exists()) {
        throw StateError('The recording is no longer on this phone.');
      }
      void onProgress(double p) => progress.value = {...progress.value, s.id: p};

      // Resumable upload; null = a backend without it: the older single-request upload below.
      final job = await _uploadResumable(s, path, onProgress);
      if (job != null) {
        _markSynced(s, job);
        await _changed(s);
        return true;
      }
      try {
        final legacyJob = await api.startAnalysis(
          path: path,
          filename: s.mediaName,
          responseId: s.responseId!,
          onProgress: onProgress,
          keyId: s.encKeyId,
          wrappedKey: s.encWrappedKey,
        );
        _markSynced(s, legacyJob);
        await _changed(s);
        return true;
      } on DioException catch (e) {
        if (e.response?.statusCode != 404) rethrow;
        // Older backend without the analysis queue: upload and wait, as before.
        final result = await api.uploadRecording(
          path: path,
          filename: s.mediaName,
          responseId: s.responseId!,
          onProgress: onProgress,
          keyId: s.encKeyId,
          wrappedKey: s.encWrappedKey,
        );
        s.syncedAt = now();
        await _complete(s, result);
        return true;
      }
    } catch (e) {
      progress.value = Map<String, double>.from(progress.value)..remove(s.id);
      if (isConnectionProblem(e)) {
        s
          ..status = SurveyStatus.waiting
          ..error = 'Waiting for a connection.';
        await _changed(s);
        return false;
      }
      if (_statusCode(e) == 401) {
        // The server ended this session: keep the survey waiting; sign-in is asked again.
        s
          ..status = SurveyStatus.waiting
          ..error = 'Renewing the sign-in: the upload continues automatically.';
        await _changed(s);
        unawaited(Session.refreshMe());
        return false;
      }
      if (_isTemporary(e)) {
        s
          ..attempts = s.attempts + 1
          ..status = SurveyStatus.waiting
          ..nextAttemptAt = now().add(_backoff(s.attempts + 1))
          ..error = 'The server is busy (${_statusCode(e)}). It will try again automatically.';
        await _changed(s);
        return true;
      }
      s
        ..status = SurveyStatus.failed
        ..attempts = s.attempts + 1
        ..error = apiErrorText(e);
      await _changed(s);
      return true; // a problem with this survey only: carry on with the others
    }
  }

  static void _markSynced(QueuedSurvey s, Map<String, dynamic> job) {
    s
      ..jobId = (job['job_id'] as num?)?.toInt()
      ..status = SurveyStatus.analyzing
      ..syncedAt = now()
      ..attempts = 0
      ..nextAttemptAt = null
      ..error = null;
    progress.value = Map<String, double>.from(progress.value)..remove(s.id);
  }

  static Future<String> _sha256Of(String path) async {
    final sink = Sha256().newHashSink();
    await for (final block in File(path).openRead()) {
      sink.add(block);
    }
    sink.close();
    final hash = await sink.hash();
    return hash.bytes.map((b) => b.toRadixString(16).padLeft(2, '0')).join();
  }

  /// Sends the recording in pieces, continuing from what the server already has.
  /// Returns the analysis job once the server confirmed the whole file, or null when
  /// the server is an older version without resumable uploads.
  static Future<Map<String, dynamic>?> _uploadResumable(
    QueuedSurvey s,
    String path,
    void Function(double) onProgress, {
    bool restarted = false,
  }) async {
    final file = File(path);
    final total = await file.length();
    if (s.mediaBytes != total || s.mediaSha256 == null) {
      s
        ..mediaBytes = total
        ..mediaSha256 = await _sha256Of(path);
    }
    if (s.uploadId == null) {
      s.uploadId = newUuid();
      s.uploadedBytes = 0;
    }
    await _changed(s);

    Future<Map<String, dynamic>?> startOver() async {
      // The server no longer has this partial upload (or it arrived damaged): new upload id.
      s
        ..uploadId = null
        ..uploadedBytes = 0;
      await _changed(s);
      if (restarted) throw StateError('The server could not keep the recording upload. Tap Try again.');
      return _uploadResumable(s, path, onProgress, restarted: true);
    }

    Map<String, dynamic> state;
    try {
      state = await api.startResumableUpload(
        uploadId: s.uploadId!,
        filename: s.mediaName,
        totalBytes: total,
        responseId: s.responseId,
        sha256: s.mediaSha256,
        keyId: s.encKeyId,
        wrappedKey: s.encWrappedKey,
      );
    } on DioException catch (e) {
      if (_isMissingRoute(e)) return null; // older backend
      rethrow;
    }
    if (state['job'] is Map) return Map<String, dynamic>.from(state['job'] as Map);

    var offset = (state['received_bytes'] as num?)?.toInt() ?? 0;
    final raf = await file.open();
    try {
      while (offset < total) {
        onProgress(offset / total);
        await raf.setPosition(offset);
        final piece = await raf.read(min(pieceBytes, total - offset));
        try {
          state = await api.putUploadPiece(s.uploadId!, offset, piece);
        } on DioException catch (e) {
          final code = e.response?.statusCode;
          if (code == 404 || code == 410) return await startOver();
          final confirmed = _receivedFrom(e);
          if (code != 409 || confirmed == null) rethrow;
          state = {'received_bytes': confirmed}; // continue from what the server has
        }
        offset = (state['received_bytes'] as num?)?.toInt() ?? offset;
        s.uploadedBytes = offset;
        await _changed(s);
      }
    } finally {
      await raf.close();
    }
    onProgress(1);
    try {
      state = await api.completeResumableUpload(s.uploadId!);
    } on DioException catch (e) {
      final code = e.response?.statusCode;
      if (code == 404 || code == 410 || code == 422) return await startOver();
      final confirmed = _receivedFrom(e);
      if (code == 409 && confirmed != null) {
        // Some bytes are missing on the server: send the rest.
        s.uploadedBytes = confirmed;
        await _changed(s);
        if (restarted) rethrow;
        return await _uploadResumable(s, path, onProgress, restarted: true);
      }
      rethrow;
    }
    final job = state['job'];
    if (job is! Map) throw StateError('The server did not confirm the recording.');
    return Map<String, dynamic>.from(job);
  }

  /// 404 from FastAPI for an address it doesn't have (an older backend), as opposed to
  /// "this record was not found".
  static bool _isMissingRoute(DioException e) {
    if (e.response?.statusCode != 404) return false;
    final data = e.response?.data;
    return data is Map && data['detail'] == 'Not Found';
  }

  static int? _receivedFrom(DioException e) {
    final data = e.response?.data;
    final detail = data is Map ? data['detail'] : null;
    final value = detail is Map ? detail['received_bytes'] : null;
    return value is num ? value.toInt() : null;
  }

  /// Checks one running analysis. Returns false when the connection is down.
  static Future<bool> _poll(QueuedSurvey s) async {
    try {
      final job = await api.analysisJob(s.jobId!);
      final status = '${job['status'] ?? ''}';
      if (status == 'done') {
        final result = job['result'] is Map
            ? Map<String, dynamic>.from(job['result'] as Map)
            : <String, dynamic>{'feedback_id': job['feedback_id']};
        await _complete(s, result);
      } else if (status == 'failed') {
        // The recording is on the server; its analysis failed. "Try again" uploads it again.
        s
          ..status = SurveyStatus.failed
          ..jobId = null
          ..uploadId = null
          ..uploadedBytes = 0
          ..error = 'Analysis failed on the server: ${job['error'] ?? 'unknown error'}';
        await _changed(s);
      }
      return true;
    } catch (e) {
      if (isConnectionProblem(e)) return false; // keep the job; follow it later
      if (e is DioException && e.response?.statusCode == 404) {
        // The job is gone (e.g. database cleared): upload again.
        s
          ..jobId = null
          ..uploadId = null
          ..uploadedBytes = 0
          ..status = SurveyStatus.waiting;
        await _changed(s);
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
      ..analyzedAt = now();
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
    await _changed(s);
    ServerHistory.refresh();
    Session.refreshStats(); // my numbers include it now
  }

  /// Removes finished surveys from this phone that were deleted on the server.
  static Future<void> reconcileWithServer() async {
    await load(startUploads: false);
    final done = items.value.where((s) => s.status == SurveyStatus.analyzed && s.responseId != null).toList();
    if (done.isEmpty || !Session.hasServerSession) return;
    try {
      final existing = await api.existingSurveys(done.map((s) => s.responseId!).toList());
      final gone = done.where((s) => !existing.contains(s.responseId)).map((s) => s.id).toSet();
      for (final id in gone) {
        await _delete(id);
      }
    } catch (_) {
      // Offline or older backend: try again next time.
    }
  }

  static Future<void> retry(String id) async {
    final s = _all.where((x) => x.id == id).firstOrNull;
    if (s == null) return;
    s
      ..status = SurveyStatus.waiting
      ..nextAttemptAt = null
      ..error = null;
    await _changed(s);
    await process(force: true);
  }

  static Future<void> _delete(String id) async {
    _all.removeWhere((x) => x.id == id);
    await (await LocalDb.instance).delete('submissions', where: 'id = ?', whereArgs: [id]);
    _publish();
  }

  /// Deletes a survey from the phone (and its recording, if still here).
  static Future<void> remove(String id) async {
    final s = _all.where((x) => x.id == id).firstOrNull;
    if (s?.mediaPath != null) {
      try {
        await File(s!.mediaPath!).delete();
      } catch (_) {}
    }
    await _delete(id);
  }

  /// Removes analyzed surveys from this phone's History (they stay on the server).
  static Future<void> clearAnalyzed() async {
    for (final s in items.value.where((x) => x.status == SurveyStatus.analyzed).toList()) {
      await _delete(s.id);
    }
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

  /// Tests: waits for uploads started in the background.
  @visibleForTesting
  static Future<void> idle() async {
    await _background;
    _stopped = true; // like closing the app: stop following analyses
    try {
      while (_running || _following || _currentRun != null) {
        await Future<void>.delayed(const Duration(milliseconds: 10));
      }
    } finally {
      _stopped = false;
    }
  }

  /// Tests: forget everything loaded in memory (the database stays).
  @visibleForTesting
  static void resetForTest() {
    _background = null;
    _currentRun = null;
    _following = false;
    _all = [];
    _loaded = false;
    _loading = null;
    _running = false;
    _retryTimer?.cancel();
    _retryTimer = null;
    Session.user.removeListener(_onUserChanged);
    items.value = [];
    progress.value = {};
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