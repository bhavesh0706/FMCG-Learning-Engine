import 'dart:async';
import 'dart:io';

import 'package:camera/camera.dart';
import 'package:dio/dio.dart' show CancelToken;
import 'package:flutter/material.dart';
import 'package:path_provider/path_provider.dart';
import 'package:record/record.dart';

import '../main.dart';
import '../services/api_service.dart';
import '../services/recording_store.dart';
import '../widgets/media_player.dart';

/// Recordings can be 30 seconds to 35 minutes, same as uploads.
const int kMaxRecordSeconds = 35 * 60;
const int kMinRecordSeconds = 30;

/// Bumped after a successful analysis so the History tab reloads itself.
final ValueNotifier<int> historyRefresh = ValueNotifier<int>(0);

enum _Phase { idle, recording, paused, finishing }

String _clock(Duration d) {
  final h = d.inHours;
  final m = (d.inMinutes % 60).toString().padLeft(2, '0');
  final s = (d.inSeconds % 60).toString().padLeft(2, '0');
  return h > 0 ? '$h:$m:$s' : '$m:$s';
}

Future<bool> _confirm(
  BuildContext context, {
  required String title,
  required String message,
  required String confirmLabel,
  bool destructive = true,
}) async {
  final result = await showDialog<bool>(
    context: context,
    builder: (ctx) => AlertDialog(
      title: Text(title),
      content: Text(message),
      actions: [
        TextButton(onPressed: () => Navigator.pop(ctx, false), child: const Text('Keep')),
        FilledButton(
          style: destructive ? FilledButton.styleFrom(backgroundColor: AppColors.record) : null,
          onPressed: () => Navigator.pop(ctx, true),
          child: Text(confirmLabel),
        ),
      ],
    ),
  );
  return result ?? false;
}

/* ============================================================
   Voice recorder
   ============================================================ */

/// A finished recording, returned to the survey flow (survey mode).
class RecordedMedia {
  const RecordedMedia({
    required this.path,
    required this.name,
    required this.kind,
    required this.durationSeconds,
    this.peakDb,
    this.source = 'recorded',
    this.sizeBytes,
  });

  final String path;
  final String name;
  final String kind; // 'audio' or 'video'
  final double durationSeconds;

  /// Loudest moment while recording, in dBFS (about -60 quiet room, 0 very loud).
  /// Null when it wasn't measured (video, or a file chosen from the phone).
  final double? peakDb;

  /// 'recorded' (in the app) or 'file' (chosen from the phone's storage).
  final String source;

  /// File size, when known (uploaded files).
  final int? sizeBytes;

  bool get isUploadedFile => source == 'file';

  /// Nothing louder than near-silence was heard: usually a microphone problem.
  bool get seemsSilent => peakDb != null && peakDb! < kSilenceDb;

  bool get isVideo => kind == 'video';
}

/// Below this loudest level a recording is treated as silent (no microphone input).
/// Deliberately low: quiet speakers are around -35 dBFS, real silence well below -60.
const double kSilenceDb = -50;

class RecordAudioScreen extends StatefulWidget {
  const RecordAudioScreen({super.key, this.returnResult = false});

  /// Survey mode: when finished, close and return a [RecordedMedia]
  /// instead of opening the review / analyze screens.
  final bool returnResult;

  @override
  State<RecordAudioScreen> createState() => _RecordAudioScreenState();
}

class _RecordAudioScreenState extends State<RecordAudioScreen> {
  final AudioRecorder _recorder = AudioRecorder();
  final Stopwatch _watch = Stopwatch();
  final List<double> _levels = List<double>.filled(40, 0.04, growable: true);
  double _peakDb = -160; // loudest level heard so far

  StreamSubscription<Amplitude>? _ampSub;
  Timer? _ticker;
  _Phase _phase = _Phase.idle;
  Duration _elapsed = Duration.zero;
  String? _path;
  String? _error;

  @override
  void dispose() {
    _ticker?.cancel();
    _ampSub?.cancel();
    _recorder.dispose();
    super.dispose();
  }

  Future<void> _start() async {
    setState(() => _error = null);
    try {
      if (!await _recorder.hasPermission()) {
        setState(() => _error =
            'Microphone access is off. Turn it on in Settings > Apps > FMCG Learning Engine > Permissions.');
        return;
      }
      final dir = await getTemporaryDirectory();
      final path = '${dir.path}/${recordingFileName('audio', 'm4a')}';
      await _recorder.start(
        const RecordConfig(encoder: AudioEncoder.aacLc, bitRate: 96000, sampleRate: 44100, numChannels: 1),
        path: path,
      );
      _path = path;
      _watch
        ..reset()
        ..start();

      _ampSub = _recorder.onAmplitudeChanged(const Duration(milliseconds: 110)).listen((a) {
        // a.current is in dBFS: about -60 when quiet, 0 when very loud.
        final level = ((a.current + 52) / 52).clamp(0.04, 1.0).toDouble();
        if (!mounted) return;
        if (a.current.isFinite && a.current > _peakDb) _peakDb = a.current;
        setState(() {
          _levels
            ..removeAt(0)
            ..add(level);
        });
      });

      _ticker = Timer.periodic(const Duration(milliseconds: 250), (_) {
        if (!mounted) return;
        setState(() => _elapsed = _watch.elapsed);
        if (_watch.elapsed.inSeconds >= kMaxRecordSeconds) _stop();
      });

      setState(() => _phase = _Phase.recording);
    } catch (e) {
      setState(() => _error = "Recording couldn't start: $e");
    }
  }

  Future<void> _pause() async {
    await _recorder.pause();
    _watch.stop();
    setState(() => _phase = _Phase.paused);
  }

  Future<void> _resume() async {
    await _recorder.resume();
    _watch.start();
    setState(() => _phase = _Phase.recording);
  }

  Future<void> _stop() async {
    if (_phase == _Phase.idle || _phase == _Phase.finishing) return;
    _ticker?.cancel();
    await _ampSub?.cancel();
    _watch.stop();
    setState(() => _phase = _Phase.finishing);

    final path = await _recorder.stop() ?? _path;
    if (!mounted || path == null) return;
    final seconds = _watch.elapsed.inMilliseconds / 1000;

    if (widget.returnResult) {
      Navigator.of(context).pop(RecordedMedia(
        path: path,
        name: path.split(Platform.pathSeparator).last,
        kind: 'audio',
        durationSeconds: seconds,
        peakDb: _peakDb,
      ));
      return;
    }

    Navigator.of(context).pushReplacement(
      MaterialPageRoute(
        builder: (_) => ReviewRecordingScreen(
          path: path,
          name: path.split(Platform.pathSeparator).last,
          kind: 'audio',
          durationSeconds: seconds,
        ),
      ),
    );
  }

  Future<void> _leave() async {
    final ok = await _confirm(
      context,
      title: 'Discard this recording?',
      message: 'What you have recorded so far will be deleted.',
      confirmLabel: 'Discard',
    );
    if (!ok) return;
    _ticker?.cancel();
    await _ampSub?.cancel();
    await _recorder.cancel(); // stops and deletes the file
    if (mounted) Navigator.of(context).pop();
  }

  @override
  Widget build(BuildContext context) {
    final live = _phase == _Phase.recording || _phase == _Phase.paused;

    return PopScope(
      canPop: _phase == _Phase.idle,
      onPopInvokedWithResult: (didPop, _) {
        if (!didPop) _leave();
      },
      child: Scaffold(
        appBar: AppBar(
          backgroundColor: AppColors.bg,
          surfaceTintColor: Colors.transparent,
          title: const Text('Voice recording', style: TextStyle(fontSize: 17, fontWeight: FontWeight.w700)),
        ),
        body: SafeArea(
          child: Padding(
            padding: const EdgeInsets.fromLTRB(20, 8, 20, 24),
            child: Column(
              children: [
                const Spacer(),
                _StatusPill(phase: _phase),
                const SizedBox(height: 18),
                Text(
                  _clock(_elapsed),
                  style: const TextStyle(
                    fontSize: 60,
                    fontWeight: FontWeight.w800,
                    letterSpacing: -1,
                    fontFeatures: [FontFeature.tabularFigures()],
                  ),
                ),
                Text('of 35:00 max', style: TextStyle(fontSize: 14, color: AppColors.muted)),
                const SizedBox(height: 28),
                _LevelBars(
                  levels: _levels,
                  color: _phase == _Phase.recording ? AppColors.record : AppColors.brand,
                  dimmed: _phase != _Phase.recording,
                ),
                const SizedBox(height: 20),
                Text(
                  switch (_phase) {
                    _Phase.idle => 'Tap the red button and start talking about the product.',
                    _Phase.recording => 'Speak normally. Tap the square to finish.',
                    _Phase.paused => 'Paused. Resume when you are ready.',
                    _Phase.finishing => 'Finishing…',
                  },
                  textAlign: TextAlign.center,
                  style: TextStyle(fontSize: 15, height: 1.4, color: AppColors.muted),
                ),
                if (_phase != _Phase.idle && _watch.elapsed.inSeconds >= 5 && _peakDb < kSilenceDb) ...[
                  const SizedBox(height: 16),
                  Container(
                    padding: const EdgeInsets.all(12),
                    decoration: BoxDecoration(
                      color: AppColors.warnSoft,
                      borderRadius: BorderRadius.circular(12),
                      border: Border.all(color: AppColors.warnLine),
                    ),
                    child: Row(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Icon(Icons.mic_off_rounded, color: AppColors.warn),
                        const SizedBox(width: 10),
                        Expanded(
                          child: Text(
                            "No sound detected. Check that the microphone isn't covered or muted. "
                            'On an emulator, turn on Extended controls > Microphone > "Virtual microphone uses host audio input".',
                            style: TextStyle(fontSize: 13.5, height: 1.4, color: AppColors.warnDeep),
                          ),
                        ),
                      ],
                    ),
                  ),
                ],
                if (_error != null) ...[
                  const SizedBox(height: 16),
                  _ErrorText(_error!),
                ],
                const Spacer(),
                _RecordControls(
                  phase: _phase,
                  onStart: _start,
                  onStop: _stop,
                  onPause: _pause,
                  onResume: _resume,
                  lightBackground: true,
                ),
                if (!live) const SizedBox(height: 12),
                if (!live)
                  Text(
                    'Find a quiet spot so your voice is clear.',
                    style: TextStyle(fontSize: 13, color: AppColors.muted),
                  ),
              ],
            ),
          ),
        ),
      ),
    );
  }
}

/* ============================================================
   Video recorder
   ============================================================ */

class RecordVideoScreen extends StatefulWidget {
  const RecordVideoScreen({super.key, this.returnResult = false});

  /// Survey mode: see [RecordAudioScreen.returnResult].
  final bool returnResult;

  @override
  State<RecordVideoScreen> createState() => _RecordVideoScreenState();
}

class _RecordVideoScreenState extends State<RecordVideoScreen> with WidgetsBindingObserver {
  final Stopwatch _watch = Stopwatch();
  List<CameraDescription> _cameras = [];
  CameraController? _controller;
  int _cameraIndex = 0;
  bool _opening = true;
  String? _error;
  _Phase _phase = _Phase.idle;
  Duration _elapsed = Duration.zero;
  Timer? _ticker;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    _setup();
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    _ticker?.cancel();
    _controller?.dispose();
    super.dispose();
  }

  // Release the camera when the app goes to the background.
  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    final c = _controller;
    if (state == AppLifecycleState.inactive) {
      if (c == null || !c.value.isInitialized) return;
      if (_phase == _Phase.recording || _phase == _Phase.paused) {
        _stop(); // keep what was recorded
      } else {
        _controller = null;
        c.dispose();
      }
    } else if (state == AppLifecycleState.resumed &&
        _phase == _Phase.idle &&
        _controller == null &&
        !_opening &&
        _cameras.isNotEmpty) {
      _openCamera();
    }
  }

  String _cameraMessage(CameraException e) {
    switch (e.code) {
      case 'CameraAccessDenied':
      case 'CameraAccessDeniedWithoutPrompt':
      case 'CameraAccessRestricted':
        return 'Camera access is off. Turn it on in Settings > Apps > FMCG Learning Engine > Permissions.';
      case 'AudioAccessDenied':
      case 'AudioAccessDeniedWithoutPrompt':
      case 'AudioAccessRestricted':
        return 'Microphone access is off. Turn it on in Settings > Apps > FMCG Learning Engine > Permissions.';
      default:
        return "The camera couldn't start: ${e.description ?? e.code}";
    }
  }

  Future<void> _setup() async {
    try {
      _cameras = await availableCameras();
      if (_cameras.isEmpty) {
        setState(() {
          _error = 'No camera was found on this device.';
          _opening = false;
        });
        return;
      }
      // Start with the back camera, so the product can be filmed.
      final back = _cameras.indexWhere((c) => c.lensDirection == CameraLensDirection.back);
      _cameraIndex = back >= 0 ? back : 0;
      await _openCamera();
    } on CameraException catch (e) {
      setState(() {
        _error = _cameraMessage(e);
        _opening = false;
      });
    }
  }

  Future<void> _openCamera() async {
    setState(() => _opening = true);
    final old = _controller;
    _controller = null;
    await old?.dispose();

    // Medium (about 480p) keeps a 35-minute video small enough to upload on mobile data.
    final c = CameraController(_cameras[_cameraIndex], ResolutionPreset.medium, enableAudio: true);
    try {
      await c.initialize();
      if (!mounted) {
        await c.dispose();
        return;
      }
      setState(() {
        _controller = c;
        _opening = false;
        _error = null;
      });
    } on CameraException catch (e) {
      await c.dispose();
      if (mounted) {
        setState(() {
          _error = _cameraMessage(e);
          _opening = false;
        });
      }
    }
  }

  Future<void> _switchCamera() async {
    if (_cameras.length < 2 || _phase != _Phase.idle) return;
    _cameraIndex = (_cameraIndex + 1) % _cameras.length;
    await _openCamera();
  }

  Future<void> _start() async {
    final c = _controller;
    if (c == null || !c.value.isInitialized || c.value.isRecordingVideo) return;
    try {
      await c.startVideoRecording();
      _watch
        ..reset()
        ..start();
      _ticker = Timer.periodic(const Duration(milliseconds: 250), (_) {
        if (!mounted) return;
        setState(() => _elapsed = _watch.elapsed);
        if (_watch.elapsed.inSeconds >= kMaxRecordSeconds) _stop();
      });
      setState(() => _phase = _Phase.recording);
    } on CameraException catch (e) {
      setState(() => _error = _cameraMessage(e));
    }
  }

  Future<void> _pause() async {
    await _controller?.pauseVideoRecording();
    _watch.stop();
    setState(() => _phase = _Phase.paused);
  }

  Future<void> _resume() async {
    await _controller?.resumeVideoRecording();
    _watch.start();
    setState(() => _phase = _Phase.recording);
  }

  Future<void> _stop() async {
    final c = _controller;
    if (c == null || _phase == _Phase.idle || _phase == _Phase.finishing) return;
    _ticker?.cancel();
    _watch.stop();
    setState(() => _phase = _Phase.finishing);

    try {
      final file = await c.stopVideoRecording();
      if (!mounted) return;
      final ext = file.path.contains('.') ? file.path.split('.').last.toLowerCase() : 'mp4';
      final name = recordingFileName('video', ext.length <= 4 ? ext : 'mp4');
      final seconds = _watch.elapsed.inMilliseconds / 1000;

      if (widget.returnResult) {
        Navigator.of(context).pop(RecordedMedia(
          path: file.path,
          name: name,
          kind: 'video',
          durationSeconds: seconds,
        ));
        return;
      }

      Navigator.of(context).pushReplacement(
        MaterialPageRoute(
          builder: (_) => ReviewRecordingScreen(
            path: file.path,
            name: name,
            kind: 'video',
            durationSeconds: seconds,
          ),
        ),
      );
    } on CameraException catch (e) {
      if (mounted) {
        setState(() {
          _error = _cameraMessage(e);
          _phase = _Phase.idle;
        });
      }
    }
  }

  Future<void> _leave() async {
    final ok = await _confirm(
      context,
      title: 'Discard this recording?',
      message: 'What you have recorded so far will be deleted.',
      confirmLabel: 'Discard',
    );
    if (!ok) return;
    _ticker?.cancel();
    final c = _controller;
    if (c != null && c.value.isRecordingVideo) {
      try {
        final f = await c.stopVideoRecording();
        await File(f.path).delete();
      } catch (_) {}
    }
    if (mounted) Navigator.of(context).pop();
  }

  @override
  Widget build(BuildContext context) {
    final c = _controller;
    final live = _phase == _Phase.recording || _phase == _Phase.paused;

    return PopScope(
      canPop: _phase == _Phase.idle,
      onPopInvokedWithResult: (didPop, _) {
        if (!didPop) _leave();
      },
      child: Scaffold(
        backgroundColor: Colors.black,
        body: Stack(
          children: [
            // Camera preview
            Positioned.fill(
              child: _error != null && c == null
                  ? Center(
                      child: Padding(
                        padding: const EdgeInsets.all(28),
                        child: Column(
                          mainAxisSize: MainAxisSize.min,
                          children: [
                            const Icon(Icons.videocam_off_rounded, color: Colors.white70, size: 40),
                            const SizedBox(height: 14),
                            Text(
                              _error!,
                              textAlign: TextAlign.center,
                              style: const TextStyle(color: Colors.white, fontSize: 15, height: 1.45),
                            ),
                            const SizedBox(height: 18),
                            FilledButton(onPressed: _setup, child: const Text('Try again')),
                          ],
                        ),
                      ),
                    )
                  : (c == null || _opening || !c.value.isInitialized)
                      ? const Center(child: CircularProgressIndicator(color: Colors.white))
                      : Center(child: CameraPreview(c)),
            ),

            // Top bar
            SafeArea(
              child: Padding(
                padding: const EdgeInsets.fromLTRB(8, 8, 8, 0),
                child: Row(
                  children: [
                    _RoundIconButton(
                      icon: Icons.close_rounded,
                      tooltip: 'Close',
                      onTap: () => _phase == _Phase.idle ? Navigator.of(context).pop() : _leave(),
                    ),
                    const Spacer(),
                    Container(
                      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 7),
                      decoration: BoxDecoration(
                        color: Colors.black.withValues(alpha: 0.5),
                        borderRadius: BorderRadius.circular(999),
                      ),
                      child: Row(
                        mainAxisSize: MainAxisSize.min,
                        children: [
                          if (live)
                            Container(
                              width: 9,
                              height: 9,
                              margin: const EdgeInsets.only(right: 8),
                              decoration: BoxDecoration(
                                color: _phase == _Phase.paused ? const Color(0xFFF5B83D) : AppColors.record,
                                shape: BoxShape.circle,
                              ),
                            ),
                          Text(
                            live ? _clock(_elapsed) : 'Max 35:00',
                            style: const TextStyle(
                              color: Colors.white,
                              fontSize: 15,
                              fontWeight: FontWeight.w700,
                              fontFeatures: [FontFeature.tabularFigures()],
                            ),
                          ),
                        ],
                      ),
                    ),
                    const Spacer(),
                    const SizedBox(width: 48),
                  ],
                ),
              ),
            ),

            // Bottom controls
            Align(
              alignment: Alignment.bottomCenter,
              child: Container(
                padding: const EdgeInsets.fromLTRB(20, 24, 20, 16),
                decoration: const BoxDecoration(
                  gradient: LinearGradient(
                    begin: Alignment.topCenter,
                    end: Alignment.bottomCenter,
                    colors: [Colors.transparent, Color(0xCC000000)],
                  ),
                ),
                child: SafeArea(
                  top: false,
                  child: Column(
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      if (_error != null && c != null) ...[
                        _ErrorText(_error!, onDark: true),
                        const SizedBox(height: 12),
                      ],
                      if (!live)
                        const Padding(
                          padding: EdgeInsets.only(bottom: 14),
                          child: Text(
                            'Keep the product in view while you talk',
                            style: TextStyle(color: Colors.white, fontSize: 14),
                          ),
                        ),
                      _RecordControls(
                        phase: _phase,
                        onStart: _start,
                        onStop: _stop,
                        onPause: _pause,
                        onResume: _resume,
                        lightBackground: false,
                        leading: _phase == _Phase.idle && _cameras.length > 1
                            ? _RoundIconButton(
                                icon: Icons.cameraswitch_rounded,
                                tooltip: 'Switch camera',
                                onTap: _switchCamera,
                              )
                            : null,
                      ),
                    ],
                  ),
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }
}

/* ============================================================
   Review: analyze now, save for later, or discard
   ============================================================ */

class ReviewRecordingScreen extends StatefulWidget {
  const ReviewRecordingScreen({
    super.key,
    required this.path,
    required this.name,
    required this.kind,
    required this.durationSeconds,
  });

  final String path;
  final String name;
  final String kind; // 'audio' or 'video'
  final double durationSeconds;

  @override
  State<ReviewRecordingScreen> createState() => _ReviewRecordingScreenState();
}

class _ReviewRecordingScreenState extends State<ReviewRecordingScreen> {
  bool _saving = false;

  bool get _isVideo => widget.kind == 'video';

  int get _size {
    try {
      return File(widget.path).lengthSync();
    } catch (_) {
      return 0;
    }
  }

  void _analyzeNow() {
    Navigator.of(context).pushReplacement(
      MaterialPageRoute(
        builder: (_) => AnalyzeScreen(
          path: widget.path,
          name: widget.name,
          kind: widget.kind,
          durationSeconds: widget.durationSeconds,
          canSaveOnFailure: true,
          deleteFileOnSuccess: true,
        ),
      ),
    );
  }

  Future<void> _saveForLater() async {
    setState(() => _saving = true);
    try {
      await RecordingStore.save(
        sourcePath: widget.path,
        name: widget.name,
        kind: widget.kind,
        durationSeconds: widget.durationSeconds,
      );
      try {
        await File(widget.path).delete(); // the saved copy is kept
      } catch (_) {}
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('Saved on this phone. Analyze it when you have a good connection.')),
      );
      Navigator.of(context).pop(true);
    } catch (e) {
      if (!mounted) return;
      setState(() => _saving = false);
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text("Couldn't save the recording: $e")),
      );
    }
  }

  Future<void> _discard() async {
    final ok = await _confirm(
      context,
      title: 'Delete this recording?',
      message: "It hasn't been analyzed or saved, so it will be lost.",
      confirmLabel: 'Delete',
    );
    if (!ok) return;
    try {
      await File(widget.path).delete();
    } catch (_) {}
    if (mounted) Navigator.of(context).pop();
  }

  @override
  Widget build(BuildContext context) {
    final short = widget.durationSeconds < kMinRecordSeconds;

    return PopScope(
      canPop: false,
      onPopInvokedWithResult: (didPop, _) {
        if (!didPop) _discard();
      },
      child: Scaffold(
        appBar: AppBar(
          backgroundColor: AppColors.bg,
          surfaceTintColor: Colors.transparent,
          automaticallyImplyLeading: false,
          title: const Text('Recording ready', style: TextStyle(fontSize: 17, fontWeight: FontWeight.w700)),
        ),
        body: SafeArea(
          child: ListView(
            padding: const EdgeInsets.fromLTRB(20, 12, 20, 24),
            children: [
              Container(
                padding: const EdgeInsets.all(20),
                decoration: BoxDecoration(
                  color: AppColors.surface,
                  borderRadius: BorderRadius.circular(20),
                  border: Border.all(color: AppColors.line),
                ),
                child: Row(
                  children: [
                    Container(
                      width: 60,
                      height: 60,
                      decoration: BoxDecoration(
                        color: _isVideo ? AppColors.videoSoft : AppColors.brandSoft,
                        borderRadius: BorderRadius.circular(18),
                      ),
                      child: Icon(
                        _isVideo ? Icons.videocam_rounded : Icons.mic_rounded,
                        size: 30,
                        color: _isVideo ? AppColors.video : AppColors.brand,
                      ),
                    ),
                    const SizedBox(width: 16),
                    Expanded(
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Text(
                            _isVideo ? 'Video recording' : 'Voice recording',
                            style: const TextStyle(fontSize: 18, fontWeight: FontWeight.w700),
                          ),
                          const SizedBox(height: 4),
                          Text(
                            '${formatDuration(widget.durationSeconds)}  ·  ${fileSizeLabel(_size)}',
                            style: TextStyle(fontSize: 14, color: AppColors.muted),
                          ),
                        ],
                      ),
                    ),
                  ],
                ),
              ),
              const SizedBox(height: 14),
              // Listen or watch before deciding what to do with it.
              MediaPlayerCard(
                filePath: widget.path,
                isVideo: _isVideo,
                durationHint: widget.durationSeconds,
              ),
              if (short) ...[
                const SizedBox(height: 14),
                Container(
                  padding: const EdgeInsets.all(14),
                  decoration: BoxDecoration(
                    color: AppColors.warnSoft,
                    borderRadius: BorderRadius.circular(14),
                  ),
                  child: Text(
                    'This recording is under 30 seconds, which may be too short to analyze. '
                    'Longer reviews give more detailed results.',
                    style: TextStyle(fontSize: 14, height: 1.45, color: AppColors.warnDeep),
                  ),
                ),
              ],
              const SizedBox(height: 24),
              FilledButton.icon(
                onPressed: _saving ? null : _analyzeNow,
                icon: const Icon(Icons.auto_awesome_rounded),
                label: const Text('Analyze now'),
              ),
              const SizedBox(height: 6),
              Text(
                'Uploads the recording and shows the results here.',
                textAlign: TextAlign.center,
                style: TextStyle(fontSize: 13, color: AppColors.muted),
              ),
              const SizedBox(height: 18),
              OutlinedButton.icon(
                onPressed: _saving ? null : _saveForLater,
                icon: _saving
                    ? const SizedBox(width: 18, height: 18, child: CircularProgressIndicator(strokeWidth: 2))
                    : const Icon(Icons.save_alt_rounded),
                label: Text(_saving ? 'Saving…' : 'Save for later'),
              ),
              const SizedBox(height: 6),
              Text(
                'Weak network? Keep it on this phone and analyze it later.',
                textAlign: TextAlign.center,
                style: TextStyle(fontSize: 13, color: AppColors.muted),
              ),
              const SizedBox(height: 24),
              TextButton.icon(
                onPressed: _saving ? null : _discard,
                style: TextButton.styleFrom(foregroundColor: AppColors.record, minimumSize: const Size(0, 48)),
                icon: const Icon(Icons.delete_outline_rounded),
                label: const Text('Discard'),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

/* ============================================================
   Upload and analyze
   ============================================================ */

enum _Stage { uploading, processing, failed }

class AnalyzeScreen extends StatefulWidget {
  const AnalyzeScreen({
    super.key,
    required this.path,
    required this.name,
    required this.kind,
    this.durationSeconds = 0,
    this.savedId,
    this.canSaveOnFailure = false,
    this.deleteFileOnSuccess = false,
  });

  final String path;
  final String name;
  final String kind;
  final double durationSeconds;

  /// Set when analyzing a recording saved on the phone; it is removed once analyzed.
  final String? savedId;

  /// A fresh recording that isn't saved yet: offer "Save for later" if the upload fails.
  final bool canSaveOnFailure;

  /// Delete the temporary file after a successful analysis.
  final bool deleteFileOnSuccess;

  @override
  State<AnalyzeScreen> createState() => _AnalyzeScreenState();
}

class _AnalyzeScreenState extends State<AnalyzeScreen> {
  final ApiService _api = ApiService();
  CancelToken _cancel = CancelToken();
  _Stage _stage = _Stage.uploading;
  double _progress = 0;
  String _error = '';
  bool _saved = false;

  @override
  void initState() {
    super.initState();
    _run();
  }

  @override
  void dispose() {
    if (!_cancel.isCancelled) _cancel.cancel();
    super.dispose();
  }

  Future<void> _run() async {
    _cancel = CancelToken();
    setState(() {
      _stage = _Stage.uploading;
      _progress = 0;
      _error = '';
    });

    try {
      final result = await _api.uploadMedia(
        widget.path,
        filename: widget.name,
        cancelToken: _cancel,
        onSendProgress: (p) {
          if (!mounted) return;
          setState(() {
            _progress = p;
            if (p >= 0.999) _stage = _Stage.processing;
          });
        },
      );

      if (widget.savedId != null) await RecordingStore.delete(widget.savedId!);
      if (widget.deleteFileOnSuccess) {
        try {
          await File(widget.path).delete();
        } catch (_) {}
      }
      historyRefresh.value++;

      if (!mounted) return;
      Navigator.of(context).pushReplacement(
        MaterialPageRoute(builder: (_) => FeedbackDetailPage(id: result.id, preview: result)),
      );
    } catch (e) {
      if (!mounted || _cancel.isCancelled) return;
      setState(() {
        _stage = _Stage.failed;
        _error = '$e';
      });
    }
  }

  Future<void> _saveForLater() async {
    try {
      await RecordingStore.save(
        sourcePath: widget.path,
        name: widget.name,
        kind: widget.kind,
        durationSeconds: widget.durationSeconds,
      );
      setState(() => _saved = true);
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('Saved on this phone. You can analyze it from the Analyze tab.')),
      );
      Navigator.of(context).pop(true);
    } catch (e) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text("Couldn't save: $e")));
    }
  }

  Future<void> _cancelRun() async {
    final ok = await _confirm(
      context,
      title: 'Stop the analysis?',
      message: _stage == _Stage.processing
          ? 'The upload is finished. The server may still complete the analysis and add it to History.'
          : 'The upload will stop. You can try again later.',
      confirmLabel: 'Stop',
    );
    if (!ok) return;
    _cancel.cancel();
    if (mounted) Navigator.of(context).pop();
  }

  @override
  Widget build(BuildContext context) {
    final running = _stage != _Stage.failed;
    final pct = (_progress * 100).clamp(0, 100).round();

    return PopScope(
      canPop: !running,
      onPopInvokedWithResult: (didPop, _) {
        if (!didPop) _cancelRun();
      },
      child: Scaffold(
        appBar: AppBar(
          backgroundColor: AppColors.bg,
          surfaceTintColor: Colors.transparent,
          title: Text(
            running ? 'Analyzing' : "Couldn't analyze",
            style: const TextStyle(fontSize: 17, fontWeight: FontWeight.w700),
          ),
        ),
        body: SafeArea(
          child: ListView(
            padding: const EdgeInsets.fromLTRB(20, 16, 20, 24),
            children: [
              if (running) ...[
                const SizedBox(height: 12),
                Center(
                  child: SizedBox(
                    width: 120,
                    height: 120,
                    child: Stack(
                      alignment: Alignment.center,
                      children: [
                        SizedBox.expand(
                          child: CircularProgressIndicator(
                            value: _stage == _Stage.uploading ? _progress : null,
                            strokeWidth: 8,
                            color: AppColors.brand,
                            backgroundColor: AppColors.brandSoft,
                          ),
                        ),
                        Icon(
                          _stage == _Stage.uploading ? Icons.cloud_upload_rounded : Icons.auto_awesome_rounded,
                          size: 38,
                          color: AppColors.brand,
                        ),
                      ],
                    ),
                  ),
                ),
                const SizedBox(height: 22),
                Text(
                  _stage == _Stage.uploading ? 'Uploading… $pct%' : 'Transcribing and analyzing',
                  textAlign: TextAlign.center,
                  style: const TextStyle(fontSize: 21, fontWeight: FontWeight.w800),
                ),
                const SizedBox(height: 8),
                Text(
                  _stage == _Stage.uploading
                      ? 'Sending ${widget.name}'
                      : 'This takes a few minutes. A 5-minute review usually takes 5 to 7 minutes.',
                  textAlign: TextAlign.center,
                  style: TextStyle(fontSize: 15, height: 1.45, color: AppColors.muted),
                ),
                const SizedBox(height: 28),
                _StepRow(label: 'Upload', state: _stage == _Stage.uploading ? 1 : 2),
                _StepRow(label: 'Transcribe and analyze', state: _stage == _Stage.processing ? 1 : 0),
                const _StepRow(label: 'Results ready', state: 0, last: true),
                const SizedBox(height: 24),
                Text(
                  'Keep the app open until it finishes.',
                  textAlign: TextAlign.center,
                  style: TextStyle(fontSize: 13.5, color: AppColors.muted),
                ),
                const SizedBox(height: 8),
                TextButton(
                  onPressed: _cancelRun,
                  style: TextButton.styleFrom(foregroundColor: AppColors.muted, minimumSize: const Size(0, 48)),
                  child: const Text('Cancel'),
                ),
              ] else ...[
                Container(
                  padding: const EdgeInsets.all(18),
                  decoration: BoxDecoration(
                    color: AppColors.negSoft,
                    borderRadius: BorderRadius.circular(18),
                  ),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Row(
                        children: [
                          Icon(Icons.cloud_off_rounded, color: AppColors.neg),
                          SizedBox(width: 10),
                          Expanded(
                            child: Text(
                              'The analysis did not finish',
                              style: TextStyle(fontSize: 16, fontWeight: FontWeight.w700, color: AppColors.neg),
                            ),
                          ),
                        ],
                      ),
                      const SizedBox(height: 8),
                      Text(_error, style: TextStyle(fontSize: 14, height: 1.45, color: AppColors.negDeep)),
                    ],
                  ),
                ),
                const SizedBox(height: 20),
                FilledButton.icon(
                  onPressed: _run,
                  icon: const Icon(Icons.refresh_rounded),
                  label: const Text('Try again'),
                ),
                if (widget.canSaveOnFailure && !_saved) ...[
                  const SizedBox(height: 12),
                  OutlinedButton.icon(
                    onPressed: _saveForLater,
                    icon: const Icon(Icons.save_alt_rounded),
                    label: const Text('Save for later'),
                  ),
                ],
                const SizedBox(height: 12),
                TextButton(
                  onPressed: () => Navigator.of(context).pop(),
                  style: TextButton.styleFrom(foregroundColor: AppColors.muted, minimumSize: const Size(0, 48)),
                  child: const Text('Back'),
                ),
              ],
            ],
          ),
        ),
      ),
    );
  }
}

/* ============================================================
   Small shared widgets
   ============================================================ */

class _StatusPill extends StatelessWidget {
  const _StatusPill({required this.phase});

  final _Phase phase;

  @override
  Widget build(BuildContext context) {
    final (label, color, bg) = switch (phase) {
      _Phase.idle => ('Ready', AppColors.brand, AppColors.brandSoft),
      _Phase.recording => ('Recording', AppColors.record, AppColors.negSoft),
      _Phase.paused => ('Paused', AppColors.warn, AppColors.warnSoft),
      _Phase.finishing => ('Saving', AppColors.muted, AppColors.neutralSoft),
    };
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 6),
      decoration: BoxDecoration(color: bg, borderRadius: BorderRadius.circular(999)),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Container(width: 8, height: 8, decoration: BoxDecoration(color: color, shape: BoxShape.circle)),
          const SizedBox(width: 8),
          Text(label, style: TextStyle(fontSize: 14, fontWeight: FontWeight.w700, color: color)),
        ],
      ),
    );
  }
}

class _LevelBars extends StatelessWidget {
  const _LevelBars({required this.levels, required this.color, required this.dimmed});

  final List<double> levels;
  final Color color;
  final bool dimmed;

  @override
  Widget build(BuildContext context) {
    return SizedBox(
      height: 90,
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.center,
        children: [
          for (final l in levels)
            Expanded(
              child: Center(
                child: AnimatedContainer(
                  duration: const Duration(milliseconds: 110),
                  width: 4,
                  height: 6 + l * 80,
                  decoration: BoxDecoration(
                    color: dimmed ? color.withValues(alpha: 0.35) : color,
                    borderRadius: BorderRadius.circular(4),
                  ),
                ),
              ),
            ),
        ],
      ),
    );
  }
}

class _RecordControls extends StatelessWidget {
  const _RecordControls({
    required this.phase,
    required this.onStart,
    required this.onStop,
    required this.onPause,
    required this.onResume,
    required this.lightBackground,
    this.leading,
  });

  final _Phase phase;
  final VoidCallback onStart;
  final VoidCallback onStop;
  final VoidCallback onPause;
  final VoidCallback onResume;
  final bool lightBackground;
  final Widget? leading;

  @override
  Widget build(BuildContext context) {
    final live = phase == _Phase.recording || phase == _Phase.paused;
    final busy = phase == _Phase.finishing;

    Widget side(Widget? child) => SizedBox(width: 72, height: 72, child: Center(child: child));

    return Row(
      mainAxisAlignment: MainAxisAlignment.center,
      children: [
        side(
          live
              ? _RoundIconButton(
                  icon: phase == _Phase.paused ? Icons.play_arrow_rounded : Icons.pause_rounded,
                  tooltip: phase == _Phase.paused ? 'Resume' : 'Pause',
                  onTap: phase == _Phase.paused ? onResume : onPause,
                  light: lightBackground,
                )
              : leading,
        ),
        const SizedBox(width: 24),
        Semantics(
          button: true,
          label: live ? 'Stop recording' : 'Start recording',
          child: GestureDetector(
            onTap: busy ? null : (live ? onStop : onStart),
            child: Container(
              width: 84,
              height: 84,
              decoration: BoxDecoration(
                shape: BoxShape.circle,
                border: Border.all(
                  color: lightBackground ? AppColors.negLine : Colors.white,
                  width: 5,
                ),
              ),
              child: Center(
                child: busy
                    ? SizedBox(
                        width: 28,
                        height: 28,
                        child: CircularProgressIndicator(strokeWidth: 3, color: AppColors.record),
                      )
                    : AnimatedContainer(
                        duration: const Duration(milliseconds: 180),
                        width: live ? 30 : 60,
                        height: live ? 30 : 60,
                        decoration: BoxDecoration(
                          color: AppColors.record,
                          borderRadius: BorderRadius.circular(live ? 7 : 30),
                        ),
                      ),
              ),
            ),
          ),
        ),
        const SizedBox(width: 24),
        side(null),
      ],
    );
  }
}

class _RoundIconButton extends StatelessWidget {
  const _RoundIconButton({
    required this.icon,
    required this.tooltip,
    required this.onTap,
    this.light = false,
  });

  final IconData icon;
  final String tooltip;
  final VoidCallback onTap;
  final bool light;

  @override
  Widget build(BuildContext context) {
    return Tooltip(
      message: tooltip,
      child: Material(
        color: light ? AppColors.surface : Colors.black.withValues(alpha: 0.5),
        shape: light ? CircleBorder(side: BorderSide(color: AppColors.line)) : const CircleBorder(),
        child: InkWell(
          customBorder: const CircleBorder(),
          onTap: onTap,
          child: SizedBox(
            width: 52,
            height: 52,
            child: Icon(icon, color: light ? AppColors.ink : Colors.white, size: 26),
          ),
        ),
      ),
    );
  }
}

class _ErrorText extends StatelessWidget {
  const _ErrorText(this.text, {this.onDark = false});

  final String text;
  final bool onDark;

  @override
  Widget build(BuildContext context) {
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(
        color: onDark ? const Color(0xE6C8414F) : AppColors.negSoft,
        borderRadius: BorderRadius.circular(12),
      ),
      child: Text(
        text,
        style: TextStyle(
          fontSize: 14,
          height: 1.4,
          color: onDark ? Colors.white : AppColors.negDeep,
        ),
      ),
    );
  }
}

class _StepRow extends StatelessWidget {
  const _StepRow({required this.label, required this.state, this.last = false});

  final String label;
  final int state; // 0 pending, 1 active, 2 done
  final bool last;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: EdgeInsets.only(bottom: last ? 0 : 12),
      child: Row(
        children: [
          SizedBox(
            width: 24,
            height: 24,
            child: state == 2
                ? Icon(Icons.check_circle_rounded, color: AppColors.brand)
                : state == 1
                    ? Padding(
                        padding: EdgeInsets.all(3),
                        child: CircularProgressIndicator(strokeWidth: 2.5, color: AppColors.brand),
                      )
                    : Icon(Icons.radio_button_unchecked_rounded, color: AppColors.line),
          ),
          const SizedBox(width: 12),
          Text(
            label,
            style: TextStyle(
              fontSize: 15.5,
              fontWeight: state == 1 ? FontWeight.w700 : FontWeight.w500,
              color: state == 0 ? AppColors.muted : AppColors.ink,
            ),
          ),
        ],
      ),
    );
  }
}