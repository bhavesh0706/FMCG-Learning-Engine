import 'dart:convert';
import 'dart:io';

import 'package:path_provider/path_provider.dart';

/// A recording saved on the phone, waiting to be analyzed.
class SavedRecording {
  SavedRecording({
    required this.id,
    required this.path,
    required this.name,
    required this.kind,
    required this.durationSeconds,
    required this.createdAt,
    required this.sizeBytes,
  });

  final String id;
  final String path;
  final String name; // file name sent to the server
  final String kind; // 'audio' or 'video'
  final double durationSeconds;
  final DateTime createdAt;
  final int sizeBytes;

  bool get isVideo => kind == 'video';

  Map<String, dynamic> toJson() => {
        'id': id,
        'path': path,
        'name': name,
        'kind': kind,
        'durationSeconds': durationSeconds,
        'createdAt': createdAt.toIso8601String(),
        'sizeBytes': sizeBytes,
      };

  factory SavedRecording.fromJson(Map<String, dynamic> j) => SavedRecording(
        id: '${j['id']}',
        path: '${j['path']}',
        name: '${j['name']}',
        kind: '${j['kind']}',
        durationSeconds: (j['durationSeconds'] as num?)?.toDouble() ?? 0,
        createdAt: DateTime.tryParse('${j['createdAt']}') ?? DateTime.now(),
        sizeBytes: (j['sizeBytes'] as num?)?.toInt() ?? 0,
      );
}

/// Stores recordings in the app's own documents folder (not the gallery),
/// so they stay until the user analyzes or deletes them.
class RecordingStore {
  static Future<Directory> _dir() async {
    final base = await getApplicationDocumentsDirectory();
    final dir = Directory('${base.path}/recordings');
    if (!await dir.exists()) await dir.create(recursive: true);
    return dir;
  }

  static Future<File> _indexFile() async => File('${(await _dir()).path}/index.json');

  static Future<List<SavedRecording>> _read() async {
    try {
      final file = await _indexFile();
      if (!await file.exists()) return [];
      final data = jsonDecode(await file.readAsString());
      if (data is! List) return [];
      return data.whereType<Map>().map((m) => SavedRecording.fromJson(Map<String, dynamic>.from(m))).toList();
    } catch (_) {
      return [];
    }
  }

  static Future<void> _write(List<SavedRecording> items) async {
    final file = await _indexFile();
    await file.writeAsString(jsonEncode(items.map((e) => e.toJson()).toList()));
  }

  /// Newest first. Entries whose file has gone missing are dropped.
  static Future<List<SavedRecording>> list() async {
    final items = await _read();
    final existing = items.where((r) => File(r.path).existsSync()).toList();
    if (existing.length != items.length) await _write(existing);
    existing.sort((a, b) => b.createdAt.compareTo(a.createdAt));
    return existing;
  }

  /// Copies a freshly recorded file into permanent storage.
  static Future<SavedRecording> save({
    required String sourcePath,
    required String name,
    required String kind,
    required double durationSeconds,
  }) async {
    final dir = await _dir();
    final id = 'rec-${DateTime.now().millisecondsSinceEpoch}';
    final target = File('${dir.path}/${id}_$name');
    await File(sourcePath).copy(target.path);

    final item = SavedRecording(
      id: id,
      path: target.path,
      name: name,
      kind: kind,
      durationSeconds: durationSeconds,
      createdAt: DateTime.now(),
      sizeBytes: await target.length(),
    );
    final items = await _read()
      ..add(item);
    await _write(items);
    return item;
  }

  static Future<void> delete(String id) async {
    final items = await _read();
    for (final r in items.where((r) => r.id == id)) {
      final f = File(r.path);
      if (await f.exists()) await f.delete();
    }
    await _write(items.where((r) => r.id != id).toList());
  }
}

/// "audio-recording-2026-10-01-1520.m4a"
String recordingFileName(String kind, String extension, [DateTime? at]) {
  final d = at ?? DateTime.now();
  String p(int n) => n.toString().padLeft(2, '0');
  return '$kind-recording-${d.year}-${p(d.month)}-${p(d.day)}-${p(d.hour)}${p(d.minute)}.$extension';
}

String fileSizeLabel(int bytes) {
  final mb = bytes / (1024 * 1024);
  if (mb >= 1) return '${mb.toStringAsFixed(1)} MB';
  return '${(bytes / 1024).ceil()} KB';
}