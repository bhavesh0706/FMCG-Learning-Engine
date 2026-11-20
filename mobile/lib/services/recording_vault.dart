import 'dart:convert';
import 'dart:io';
import 'dart:isolate';
import 'dart:math';
import 'dart:typed_data';

import 'package:path_provider/path_provider.dart';
import 'package:pointycastle/asn1.dart';
import 'package:pointycastle/export.dart';

/*
  Encrypted recordings on the phone (Android and iOS; the same format as the server's
  backend/app/services/recording_crypto.py and the web app's services/recordingCrypto.js).

  As soon as a recording is finished (or a file is chosen), it is sealed:
    1. a fresh random 256-bit data key, from the platform's secure random source;
    2. the recording encrypted with AES-256-GCM in 1 MiB chunks (FMCGREC1, below), written to
       the app's PRIVATE storage (offline_media/, not the gallery, Downloads or Files);
    3. the data key wrapped with the server's RSA-OAEP-SHA256 PUBLIC key, then wiped.
  The plaintext file is deleted. The app keeps only the encrypted file and the wrapped key:
  it cannot decrypt them (only the server's private key can), so the survey person can't
  play, export or share the recording, and nothing readable sits in the phone's storage.
  It all works offline: the public key is downloaded at sign-in / with the survey data.

  FMCGREC1: "FMCGREC1" | repeated [len(4, BE) | nonce(12) | ciphertext+tag(16)]
            AAD of chunk i = "FMCGREC1" | i (4, BE) | last (1 byte)
*/

const _magic = 'FMCGREC1';
const recordingChunk = 1024 * 1024;

class RecordingPublicKey {
  const RecordingPublicKey({required this.keyId, required this.spkiB64, required this.algorithm});

  final int keyId;
  final String spkiB64;
  final String algorithm;

  Map<String, dynamic> toJson() => {'key_id': keyId, 'public_key_spki_b64': spkiB64, 'algorithm': algorithm};

  static RecordingPublicKey? fromJson(dynamic j) {
    if (j is! Map || j['key_id'] is! num || j['public_key_spki_b64'] is! String) return null;
    return RecordingPublicKey(
      keyId: (j['key_id'] as num).toInt(),
      spkiB64: j['public_key_spki_b64'] as String,
      algorithm: '${j['algorithm'] ?? ''}',
    );
  }
}

/// An encrypted recording: the file (in private storage) and its wrapped data key.
class SealedRecording {
  const SealedRecording({required this.path, required this.keyId, required this.wrappedKey, required this.bytes});

  final String path;
  final int keyId;
  final String wrappedKey;
  final int bytes;
}

class RecordingKeyMissing implements Exception {
  @override
  String toString() =>
      'This phone has no recording security key yet. Connect to the internet once (sign in or download survey data), then record.';
}

class RecordingVault {
  RecordingVault._();

  /// Where the key file and encrypted recordings live (overridable in tests).
  static Future<Directory> Function() storageDir = () async => getApplicationSupportDirectory();

  static Future<File> _keyFile() async => File('${(await storageDir()).path}${Platform.pathSeparator}recording_key.json');

  static Future<Directory> mediaDir() async {
    final dir = Directory('${(await storageDir()).path}${Platform.pathSeparator}offline_media');
    if (!await dir.exists()) await dir.create(recursive: true);
    return dir;
  }

  /// The server's public key, as last downloaded (not secret: it can only encrypt).
  static Future<RecordingPublicKey?> cachedKey() async {
    try {
      final f = await _keyFile();
      if (!await f.exists()) return null;
      return RecordingPublicKey.fromJson(jsonDecode(await f.readAsString()));
    } catch (_) {
      return null;
    }
  }

  /// Saves the key from GET /recordings/public-key.
  static Future<void> saveKey(dynamic json) async {
    final key = RecordingPublicKey.fromJson(json);
    if (key == null) return;
    final f = await _keyFile();
    await f.parent.create(recursive: true);
    await f.writeAsString(jsonEncode(key.toJson()), flush: true);
  }

  /// Downloads the server's public key (GET /recordings/public-key) and keeps it for offline use.
  /// Best effort: a failure keeps the previous key.
  static Future<void> refresh(Future<dynamic> Function(String path) get) async {
    try {
      await saveKey(await get('/recordings/public-key'));
    } catch (_) {
      // offline or an older server: the cached key (if any) stays
    }
  }

  /// Encrypts [sourcePath] into private storage and deletes the plaintext when it's the
  /// app's own (a recording, or a temporary copy of a chosen file). Runs in a background isolate.
  static Future<SealedRecording> seal(String sourcePath, {RecordingPublicKey? key, bool deleteSource = true}) async {
    final pk = key ?? await cachedKey();
    if (pk == null) throw RecordingKeyMissing();
    final dir = await mediaDir();
    final target = '${dir.path}${Platform.pathSeparator}${_randomName()}.enc';
    final result = await Isolate.run(() => sealSync(sourcePath, target, pk));
    if (deleteSource) {
      try {
        await File(sourcePath).delete();
      } catch (_) {
        // already gone, or a file the app may not delete (it was never ours)
      }
    }
    return result;
  }

  /// The work itself (synchronous; called in an isolate).
  static SealedRecording sealSync(String sourcePath, String targetPath, RecordingPublicKey pk) {
    final dataKey = _secureBytes(32);
    try {
      final bytes = encryptFileSync(File(sourcePath), File(targetPath), dataKey);
      final wrapped = wrapKey(dataKey, pk.spkiB64);
      return SealedRecording(path: targetPath, keyId: pk.keyId, wrappedKey: wrapped, bytes: bytes);
    } catch (e) {
      try {
        File(targetPath).deleteSync();
      } catch (_) {}
      rethrow;
    } finally {
      dataKey.fillRange(0, dataKey.length, 0); // the data key is forgotten
    }
  }

  /// FMCGREC1 encryption, chunk by chunk (constant memory). Returns the encrypted size.
  static int encryptFileSync(File source, File target, Uint8List dataKey) {
    final input = source.openSync();
    final tmp = File('${target.path}.part');
    final output = tmp.openSync(mode: FileMode.write);
    try {
      output.writeFromSync(ascii.encode(_magic));
      var index = 0;
      var chunk = input.readSync(recordingChunk);
      while (true) {
        final following = chunk.length == recordingChunk ? input.readSync(recordingChunk) : Uint8List(0);
        final last = following.isEmpty;
        final nonce = _secureBytes(12);
        final sealed = _gcm(true, dataKey, nonce, _aad(index, last), chunk);
        final head = ByteData(4)..setUint32(0, 12 + sealed.length);
        output
          ..writeFromSync(head.buffer.asUint8List())
          ..writeFromSync(nonce)
          ..writeFromSync(sealed);
        index++;
        if (last) break;
        chunk = following;
      }
      output.flushSync();
    } finally {
      input.closeSync();
      output.closeSync();
    }
    tmp.renameSync(target.path);
    return target.lengthSync();
  }

  /// Decryption (tests and diagnostics only: the app never has a data key after sealing).
  static Uint8List decryptBytes(Uint8List encrypted, Uint8List dataKey) {
    if (encrypted.length < 8 || ascii.decode(encrypted.sublist(0, 8)) != _magic) {
      throw const FormatException('Not an FMCGREC1 file');
    }
    final out = BytesBuilder(copy: false);
    var pos = 8;
    var index = 0;
    while (pos < encrypted.length) {
      final len = ByteData.sublistView(encrypted, pos, pos + 4).getUint32(0);
      final body = encrypted.sublist(pos + 4, pos + 4 + len);
      pos += 4 + len;
      final last = pos >= encrypted.length;
      out.add(_gcm(false, dataKey, body.sublist(0, 12), _aad(index, last), body.sublist(12)));
      index++;
    }
    return out.toBytes();
  }

  static Uint8List _gcm(bool encrypt, Uint8List key, Uint8List nonce, Uint8List aad, Uint8List data) {
    final cipher = GCMBlockCipher(AESEngine())..init(encrypt, AEADParameters(KeyParameter(key), 128, nonce, aad));
    return cipher.process(data);
  }

  static Uint8List _aad(int index, bool last) {
    final b = BytesBuilder()
      ..add(ascii.encode(_magic))
      ..add((ByteData(4)..setUint32(0, index)).buffer.asUint8List())
      ..addByte(last ? 1 : 0);
    return b.toBytes();
  }

  /// RSA-OAEP (SHA-256, MGF1-SHA-256) of the data key with the server's public key.
  static String wrapKey(Uint8List dataKey, String spkiB64) {
    final cipher = OAEPEncoding.withSHA256(RSAEngine())..init(true, PublicKeyParameter<RSAPublicKey>(parsePublicKey(spkiB64)));
    return base64Encode(cipher.process(dataKey));
  }

  /// SubjectPublicKeyInfo (DER, base64) -> RSA public key.
  static RSAPublicKey parsePublicKey(String spkiB64) {
    final spki = ASN1Parser(base64Decode(spkiB64)).nextObject() as ASN1Sequence;
    final bits = spki.elements![1] as ASN1BitString;
    final rsa = ASN1Parser(Uint8List.fromList(bits.stringValues!)).nextObject() as ASN1Sequence;
    final modulus = (rsa.elements![0] as ASN1Integer).integer!;
    final exponent = (rsa.elements![1] as ASN1Integer).integer!;
    return RSAPublicKey(modulus, exponent);
  }

  static final _random = Random.secure();
  static const _passkeyAlphabet = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'; // no 0/O, 1/I

  /// A strong recording passkey for a new product (Super Admin), e.g. K7QM-9XPD-2RTF-H4WB.
  static String generatePasskey() {
    final chars = List.generate(16, (_) => _passkeyAlphabet[_random.nextInt(_passkeyAlphabet.length)]).join();
    return [for (var i = 0; i < 16; i += 4) chars.substring(i, i + 4)].join('-');
  }

  static Uint8List _secureBytes(int n) => Uint8List.fromList(List<int>.generate(n, (_) => _random.nextInt(256)));

  static String _randomName() => List<int>.generate(16, (_) => _random.nextInt(256)).map((b) => b.toRadixString(16).padLeft(2, '0')).join();
}
