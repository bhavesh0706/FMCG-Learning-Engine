// Shared setup for the offline-mode tests: an unencrypted SQLite database on the
// computer (sqflite_common_ffi) in place of SQLCipher, an in-memory secure store,
// temporary folders for app storage, and a fake server for the survey queue.

import 'dart:io';
import 'dart:typed_data';

import 'package:dio/dio.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:mobile/services/connectivity_service.dart';
import 'package:mobile/services/local_db.dart';
import 'package:mobile/services/session.dart';
import 'package:mobile/services/survey_api.dart';
import 'package:mobile/services/survey_queue.dart';
import 'package:path_provider_platform_interface/path_provider_platform_interface.dart';
import 'package:plugin_platform_interface/plugin_platform_interface.dart';
import 'package:sqflite_common_ffi/sqflite_ffi.dart' as ffi;

class FakePathProvider extends PathProviderPlatform with MockPlatformInterfaceMixin {
  FakePathProvider(this.root);

  final Directory root;

  Future<String> _dir(String name) async {
    final d = Directory('${root.path}/$name');
    if (!await d.exists()) await d.create(recursive: true);
    return d.path;
  }

  @override
  Future<String?> getApplicationSupportPath() => _dir('support');

  @override
  Future<String?> getApplicationDocumentsPath() => _dir('documents');

  @override
  Future<String?> getTemporaryPath() => _dir('tmp');
}

/// A fresh "phone" for one test. Returns its storage folder.
Future<Directory> setUpPhone() async {
  TestWidgetsFlutterBinding.ensureInitialized();
  HttpOverrides.global = null; // real sockets (requests to a closed port fail as "no connection")
  ffi.sqfliteFfiInit();
  final root = await Directory.systemTemp.createTemp('fmcg-offline-test-');
  PathProviderPlatform.instance = FakePathProvider(root);
  FlutterSecureStorage.setMockInitialValues({});
  LocalDb.pathProvider = () async => '${root.path}/fmcg_offline.db';
  LocalDb.opener =
      (
        String path, {
        required String password,
        required int version,
        required ffi.OnDatabaseCreateFn onCreate,
        required ffi.OnDatabaseVersionChangeFn onUpgrade,
      }) => ffi.databaseFactoryFfi.openDatabase(
        path,
        options: ffi.OpenDatabaseOptions(version: version, onCreate: onCreate, onUpgrade: onUpgrade),
      );
  SurveyQueue.mediaDir = () async {
    final d = Directory('${root.path}/media');
    if (!await d.exists()) await d.create(recursive: true);
    return d;
  };
  SurveyQueue.legacyDir = () async => Directory('${root.path}/documents/surveys');
  ConnectivityService.online.value = true;
  ConnectivityService.onReconnect = null;
  return root;
}

Future<void> tearDownPhone(Directory root) async {
  await SurveyQueue.idle();
  SurveyQueue.resetForTest();
  await LocalDb.close();
  await Session.logout(endOnServer: false); // also forgets an offline session's in-memory password
  try {
    await root.delete(recursive: true);
  } catch (_) {}
}

/// Simulates closing the app and opening it again: everything in memory is forgotten,
/// only what was saved on the phone remains.
Future<void> restartApp() async {
  await SurveyQueue.idle();
  SurveyQueue.resetForTest();
  await LocalDb.close();
}

void signInAs(int id, {String role = 'executive', bool withServerToken = true}) {
  Session.user.value = SessionUser(id: id, name: 'Surveyor $id', role: role, mobile: '98765432${id.toString().padLeft(2, '0')}');
  Session.token = withServerToken ? 'token-$id' : null;
}

Future<File> recording(Directory root, String name, int bytes) async {
  final f = File('${root.path}/$name');
  final data = Uint8List(bytes);
  for (var i = 0; i < bytes; i++) {
    data[i] = (i * 31 + 7) & 0xff;
  }
  await f.writeAsBytes(data);
  return f;
}

DioException connectionLost(String path) =>
    DioException(requestOptions: RequestOptions(path: path), type: DioExceptionType.connectionError);

DioException httpError(String path, int status, [Object? data]) {
  final options = RequestOptions(path: path);
  return DioException(
    requestOptions: options,
    response: Response(requestOptions: options, statusCode: status, data: data ?? {'detail': 'Error $status'}),
    type: DioExceptionType.badResponse,
  );
}

/// The server as the queue sees it, with the same idempotency rules as the backend:
/// one record per client_uuid, uploads continue from received bytes, one job per upload.
class FakeServer extends SurveyApi {
  final respondents = <String, int>{}; // client_uuid -> id
  final responses = <String, int>{};
  final responseBodies = <String, Map<String, dynamic>>{}; // client_uuid -> what the phone sent
  final answers = <int, List<Map<String, dynamic>>>{};
  final submitted = <int>{};
  final uploads = <String, BytesBuilder>{};
  final encryptedUploads = <String, String>{}; // upload id -> wrapped key (encrypted recordings)
  final uploadTotals = <String, int>{};
  final uploadJobs = <String, int>{};
  final jobFiles = <int, Uint8List>{};
  final calls = <String>[];
  int _nextId = 100;

  /// Hooks to inject failures: return an exception to throw (or null to proceed).
  /// [beforeWrite] runs before the server changes anything, [afterWrite] after
  /// (the reply is "lost": the server did the work, the phone never heard back).
  Object? Function(String call)? beforeWrite;
  Object? Function(String call)? afterWrite;

  void _check(Object? Function(String)? hook, String call) {
    final e = hook?.call(call);
    if (e != null) throw e;
  }

  @override
  Future<int> createRespondent(Map<String, dynamic> respondent) async {
    calls.add('respondent');
    _check(beforeWrite, 'respondent');
    final id = respondents.putIfAbsent('${respondent['client_uuid']}', () => _nextId++);
    _check(afterWrite, 'respondent');
    return id;
  }

  @override
  Future<int> createResponse(Map<String, dynamic> response) async {
    calls.add('response');
    _check(beforeWrite, 'response');
    final id = responses.putIfAbsent('${response['client_uuid']}', () => _nextId++);
    responseBodies['${response['client_uuid']}'] = Map<String, dynamic>.from(response);
    _check(afterWrite, 'response');
    return id;
  }

  @override
  Future<void> claimResponse(int responseId) async {}

  @override
  Future<void> saveAnswers(int responseId, List<Map<String, dynamic>> list) async {
    calls.add('answers');
    _check(beforeWrite, 'answers');
    answers[responseId] = list;
  }

  @override
  Future<void> submit(int responseId) async {
    calls.add('submit');
    _check(beforeWrite, 'submit');
    submitted.add(responseId);
  }

  Map<String, dynamic> _state(String id) => {
    'upload_id': id,
    'received_bytes': uploads[id]!.length,
    'total_bytes': uploadTotals[id],
    'status': uploadJobs.containsKey(id) ? 'complete' : 'receiving',
    'job': uploadJobs.containsKey(id) ? {'job_id': uploadJobs[id], 'status': 'queued'} : null,
  };

  @override
  Future<Map<String, dynamic>> startResumableUpload({
    required String uploadId,
    required String filename,
    required int totalBytes,
    int? responseId,
    String? sha256,
    int? keyId,
    String? wrappedKey,
  }) async {
    calls.add('start-upload');
    if (wrappedKey != null) encryptedUploads[uploadId] = wrappedKey;
    _check(beforeWrite, 'start-upload');
    uploads.putIfAbsent(uploadId, BytesBuilder.new);
    uploadTotals[uploadId] = totalBytes;
    return _state(uploadId);
  }

  @override
  Future<Map<String, dynamic>> putUploadPiece(String uploadId, int offset, List<int> bytes) async {
    calls.add('piece@$offset');
    _check(beforeWrite, 'piece');
    final have = uploads[uploadId]!;
    if (offset > have.length) {
      throw httpError('/piece', 409, {
        'detail': {'received_bytes': have.length},
      });
    }
    final skip = have.length - offset;
    if (skip < bytes.length) have.add(bytes.sublist(skip));
    _check(afterWrite, 'piece');
    return _state(uploadId);
  }

  @override
  Future<Map<String, dynamic>> completeResumableUpload(String uploadId) async {
    calls.add('complete');
    _check(beforeWrite, 'complete');
    final have = uploads[uploadId]!;
    if (have.length != uploadTotals[uploadId]) {
      throw httpError('/complete', 409, {
        'detail': {'received_bytes': have.length},
      });
    }
    if (!uploadJobs.containsKey(uploadId)) {
      final job = _nextId++;
      uploadJobs[uploadId] = job;
      jobFiles[job] = have.toBytes();
    }
    _check(afterWrite, 'complete');
    return _state(uploadId);
  }

  /// Analysis status returned while following jobs.
  String jobStatus = 'done';

  @override
  Future<Map<String, dynamic>> analysisJob(int jobId) async {
    calls.add('poll');
    return {
      'job_id': jobId,
      'status': jobStatus,
      'result': {
        'feedback_id': jobId + 1000,
        'overall_rating': 4.0,
        'summary': {'summary': 'Liked it', 'sentiment': 'positive'},
      },
    };
  }

  @override
  Future<Set<int>> existingSurveys(List<int> ids) async => ids.toSet();
}

Future<QueuedSurvey> collectSurvey(Directory root,
    {int bytes = 5 * 1024 * 1024 + 123, String name = 'Priya Sharma', Map<String, dynamic> extraResponse = const {}}) async {
  _name = name;
  _extraResponse = extraResponse;
  final media = await recording(root, 'rec-${DateTime.now().microsecondsSinceEpoch}.m4a', bytes);
  // Collected as if offline: no background upload starts, so each test decides when to sync.
  final wasOnline = ConnectivityService.online.value;
  ConnectivityService.online.value = false;
  try {
    return await _add(media);
  } finally {
    await SurveyQueue.idle();
    ConnectivityService.online.value = wasOnline;
  }
}

Future<QueuedSurvey> _add(File media) {
  return SurveyQueue.add(
    productId: 1,
    productName: "Kellogg's Corn Flakes",
    categoryName: 'Cereal',
    respondent: {'name': _name, 'mobile': '9876543210', 'location': 'Pune', 'consent_given': true},
    response: {'product_id': 1, 'executive_name': 'Surveyor', 'location': 'Market', ..._extraResponse},
    answers: [
      {'question_id': 1, 'value': '5'},
      {'question_id': 2, 'value': 'Crunchy'},
    ],
    mediaSourcePath: media.path,
    mediaName: 'feedback.m4a',
    mediaKind: 'audio',
    durationSeconds: 42,
  );
}

String _name = '';
Map<String, dynamic> _extraResponse = const {};
