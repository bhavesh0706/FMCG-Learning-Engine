// Encrypted recordings on the phone (services/recording_vault.dart).
import 'dart:convert';
import 'dart:io';
import 'dart:math';
import 'dart:typed_data';

import 'package:flutter_test/flutter_test.dart';
import 'package:mobile/services/recording_vault.dart';
import 'package:pointycastle/asn1.dart';
import 'package:pointycastle/export.dart';

/// A test RSA key pair (the server's would be 3072-bit; 2048 keeps the test fast).
AsymmetricKeyPair<RSAPublicKey, RSAPrivateKey> _keyPair() {
  final random = FortunaRandom()..seed(KeyParameter(Uint8List.fromList(List.generate(32, (_) => Random.secure().nextInt(256)))));
  final gen = RSAKeyGenerator()..init(ParametersWithRandom(RSAKeyGeneratorParameters(BigInt.from(65537), 2048, 64), random));
  final pair = gen.generateKeyPair();
  return AsymmetricKeyPair(pair.publicKey as RSAPublicKey, pair.privateKey as RSAPrivateKey);
}

/// SubjectPublicKeyInfo DER, as the server publishes it.
String _spki(RSAPublicKey key) {
  final algorithm = ASN1Sequence()
    ..add(ASN1ObjectIdentifier.fromIdentifierString('1.2.840.113549.1.1.1'))
    ..add(ASN1Null());
  final rsa = ASN1Sequence()
    ..add(ASN1Integer(key.modulus))
    ..add(ASN1Integer(key.exponent));
  final spki = ASN1Sequence()
    ..add(algorithm)
    ..add(ASN1BitString(stringValues: rsa.encode()));
  return base64Encode(spki.encode());
}

void main() {
  late Directory root;
  late AsymmetricKeyPair<RSAPublicKey, RSAPrivateKey> pair;
  late RecordingPublicKey publicKey;

  setUpAll(() {
    pair = _keyPair();
    publicKey = RecordingPublicKey(keyId: 7, spkiB64: _spki(pair.publicKey), algorithm: 'RSA-OAEP-256/AES-256-GCM/FMCGREC1');
  });

  setUp(() async {
    root = await Directory.systemTemp.createTemp('vault-');
    RecordingVault.storageDir = () async => root;
  });

  tearDown(() => root.delete(recursive: true));

  Uint8List unwrap(String wrapped) {
    final cipher = OAEPEncoding.withSHA256(RSAEngine())..init(false, PrivateKeyParameter<RSAPrivateKey>(pair.privateKey));
    return cipher.process(base64Decode(wrapped));
  }

  test('a recording is sealed into private storage, the plaintext is deleted, and only the private key opens it', () async {
    final plain = Uint8List.fromList(List.generate(recordingChunk * 2 + 777, (i) => (i * 31 + 7) % 256));
    final source = File('${root.path}/rec-123.m4a')..writeAsBytesSync(plain);

    expect(await RecordingVault.cachedKey(), isNull);
    await expectLater(RecordingVault.seal(source.path), throwsA(isA<RecordingKeyMissing>()), reason: 'no key, no recording kept in clear');
    await RecordingVault.saveKey(publicKey.toJson());

    final sealed = await RecordingVault.seal(source.path);
    expect(source.existsSync(), isFalse, reason: 'the plaintext recording is gone');
    expect(sealed.path, startsWith('${root.path}/offline_media/'));
    expect(sealed.path, endsWith('.enc'));
    expect(sealed.keyId, 7);
    final encrypted = File(sealed.path).readAsBytesSync();
    expect(sealed.bytes, encrypted.length);
    expect(ascii.decode(encrypted.sublist(0, 8)), 'FMCGREC1');
    expect(_contains(encrypted, plain.sublist(1000, 1064)), isFalse, reason: 'nothing readable in the file');
    // Three chunks: 8 + 3 x (4 + 12 + 16) + plaintext.
    expect(encrypted.length, 8 + 3 * 32 + plain.length);

    final dataKey = unwrap(sealed.wrappedKey);
    expect(dataKey.length, 32);
    expect(RecordingVault.decryptBytes(encrypted, dataKey), plain);
    // Another key fails (authenticated encryption).
    expect(() => RecordingVault.decryptBytes(encrypted, Uint8List(32)), throwsA(anything));
    // A truncated file fails: the last chunk is missing its "last" mark.
    final cut = encrypted.sublist(0, 8 + 2 * (32 + recordingChunk));
    expect(() => RecordingVault.decryptBytes(cut, dataKey), throwsA(anything));
  });

  test('each recording gets its own data key and file name', () async {
    await RecordingVault.saveKey(publicKey.toJson());
    final a = File('${root.path}/a.m4a')..writeAsBytesSync(utf8.encode('same words'));
    final b = File('${root.path}/b.m4a')..writeAsBytesSync(utf8.encode('same words'));
    final sa = await RecordingVault.seal(a.path);
    final sb = await RecordingVault.seal(b.path);
    expect(sa.path == sb.path, isFalse);
    expect(unwrap(sa.wrappedKey), isNot(equals(unwrap(sb.wrappedKey))));
    expect(File(sa.path).readAsBytesSync(), isNot(equals(File(sb.path).readAsBytesSync())));
  });

  test('a file the survey person chose keeps its original (only the copy is sealed)', () async {
    await RecordingVault.saveKey(publicKey.toJson());
    final chosen = File('${root.path}/my_clip.mp4')..writeAsBytesSync([1, 2, 3]);
    await RecordingVault.seal(chosen.path, deleteSource: false);
    expect(chosen.existsSync(), isTrue);
  });
}

bool _contains(Uint8List hay, Uint8List needle) {
  outer:
  for (var i = 0; i + needle.length <= hay.length; i++) {
    for (var j = 0; j < needle.length; j++) {
      if (hay[i + j] != needle[j]) continue outer;
    }
    return true;
  }
  return false;
}
