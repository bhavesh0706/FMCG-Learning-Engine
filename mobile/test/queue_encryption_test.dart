// Recordings in the offline queue are encrypted and stay encrypted until the server has them.
//
// Run:  flutter test --dart-define=API_BASE_URL=http://127.0.0.1:8765
import 'dart:convert';
import 'dart:io';
import 'dart:math';
import 'dart:typed_data';

import 'package:flutter_test/flutter_test.dart';
import 'package:mobile/services/connectivity_service.dart';
import 'package:mobile/services/recording_vault.dart';
import 'package:mobile/services/survey_queue.dart';
import 'package:pointycastle/asn1.dart';
import 'package:pointycastle/export.dart';

import 'offline_test_support.dart';

AsymmetricKeyPair<RSAPublicKey, RSAPrivateKey> _pair() {
  final random = FortunaRandom()..seed(KeyParameter(Uint8List.fromList(List.generate(32, (_) => Random.secure().nextInt(256)))));
  final gen = RSAKeyGenerator()..init(ParametersWithRandom(RSAKeyGeneratorParameters(BigInt.from(65537), 2048, 64), random));
  final p = gen.generateKeyPair();
  return AsymmetricKeyPair(p.publicKey as RSAPublicKey, p.privateKey as RSAPrivateKey);
}

String _spki(RSAPublicKey key) {
  final alg = ASN1Sequence()
    ..add(ASN1ObjectIdentifier.fromIdentifierString('1.2.840.113549.1.1.1'))
    ..add(ASN1Null());
  final rsa = ASN1Sequence()
    ..add(ASN1Integer(key.modulus))
    ..add(ASN1Integer(key.exponent));
  return base64Encode((ASN1Sequence()
        ..add(alg)
        ..add(ASN1BitString(stringValues: rsa.encode())))
      .encode());
}

void main() {
  late Directory root;
  late FakeServer server;
  late AsymmetricKeyPair<RSAPublicKey, RSAPrivateKey> pair;

  setUpAll(() => pair = _pair());

  setUp(() async {
    root = await setUpPhone();
    server = FakeServer();
    SurveyQueue.api = server;
    RecordingVault.storageDir = () async => root;
  });

  tearDown(() => tearDownPhone(root));

  Uint8List unwrap(String wrapped) =>
      (OAEPEncoding.withSHA256(RSAEngine())..init(false, PrivateKeyParameter<RSAPrivateKey>(pair.privateKey))).process(base64Decode(wrapped));

  test('an older plaintext recording in the queue is sealed before upload, and only the server can open it', () async {
    signInAs(7);
    ConnectivityService.online.value = false;
    final s = await collectSurvey(root, bytes: 3 * 1024 * 1024 + 5); // queued plaintext (older app version)
    final plainPath = s.mediaPath!;
    final original = File(plainPath).readAsBytesSync();
    await RecordingVault.saveKey({'key_id': 4, 'public_key_spki_b64': _spki(pair.publicKey), 'algorithm': 'x'});

    ConnectivityService.online.value = true;
    await SurveyQueue.process();
    await SurveyQueue.idle();

    expect(File(plainPath).existsSync(), isFalse, reason: 'no plaintext left on the phone');
    final uploadId = server.encryptedUploads.keys.single;
    final wrapped = server.encryptedUploads[uploadId]!;
    final received = server.jobFiles.values.single;
    expect(ascii.decode(received.sublist(0, 8)), 'FMCGREC1', reason: 'the server received only ciphertext');
    expect(RecordingVault.decryptBytes(received, unwrap(wrapped)), original, reason: 'the server can open it');
  });

  test('a sealed recording is moved into the queue (no second copy) and uploads with its wrapped key', () async {
    signInAs(7);
    await RecordingVault.saveKey({'key_id': 4, 'public_key_spki_b64': _spki(pair.publicKey), 'algorithm': 'x'});
    final source = File('${root.path}/rec.m4a')..writeAsBytesSync(List.generate(5000, (i) => i % 256));
    final sealed = await RecordingVault.seal(source.path);
    ConnectivityService.online.value = false;
    final s = await SurveyQueue.add(
      productId: 1,
      productName: 'Masala Peanuts',
      categoryName: 'Snacks',
      respondent: {'name': 'Priya Sharma', 'location': 'Noida', 'consent_given': true},
      response: {'product_id': 1},
      answers: const [],
      mediaSourcePath: sealed.path,
      mediaName: 'feedback.m4a',
      mediaKind: 'audio',
      durationSeconds: 40,
      encKeyId: sealed.keyId,
      encWrappedKey: sealed.wrappedKey,
    );
    await SurveyQueue.idle();
    expect(File(sealed.path).existsSync(), isFalse, reason: 'moved, not copied');
    expect(s.mediaPath, endsWith('.enc'));
    expect(s.isEncrypted, isTrue);
    ConnectivityService.online.value = true;
    await SurveyQueue.process();
    await SurveyQueue.idle();
    expect(server.encryptedUploads.values.single, sealed.wrappedKey);
    expect(RecordingVault.decryptBytes(server.jobFiles.values.single, unwrap(sealed.wrappedKey)), List.generate(5000, (i) => i % 256));
  });
}
