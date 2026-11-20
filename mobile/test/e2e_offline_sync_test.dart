// End-to-end: the app's offline code against the REAL FastAPI backend.
//
// The test starts the backend (SQLite, throw-away folders) on port 8765, signs in
// online, downloads the survey data, stops the backend ("no internet"), signs in
// offline, collects an audio and a video survey, restarts the app, starts the backend
// again, kills it in the middle of a recording upload, starts it again, and checks
// in the backend's database that everything arrived exactly once and byte for byte.
//
// Run (from mobile/):
//   RUN_E2E=1 flutter test test/e2e_offline_sync_test.dart --dart-define=API_BASE_URL=http://127.0.0.1:8765
// Needs backend/.venv with the backend requirements installed.

import 'dart:convert';
import 'dart:io';

import 'package:connectivity_plus/connectivity_plus.dart';
import 'package:cryptography/cryptography.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:mobile/services/api_service.dart';
import 'package:mobile/services/connectivity_service.dart';
import 'package:mobile/services/offline_auth.dart';
import 'package:mobile/services/offline_catalog.dart';
import 'package:mobile/services/session.dart';
import 'package:mobile/services/survey_api.dart';
import 'package:mobile/services/survey_queue.dart';

import 'offline_test_support.dart';

const _port = 8765;
final _backendDir = Directory('${Directory.current.path}/../backend').absolute.path;
final _python = '$_backendDir/.venv/bin/python';

class _Backend {
  _Backend(this.work);

  final Directory work;
  Process? _process;

  Map<String, String> get env => {
    ...Platform.environment,
    'APP_ENV': 'development',
    'DATABASE_URL': 'sqlite:///${work.path}/backend.db',
    'MYSQL_PASSWORD': 'unused-with-sqlite',
    'FMCG_WORKER_MODE': 'off', // analyses stay queued: this test checks the upload, not the AI
    'FMCG_SECRET_KEY': 'e2e-secret-key',
    'FMCG_PBKDF2_ROUNDS': '1000',
    'AI_SERVICE_URL': 'http://127.0.0.1:9',
    'FMCG_STORAGE_ROOT': '${work.path}/storage',
    'FMCG_RECORDINGS_DIR': '${work.path}/recordings',
    'FMCG_QUEUE_DIR': '${work.path}/queue',
    'FMCG_ATTACHMENTS_DIR': '${work.path}/recipe_files',
    'CORS_ORIGINS': 'http://localhost',
    'LOG_LEVEL': 'WARNING',
  };

  Future<Map<String, dynamic>> seed() async {
    final r = await Process.run(_python, ['${Directory.current.path}/test/e2e/seed_backend.py'],
        environment: env, workingDirectory: _backendDir);
    if (r.exitCode != 0) throw StateError('seed failed: ${r.stderr}');
    final line = '${r.stdout}'.trim().split('\n').last;
    return Map<String, dynamic>.from(jsonDecode(line) as Map);
  }

  Future<void> start() async {
    _process = await Process.start(
      _python,
      ['-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', '$_port'],
      environment: env,
      workingDirectory: _backendDir,
    );
    _process!.stdout.drain<void>();
    _process!.stderr.drain<void>();
    final client = HttpClient();
    for (var i = 0; i < 100; i++) {
      try {
        final req = await client.getUrl(Uri.parse('http://127.0.0.1:$_port/health'));
        final res = await req.close();
        await res.drain<void>();
        if (res.statusCode == 200) return;
      } catch (_) {}
      await Future<void>.delayed(const Duration(milliseconds: 200));
    }
    throw StateError('backend did not start');
  }

  Future<void> stop() async {
    final p = _process;
    _process = null;
    if (p == null) return;
    p.kill(ProcessSignal.sigkill);
    await p.exitCode;
  }

  /// Runs SQL on the backend's database and returns the rows.
  Future<List<dynamic>> query(String sql) async {
    final script = 'import json,sqlite3,sys; c=sqlite3.connect(sys.argv[1]); '
        'print(json.dumps(c.execute(sys.argv[2]).fetchall()))';
    final r = await Process.run(_python, ['-c', script, '${work.path}/backend.db', sql]);
    if (r.exitCode != 0) throw StateError('query failed: ${r.stderr}');
    return jsonDecode('${r.stdout}'.trim()) as List;
  }
}

/// The real API, with a hook that runs before each recording piece is sent.
class _HookedApi extends SurveyApi {
  Future<void> Function(int piece)? beforePiece;
  int pieces = 0;

  @override
  Future<Map<String, dynamic>> putUploadPiece(String uploadId, int offset, List<int> bytes) async {
    pieces++;
    await beforePiece?.call(pieces);
    return super.putUploadPiece(uploadId, offset, bytes);
  }
}

Future<String> _sha256(File f) async {
  final hash = await Sha256().hash(await f.readAsBytes());
  return hash.bytes.map((b) => b.toRadixString(16).padLeft(2, '0')).join();
}

Future<void> _waitUntil(bool Function() done, {Duration timeout = const Duration(seconds: 60)}) async {
  final end = DateTime.now().add(timeout);
  while (!done()) {
    if (DateTime.now().isAfter(end)) throw StateError('timed out');
    await Future<void>.delayed(const Duration(milliseconds: 100));
  }
}

void main() {
  final enabled = Platform.environment['RUN_E2E'] == '1' && ApiConfig.baseUrl == 'http://127.0.0.1:$_port';

  test('offline survey collection syncs to the real backend exactly once', () async {
    final phone = await setUpPhone();
    final backend = _Backend(await Directory.systemTemp.createTemp('fmcg-e2e-backend-'));
    final api = _HookedApi();
    SurveyQueue.api = api;
    OfflineCatalog.api = api;
    OfflineAuth.iterations = 2000;
    try {
      final ids = await backend.seed();
      await backend.start();

      // 1-2. Online: sign in and download the assigned surveys.
      await Session.login('9800000001', 'Field#2026');
      expect(Session.hasServerSession, isTrue);
      expect(Session.offlineMode.value, isFalse);
      final download = await OfflineCatalog.downloadAll();
      expect(download.products, 1);

      // 3-5. Close the app, lose the internet, open the app again.
      await Session.logout(); // also ends the token on the server
      await restartApp();
      await backend.stop();
      ConnectivityService.online.value = false;

      // 6. Offline sign-in with the verified credentials (wrong password refused first).
      await expectLater(Session.login('9800000001', 'wrong-password'), throwsA(contains('incorrect')));
      await Session.login('9800000001', 'Field#2026');
      expect(Session.offlineMode.value, isTrue);
      expect(Session.user.value!.id, ids['user_id']);

      // 7. Downloaded assignment and its questions, without a server.
      final products = await OfflineCatalog.products();
      final product = products.single;
      expect(product.id, ids['product_id']);
      final questions = await OfflineCatalog.questions(product.id);
      expect(questions, hasLength(5));

      // 8-12. Register two consumers, answer, record audio and video, save.
      final answers = [
        for (final q in questions)
          {'question_id': q.id, 'value': q.type == 'rating' ? '4' : (q.type == 'yes_no' ? 'Yes' : 'Less sugar')},
      ];
      final audio = await recording(phone, 'audio.m4a', 5 * 1024 * 1024 + 17);
      final video = await recording(phone, 'video.mp4', 9 * 1024 * 1024 + 5);
      final audioHash = await _sha256(audio);
      final videoHash = await _sha256(video);
      for (final (file, kind, who) in [(audio, 'audio', 'Asha Verma'), (video, 'video', 'Rohit Nair')]) {
        await SurveyQueue.add(
          productId: product.id,
          productName: product.name,
          categoryName: product.categoryName,
          respondent: {'name': who, 'mobile': '9811111111', 'location': 'Pune', 'consent_given': true},
          response: {'product_id': product.id, 'executive_name': 'Field Surveyor', 'location': 'Market'},
          answers: answers,
          mediaSourcePath: file.path,
          mediaName: kind == 'video' ? 'feedback.mp4' : 'feedback.m4a',
          mediaKind: kind,
          durationSeconds: 60,
        );
      }
      expect(SurveyQueue.counts.pending, 2);

      // 13-14. Close and reopen the app, still offline: everything is still there.
      await restartApp();
      Session.user.value = null;
      Session.offlineMode.value = false;
      await Session.load();
      expect(Session.offlineMode.value, isTrue);
      await SurveyQueue.load(startUploads: false);
      expect(SurveyQueue.items.value, hasLength(2));
      expect(SurveyQueue.items.value.every((s) => s.respondent != null && File(s.mediaPath!).existsSync()), isTrue);

      // 15-16. Internet back. After the restart the app asks for the password once.
      // The backend will go down in the middle of the first recording (a real dropped connection).
      api.beforePiece = (n) async {
        if (n == 2) await backend.stop();
      };
      await backend.start();
      ConnectivityService.onReconnect = () async {
        if (Session.offlineMode.value) await Session.restoreOnline();
        await SurveyQueue.process();
      };
      ConnectivityService.update([ConnectivityResult.wifi]);
      await _waitUntil(() => Session.needsOnlineSignIn.value);
      await Session.reauthenticate('Field#2026');
      expect(Session.hasServerSession, isTrue);

      await SurveyQueue.syncNow(); // (or waits for the sync the reconnection already started)
      final interrupted = SurveyQueue.items.value.firstWhere((s) => s.uploadedBytes > 0);
      expect(interrupted.status, SurveyStatus.waiting);
      expect(interrupted.uploadedBytes, SurveyQueue.pieceBytes); // what the server confirmed

      // Back again: the upload continues from the server's offset.
      api.beforePiece = null;
      await backend.start();
      final piecesBefore = api.pieces;
      await SurveyQueue.syncNow();
      expect(SurveyQueue.items.value.every((s) => s.isSynced), isTrue);
      // 5 MB audio = 3 pieces, 9 MB video = 5 pieces; one audio piece was already on the server.
      expect(api.pieces - piecesBefore, (3 - 1) + 5);
      expect(SurveyQueue.counts.synced, 2);
      expect(SurveyQueue.counts.pending + SurveyQueue.counts.failed, 0);

      // 17-18. On the backend: each record exactly once, recordings byte for byte.
      final uuids = SurveyQueue.items.value.map((s) => s.clientUuid).toSet();
      final respondents = await backend.query('SELECT client_uuid, created_by FROM respondents');
      expect(respondents.map((r) => r[0]).toSet(), uuids);
      expect(respondents, hasLength(2));
      expect(respondents.every((r) => r[1] == ids['user_id']), isTrue);
      final responses = await backend.query('SELECT client_uuid, user_id, status, product_id FROM survey_responses');
      expect(responses, hasLength(2));
      expect(responses.map((r) => r[0]).toSet(), uuids);
      expect(responses.every((r) => r[1] == ids['user_id'] && r[2] == 'submitted' && r[3] == ids['product_id']), isTrue);
      final answerRows = await backend.query('SELECT COUNT(*) FROM survey_answers');
      expect(answerRows.single[0], 10); // 2 surveys x 5 answers
      final jobs = await backend.query('SELECT stored_path, response_id FROM analysis_jobs');
      expect(jobs, hasLength(2));
      final hashes = <String>{};
      for (final j in jobs) {
        final file = File('${backend.work.path}/queue/${'${j[0]}'.split('/').last}');
        hashes.add(await _sha256(file));
      }
      expect(hashes, {audioHash, videoHash});

      // Syncing again (or a stale retry) creates nothing new.
      await SurveyQueue.syncNow();
      expect(await backend.query('SELECT COUNT(*) FROM respondents'), [
        [2],
      ]);
      expect(await backend.query('SELECT COUNT(*) FROM survey_responses'), [
        [2],
      ]);
      expect(await backend.query('SELECT COUNT(*) FROM analysis_jobs'), [
        [2],
      ]);
    } finally {
      await backend.stop();
      await tearDownPhone(phone);
      try {
        await backend.work.delete(recursive: true);
      } catch (_) {}
    }
  }, skip: enabled ? false : 'set RUN_E2E=1 and --dart-define=API_BASE_URL=http://127.0.0.1:$_port', timeout: const Timeout(Duration(minutes: 5)));
}
