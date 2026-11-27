// Offline mode for survey persons: offline sign-in, encrypted local storage,
// surviving restarts, and synchronisation without duplicates.
//
// Run:  flutter test --dart-define=API_BASE_URL=http://127.0.0.1:8765
// (a port where nothing listens: the server is "unreachable" for the sign-in tests)

import 'dart:convert';
import 'dart:io';

import 'package:connectivity_plus/connectivity_plus.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:mobile/services/api_service.dart';
import 'package:mobile/services/connectivity_service.dart';
import 'package:mobile/services/local_db.dart';
import 'package:mobile/services/offline_auth.dart';
import 'package:mobile/services/session.dart';
import 'package:mobile/services/survey_queue.dart';

import 'offline_test_support.dart';

const _executive = {'id': 7, 'name': 'Ishaan Yadav', 'role': 'executive', 'mobile': '9876543210'};

void main() {
  late Directory root;
  late FakeServer server;

  setUp(() async {
    root = await setUpPhone();
    server = FakeServer();
    SurveyQueue.api = server;
    OfflineAuth.iterations = 2000; // fast in tests; the app uses 100,000
    OfflineAuth.now = DateTime.now;
  });

  tearDown(() => tearDownPhone(root));

  // ------------------------------------------------------------------ offline sign-in
  group('Offline sign-in verifier', () {
    test('a survey person verified online can sign in offline with the same password', () async {
      await OfflineAuth.remember(identifier: '9876543210', password: 'S3cret!pw', user: _executive);
      final user = await OfflineAuth.verify('+91 98765 43210', 'S3cret!pw'); // same number, other format
      expect(user['id'], 7);
      expect(user['role'], 'executive');
    });

    test('the password itself is never stored', () async {
      await OfflineAuth.remember(identifier: 'a@b.com', password: 'S3cret!pw', user: _executive);
      final stored = await const FlutterSecureStorage().readAll();
      expect(stored.values.join(), isNot(contains('S3cret!pw')));
      final record = jsonDecode(stored.values.single) as Map;
      expect(record['iterations'], 2000);
      expect(base64Decode(record['salt'] as String), hasLength(16));
    });

    test('first-time sign-in without internet is refused', () async {
      expect(
        () => OfflineAuth.verify('9876543210', 'whatever'),
        throwsA(isA<OfflineLoginError>().having((e) => e.problem, 'problem', OfflineLoginProblem.neverVerified)),
      );
    });

    test('wrong passwords are refused and lock offline sign-in after 5 tries', () async {
      await OfflineAuth.remember(identifier: '9876543210', password: 'right', user: _executive);
      for (var i = 0; i < 4; i++) {
        await expectLater(
          OfflineAuth.verify('9876543210', 'wrong'),
          throwsA(isA<OfflineLoginError>().having((e) => e.problem, 'p', OfflineLoginProblem.wrongPassword)),
        );
      }
      await expectLater(
        OfflineAuth.verify('9876543210', 'wrong'),
        throwsA(isA<OfflineLoginError>().having((e) => e.problem, 'p', OfflineLoginProblem.locked)),
      );
      // Even the right password no longer works offline; an online sign-in resets it.
      await expectLater(
        OfflineAuth.verify('9876543210', 'right'),
        throwsA(isA<OfflineLoginError>().having((e) => e.problem, 'p', OfflineLoginProblem.locked)),
      );
      await OfflineAuth.remember(identifier: '9876543210', password: 'right', user: _executive);
      expect((await OfflineAuth.verify('9876543210', 'right'))['id'], 7);
    });

    test('offline authorisation expires after the allowed days without an online sign-in', () async {
      final start = DateTime.utc(2026, 11, 1);
      OfflineAuth.now = () => start;
      await OfflineAuth.remember(identifier: '9876543210', password: 'pw', user: _executive);
      OfflineAuth.now = () => start.add(OfflineAuth.maxAge - const Duration(minutes: 1));
      expect((await OfflineAuth.verify('9876543210', 'pw'))['id'], 7);
      expect(await OfflineAuth.stillValid('9876543210', 7), isTrue);
      OfflineAuth.now = () => start.add(OfflineAuth.maxAge + const Duration(minutes: 1));
      await expectLater(
        OfflineAuth.verify('9876543210', 'pw'),
        throwsA(isA<OfflineLoginError>().having((e) => e.problem, 'p', OfflineLoginProblem.expired)),
      );
      expect(await OfflineAuth.stillValid('9876543210', 7), isFalse);
    });

    test('admins and the organization never get offline access', () async {
      await OfflineAuth.remember(
        identifier: 'admin@x.com',
        password: 'pw',
        user: {'id': 1, 'name': 'Admin', 'role': 'admin'},
      );
      await expectLater(
        OfflineAuth.verify('admin@x.com', 'pw'),
        throwsA(isA<OfflineLoginError>().having((e) => e.problem, 'p', OfflineLoginProblem.neverVerified)),
      );
    });
  });

  group('Session sign-in without a server', () {
    final serverUnreachable = ApiConfig.baseUrl.startsWith('http://127.0.0.1:');

    test('verified survey person signs in offline; restart keeps the offline session', () async {
      await OfflineAuth.remember(identifier: '9876543210', password: 'pw123456', user: _executive);
      await Session.login('9876543210', 'pw123456');
      expect(Session.signedIn, isTrue);
      expect(Session.offlineMode.value, isTrue);
      expect(Session.hasServerSession, isFalse); // nothing uploads without a server token
      expect(Session.isExecutive, isTrue);
      expect(Session.user.value!.id, 7);

      // Close and reopen the app, still offline.
      Session.user.value = null;
      Session.offlineMode.value = false;
      await Session.load();
      expect(Session.signedIn, isTrue);
      expect(Session.offlineMode.value, isTrue);
      expect(Session.needsOnlineSignIn.value, isTrue); // the password is asked again to upload
    }, skip: serverUnreachable ? false : 'needs --dart-define=API_BASE_URL=http://127.0.0.1:<closed port>');

    test('wrong password and first-time accounts are refused offline', () async {
      await OfflineAuth.remember(identifier: '9876543210', password: 'pw123456', user: _executive);
      await expectLater(Session.login('9876543210', 'nope'), throwsA(contains('incorrect')));
      await expectLater(Session.login('9123456789', 'pw123456'), throwsA(contains("hasn't signed in on this phone")));
      expect(Session.signedIn, isFalse);
    }, skip: serverUnreachable ? false : 'needs --dart-define=API_BASE_URL=http://127.0.0.1:<closed port>');

    test('an expired offline session is not reopened after a restart', () async {
      final start = DateTime.now().toUtc();
      OfflineAuth.now = () => start;
      await OfflineAuth.remember(identifier: '9876543210', password: 'pw123456', user: _executive);
      await Session.login('9876543210', 'pw123456');
      Session.user.value = null;
      Session.offlineMode.value = false;
      OfflineAuth.now = () => start.add(OfflineAuth.maxAge + const Duration(hours: 1));
      await Session.load();
      expect(Session.signedIn, isFalse);
    }, skip: serverUnreachable ? false : 'needs --dart-define=API_BASE_URL=http://127.0.0.1:<closed port>');
  });

  // ------------------------------------------------------------------ local storage
  group('Local storage', () {
    test('the database key is random, kept in the secure store, and reused after a restart', () async {
      await LocalDb.instance;
      final key = (await const FlutterSecureStorage().readAll())['fmcg.localdb.key'];
      expect(key, isNotNull);
      expect(base64Url.decode(key!), hasLength(32));
      await restartApp();
      await LocalDb.instance;
      expect((await const FlutterSecureStorage().readAll())['fmcg.localdb.key'], key);
    });

    test('a survey collected offline survives closing the app and is complete', () async {
      signInAs(7, withServerToken: false);
      ConnectivityService.online.value = false;
      final s = await collectSurvey(root);
      expect(s.status, SurveyStatus.waiting);
      expect(server.calls, isEmpty); // nothing was sent

      await restartApp();
      signInAs(7, withServerToken: false);
      await SurveyQueue.load(startUploads: false);
      final again = SurveyQueue.items.value.single;
      expect(again.id, s.id);
      expect(again.clientUuid, s.clientUuid);
      expect(again.respondent!['name'], 'Priya Sharma');
      expect(again.respondent!['mobile'], '9876543210');
      expect(again.answers, hasLength(2));
      expect(again.ownerUserId, 7);
      expect(again.status, SurveyStatus.waiting);
      expect(await File(again.mediaPath!).length(), 5 * 1024 * 1024 + 123);
      expect(again.mediaPath, startsWith('${root.path}/media/')); // app-private folder
    });

    test('an upload interrupted by closing the app resumes as pending', () async {
      signInAs(7);
      final s = await collectSurvey(root);
      await SurveyQueue.load(startUploads: false);
      // Pretend the app was killed while uploading.
      server.beforeWrite = (call) => call == 'piece' ? connectionLost('/piece') : null;
      await SurveyQueue.process();
      await restartApp();
      signInAs(7);
      await SurveyQueue.load(startUploads: false);
      expect(SurveyQueue.items.value.single.id, s.id);
      expect(SurveyQueue.items.value.single.status, SurveyStatus.waiting);
    });

    test('surveys from an older app version move into the encrypted database once', () async {
      signInAs(7);
      final legacy = Directory('${root.path}/documents/surveys');
      await legacy.create(recursive: true);
      final media = await recording(root, 'old.m4a', 1000);
      final old = QueuedSurvey(
        id: '1700000000000000',
        createdAt: DateTime(2026, 11, 1),
        productId: 1,
        productName: 'Old product',
        categoryName: 'Cat',
        respondentLabel: 'Ravi K.',
        area: 'Pune',
        respondent: {'name': 'Ravi Kumar', 'consent_given': true},
        response: {'product_id': 1},
        answers: const [],
        mediaPath: media.path,
        mediaName: 'old.m4a',
        mediaKind: 'audio',
        durationSeconds: 30,
      ).toJson()..remove('ownerUserId')..remove('clientUuid');
      final index = File('${legacy.path}/index.json');
      await index.writeAsString(jsonEncode([old]));

      await SurveyQueue.load(startUploads: false);
      final moved = SurveyQueue.items.value.single;
      expect(moved.id, '1700000000000000');
      expect(moved.ownerUserId, 7);
      expect(moved.clientUuid, hasLength(36));
      expect(await index.exists(), isFalse); // the unencrypted copy with personal data is gone
    });

    test('each survey person sees and uploads only their own surveys', () async {
      signInAs(7);
      ConnectivityService.online.value = false;
      await collectSurvey(root, name: 'Customer Of Seven');
      signInAs(8);
      await Future<void>.delayed(Duration.zero);
      expect(SurveyQueue.items.value, isEmpty);
      await collectSurvey(root, name: 'Customer Of Eight', bytes: 1000);
      ConnectivityService.online.value = true;
      await SurveyQueue.process();
      expect(server.respondents, hasLength(1)); // only person 8's survey was sent with person 8's token
      signInAs(7);
      await Future<void>.delayed(Duration.zero);
      expect(SurveyQueue.items.value.single.respondentLabel, 'Customer S.'); // "Customer Of Seven"
      expect(SurveyQueue.items.value.single.status, SurveyStatus.waiting);
    });
  });

  // ------------------------------------------------------------------ synchronisation
  group('Synchronisation', () {
    test('pending surveys upload when the connection returns, with every part', () async {
      signInAs(7);
      ConnectivityService.online.value = false;
      final s = await collectSurvey(root);
      await SurveyQueue.process();
      expect(server.calls, isEmpty);

      ConnectivityService.onReconnect = () => SurveyQueue.process();
      ConnectivityService.update([ConnectivityResult.none]); // still no network
      expect(server.calls, isEmpty);
      ConnectivityService.update([ConnectivityResult.wifi]); // back online: syncs by itself
      await _settle();

      final done = SurveyQueue.items.value.single;
      expect(done.status, SurveyStatus.analyzed);
      expect(done.syncedAt, isNotNull);
      expect(server.respondents.keys.single, s.clientUuid);
      expect(server.responses.keys.single, s.clientUuid);
      expect(server.answers.values.single, hasLength(2));
      expect(server.submitted, hasLength(1));
      // The recording arrived byte for byte, in 2 MB pieces.
      final original = await File('${root.path}/media/${s.id}.m4a').exists();
      expect(original, isFalse); // deleted from the phone only after the server confirmed it
      expect(server.jobFiles.values.single.length, 5 * 1024 * 1024 + 123);
      expect(server.calls.where((c) => c.startsWith('piece@')), hasLength(3));
      // Personal details leave the phone after the analysis; the short label stays.
      expect(done.respondent, isNull);
      expect(done.respondentLabel, 'Priya S.');
    });

    test('a network drop in the middle of the recording resumes from the server offset', () async {
      signInAs(7);
      final s = await collectSurvey(root);
      final bytes = await File(s.mediaPath!).readAsBytes();
      var pieces = 0;
      server.beforeWrite = (call) => call == 'piece' && ++pieces == 2 ? connectionLost('/piece') : null;
      await SurveyQueue.process();

      var now = SurveyQueue.items.value.single;
      expect(now.status, SurveyStatus.waiting);
      expect(now.uploadedBytes, SurveyQueue.pieceBytes); // first piece confirmed and remembered

      server.beforeWrite = null;
      await restartApp(); // the app was even closed in between
      signInAs(7);
      await SurveyQueue.load(startUploads: false);
      await SurveyQueue.process();
      now = SurveyQueue.items.value.single;
      expect(now.isSynced, isTrue);
      expect(server.jobFiles.values.single, bytes);
      // Resumed at 2 MB; the first piece was not sent again.
      expect(server.calls.where((c) => c == 'piece@0'), hasLength(1));
      expect(server.respondents, hasLength(1));
      expect(server.responses, hasLength(1));
    });

    test('lost replies never create duplicates', () async {
      signInAs(7);
      await collectSurvey(root);
      // The server saves each record but the phone never hears back (once per step).
      final lost = <String>{};
      server.afterWrite = (call) => lost.add(call) ? connectionLost('/$call') : null;
      for (var i = 0; i < 10 && !SurveyQueue.items.value.single.isSynced; i++) {
        await SurveyQueue.process();
      }
      expect(SurveyQueue.items.value.single.isSynced, isTrue);
      expect(lost, containsAll(['respondent', 'response', 'piece', 'complete']));
      expect(server.respondents, hasLength(1));
      expect(server.responses, hasLength(1));
      expect(server.uploadJobs, hasLength(1));
      expect(server.jobFiles.values.single.length, 5 * 1024 * 1024 + 123);
    });

    test('several pending surveys upload oldest first', () async {
      signInAs(7);
      ConnectivityService.online.value = false;
      final a = await collectSurvey(root, name: 'First Person', bytes: 2000);
      final b = await collectSurvey(root, name: 'Second Person', bytes: 3000);
      final c = await collectSurvey(root, name: 'Third Person', bytes: 4000);
      expect(SurveyQueue.counts.pending, 3);
      ConnectivityService.online.value = true;
      await SurveyQueue.process();
      expect(server.respondents.keys.toList(), [a.clientUuid, b.clientUuid, c.clientUuid]);
      expect(SurveyQueue.counts.synced, 3);
      expect(SurveyQueue.counts.pending, 0);
    });

    test('server errors retry automatically with a pause; Sync now retries at once', () async {
      signInAs(7);
      await collectSurvey(root, bytes: 1000);
      server.beforeWrite = (call) => call == 'respondent' ? httpError('/respondents', 503) : null;
      await SurveyQueue.process();
      var s = SurveyQueue.items.value.single;
      expect(s.status, SurveyStatus.waiting);
      expect(s.attempts, 1);
      expect(s.nextAttemptAt, isNotNull);

      server.beforeWrite = null;
      await SurveyQueue.process(); // too early: the pause isn't over
      expect(SurveyQueue.items.value.single.status, SurveyStatus.waiting);
      expect(server.respondents, isEmpty);

      await SurveyQueue.syncNow();
      s = SurveyQueue.items.value.single;
      expect(s.isSynced, isTrue);
    });

    test('a refused survey is marked failed and can be retried without collecting it again', () async {
      signInAs(7);
      await collectSurvey(root, bytes: 1000);
      server.beforeWrite = (call) =>
          call == 'response' ? httpError('/responses', 400, {'detail': 'This round is closed.'}) : null;
      await SurveyQueue.process();
      var s = SurveyQueue.items.value.single;
      expect(s.status, SurveyStatus.failed);
      expect(s.error, 'This round is closed.');
      expect(syncLabel(s.status), 'Upload failed');
      expect(SurveyQueue.counts.failed, 1);

      server.beforeWrite = null;
      await SurveyQueue.retry(s.id);
      s = SurveyQueue.items.value.single;
      expect(s.isSynced, isTrue);
      expect(server.respondents, hasLength(1)); // the customer created before the refusal was reused
    });

    test('an ended session keeps surveys pending instead of failing them', () async {
      signInAs(7);
      await collectSurvey(root, bytes: 1000);
      server.beforeWrite = (call) => httpError('/x', 401);
      await SurveyQueue.process();
      final s = SurveyQueue.items.value.single;
      expect(s.status, SurveyStatus.waiting);
      // The app renews the sign-in by itself (refresh token) and uploads afterwards.
      expect(s.error, contains('Renewing the sign-in'));
    });

    test('nothing is uploaded during an offline session (no server token)', () async {
      signInAs(7, withServerToken: false);
      Session.offlineMode.value = true;
      await collectSurvey(root, bytes: 1000);
      await SurveyQueue.process(); // restoreOnline has no password in this test
      expect(server.calls, isEmpty);
      expect(Session.needsOnlineSignIn.value, isTrue);
    });

    test('a large recording (60 MB video) uploads in pieces and arrives intact', () async {
      signInAs(7);
      final s = await collectSurvey(root, bytes: 60 * 1024 * 1024);
      var pieces = 0;
      var drops = 0;
      // The connection drops three times during the upload.
      server.beforeWrite = (call) {
        if (call != 'piece') return null;
        pieces++;
        if (drops < 3 && pieces % 7 == 0) {
          drops++;
          return connectionLost('/piece');
        }
        return null;
      };
      for (var i = 0; i < 6 && !SurveyQueue.items.value.single.isSynced; i++) {
        await SurveyQueue.process();
      }
      expect(drops, 3);
      expect(SurveyQueue.items.value.single.isSynced, isTrue);
      expect(server.jobFiles.values.single.length, 60 * 1024 * 1024);
      expect(server.uploadJobs, hasLength(1));
      expect(s.mediaBytes, 60 * 1024 * 1024);
    });
  });
}

/// Lets the reconnect callback's uploads finish.
Future<void> _settle() async {
  for (var i = 0; i < 50 && SurveyQueue.syncing.value; i++) {
    await Future<void>.delayed(const Duration(milliseconds: 50));
  }
  for (var i = 0; i < 50 && SurveyQueue.items.value.any((s) => s.needsUpload || s.status == SurveyStatus.uploading); i++) {
    await Future<void>.delayed(const Duration(milliseconds: 50));
  }
  // The fake server finishes the analysis at once; give the queue time to record it.
  for (var i = 0; i < 40 && SurveyQueue.items.value.any((s) => s.status == SurveyStatus.analyzing); i++) {
    await Future<void>.delayed(const Duration(milliseconds: 50));
  }
  await SurveyQueue.idle();
}
