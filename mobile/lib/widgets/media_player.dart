import 'dart:io';

import 'package:flutter/material.dart';
import 'package:video_player/video_player.dart';

import '../main.dart';

/// Plays a voice or video recording, either a file on the phone (filePath)
/// or the original stored on the server (url).
class MediaPlayerCard extends StatefulWidget {
  const MediaPlayerCard({
    super.key,
    this.filePath,
    this.url,
    required this.isVideo,
    this.durationHint = 0,
  }) : assert(filePath != null || url != null);

  final String? filePath;
  final String? url;
  final bool isVideo;

  /// Used for the total time until the player knows the real length.
  final double durationHint;

  @override
  State<MediaPlayerCard> createState() => _MediaPlayerCardState();
}

class _MediaPlayerCardState extends State<MediaPlayerCard> {
  late VideoPlayerController _controller;
  bool _ready = false;
  String? _error;
  double? _dragMs; // slider position while the user drags

  @override
  void initState() {
    super.initState();
    _load();
  }

  void _load() {
    _ready = false;
    _error = null;
    _controller = widget.filePath != null
        ? VideoPlayerController.file(File(widget.filePath!))
        : VideoPlayerController.networkUrl(Uri.parse(widget.url!));
    _controller.addListener(_onTick);
    _controller.initialize().then((_) {
      if (mounted) setState(() => _ready = true);
    }).catchError((Object e) {
      _fail(e);
    });
  }

  Future<void> _retry() async {
    _controller.removeListener(_onTick);
    await _controller.dispose();
    if (!mounted) return;
    setState(_load);
  }

  /// Turns the player's technical error into a message that says what to do.
  void _fail(Object error) {
    final raw = '$error';
    debugPrint('Media player error (${widget.url ?? widget.filePath}): $raw');
    final t = raw.toLowerCase();

    String message;
    if (t.contains('cleartext')) {
      message = 'Android blocked the http:// connection to the server. '
          'Add android:usesCleartextTraffic="true" to AndroidManifest.xml and rebuild the app.';
    } else if (t.contains('404') || t.contains('response code: 4')) {
      message = 'The server has no recording file for this review (not found).';
    } else if (t.contains('decoder') || t.contains('codec') || t.contains('format') || t.contains('renderer')) {
      message = widget.url != null
          ? "This device can't decode the recording's format. iPhone .mov videos often "
              "use HEVC, which many emulators can't play. Try a real phone or an MP4 upload."
          : "This recording's format can't be played on this device.";
    } else if (t.contains('unable to connect') ||
        t.contains('failed to connect') ||
        t.contains('timed out') ||
        t.contains('connection') ||
        t.contains('source error')) {
      message = widget.url != null
          ? "Couldn't reach the server to stream the recording. Check that the backend is running."
          : "This recording file couldn't be opened.";
    } else {
      message = widget.url != null
          ? "The original recording couldn't be loaded from the server."
          : "This recording can't be played on this device.";
    }

    if (mounted) setState(() => _error = message);
  }

  void _onTick() {
    if (!mounted) return;
    final v = _controller.value;
    if (v.hasError && _error == null) {
      _fail(v.errorDescription ?? 'playback error');
      return;
    }
    setState(() {});
  }

  @override
  void dispose() {
    _controller.removeListener(_onTick);
    _controller.dispose();
    super.dispose();
  }

  Duration get _duration {
    final d = _controller.value.duration;
    if (d > Duration.zero) return d;
    return Duration(milliseconds: (widget.durationHint * 1000).round());
  }

  bool get _atEnd =>
      _duration > Duration.zero && _controller.value.position >= _duration - const Duration(milliseconds: 200);

  Future<void> _toggle() async {
    if (!_ready) return;
    if (_controller.value.isPlaying) {
      await _controller.pause();
    } else {
      if (_atEnd) await _controller.seekTo(Duration.zero);
      await _controller.play();
    }
  }

  String _fmt(Duration d) {
    final h = d.inHours;
    final m = (d.inMinutes % 60).toString().padLeft(2, '0');
    final s = (d.inSeconds % 60).toString().padLeft(2, '0');
    return h > 0 ? '$h:$m:$s' : '$m:$s';
  }

  @override
  Widget build(BuildContext context) {
    if (_error != null) return _ErrorBox(message: _error!, isVideo: widget.isVideo, onRetry: _retry);
    return widget.isVideo ? _buildVideo() : _buildAudio();
  }

  /* ---------- Shared seek bar ---------- */

  Widget _seekBar({required bool onDark}) {
    final totalMs = _duration.inMilliseconds.toDouble();
    final posMs = _dragMs ?? _controller.value.position.inMilliseconds.toDouble();
    final max = totalMs > 0 ? totalMs : 1.0;
    final textColor = onDark ? Colors.white : AppColors.muted;

    return Column(
      mainAxisSize: MainAxisSize.min,
      children: [
        SliderTheme(
          data: SliderTheme.of(context).copyWith(
            trackHeight: 4,
            thumbShape: const RoundSliderThumbShape(enabledThumbRadius: 7),
            overlayShape: const RoundSliderOverlayShape(overlayRadius: 16),
            activeTrackColor: onDark ? Colors.white : AppColors.brand,
            inactiveTrackColor: onDark ? Colors.white24 : AppColors.neutralSoft,
            thumbColor: onDark ? Colors.white : AppColors.brand,
          ),
          child: Slider(
            value: posMs.clamp(0, max).toDouble(),
            max: max,
            onChangeStart: _ready ? (v) => setState(() => _dragMs = v) : null,
            onChanged: _ready ? (v) => setState(() => _dragMs = v) : null,
            onChangeEnd: _ready
                ? (v) async {
                    await _controller.seekTo(Duration(milliseconds: v.round()));
                    if (mounted) setState(() => _dragMs = null);
                  }
                : null,
          ),
        ),
        Padding(
          padding: const EdgeInsets.symmetric(horizontal: 16),
          child: Row(
            children: [
              Text(
                _fmt(Duration(milliseconds: posMs.round())),
                style: TextStyle(fontSize: 12.5, color: textColor, fontFeatures: const [FontFeature.tabularFigures()]),
              ),
              const Spacer(),
              Text(
                _fmt(_duration),
                style: TextStyle(fontSize: 12.5, color: textColor, fontFeatures: const [FontFeature.tabularFigures()]),
              ),
            ],
          ),
        ),
      ],
    );
  }

  /* ---------- Voice ---------- */

  Widget _buildAudio() {
    final playing = _controller.value.isPlaying;
    return Container(
      padding: const EdgeInsets.fromLTRB(12, 12, 4, 8),
      decoration: BoxDecoration(
        color: AppColors.brandSoft,
        borderRadius: BorderRadius.circular(18),
      ),
      child: Row(
        children: [
          Semantics(
            button: true,
            label: playing ? 'Pause' : 'Play',
            child: Material(
              color: AppColors.brand,
              shape: const CircleBorder(),
              child: InkWell(
                customBorder: const CircleBorder(),
                onTap: _toggle,
                child: SizedBox(
                  width: 54,
                  height: 54,
                  child: _ready
                      ? Icon(playing ? Icons.pause_rounded : Icons.play_arrow_rounded, color: Colors.white, size: 30)
                      : const Padding(
                          padding: EdgeInsets.all(16),
                          child: CircularProgressIndicator(strokeWidth: 2.5, color: Colors.white),
                        ),
                ),
              ),
            ),
          ),
          Expanded(child: _seekBar(onDark: false)),
        ],
      ),
    );
  }

  /* ---------- Video ---------- */

  Widget _buildVideo() {
    final v = _controller.value;
    final ratio = _ready && v.aspectRatio > 0 ? v.aspectRatio : 16 / 9;
    // Portrait phone videos would be very tall; cap the height.
    final maxHeight = MediaQuery.of(context).size.height * 0.5;

    return ClipRRect(
      borderRadius: BorderRadius.circular(18),
      child: Container(
        color: Colors.black,
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            ConstrainedBox(
              constraints: BoxConstraints(maxHeight: maxHeight),
              child: AspectRatio(
                aspectRatio: ratio,
                child: Stack(
                  fit: StackFit.expand,
                  children: [
                    if (_ready) VideoPlayer(_controller),
                    if (!_ready) const Center(child: CircularProgressIndicator(color: Colors.white)),
                    if (_ready)
                      Material(
                        color: Colors.transparent,
                        child: InkWell(
                          onTap: _toggle,
                          child: AnimatedOpacity(
                            opacity: v.isPlaying ? 0 : 1,
                            duration: const Duration(milliseconds: 180),
                            child: Center(
                              child: Container(
                                width: 64,
                                height: 64,
                                decoration: BoxDecoration(
                                  color: Colors.black.withValues(alpha: 0.5),
                                  shape: BoxShape.circle,
                                ),
                                child: const Icon(Icons.play_arrow_rounded, color: Colors.white, size: 40),
                              ),
                            ),
                          ),
                        ),
                      ),
                  ],
                ),
              ),
            ),
            Padding(
              padding: const EdgeInsets.fromLTRB(4, 4, 4, 8),
              child: Row(
                children: [
                  IconButton(
                    onPressed: _ready ? _toggle : null,
                    tooltip: v.isPlaying ? 'Pause' : 'Play',
                    icon: Icon(v.isPlaying ? Icons.pause_rounded : Icons.play_arrow_rounded, color: Colors.white),
                  ),
                  Expanded(child: _seekBar(onDark: true)),
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class _ErrorBox extends StatelessWidget {
  const _ErrorBox({required this.message, required this.isVideo, required this.onRetry});

  final String message;
  final bool isVideo;
  final Future<void> Function() onRetry;

  @override
  Widget build(BuildContext context) {
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: AppColors.neutralSoft,
        borderRadius: BorderRadius.circular(18),
      ),
      child: Row(
        children: [
          Icon(isVideo ? Icons.videocam_off_rounded : Icons.volume_off_rounded, color: AppColors.muted),
          const SizedBox(width: 12),
          Expanded(
            child: Text(message, style: TextStyle(fontSize: 14, height: 1.4, color: AppColors.ink2)),
          ),
          TextButton(
            onPressed: onRetry,
            style: TextButton.styleFrom(foregroundColor: AppColors.brand, minimumSize: const Size(0, 44)),
            child: const Text('Retry'),
          ),
        ],
      ),
    );
  }
}

/// Bottom sheet that plays a recording saved on the phone.
Future<void> showRecordingPlayer(
  BuildContext context, {
  required String filePath,
  required bool isVideo,
  required String title,
  double durationHint = 0,
}) {
  return showModalBottomSheet<void>(
    context: context,
    useSafeArea: true,
    showDragHandle: true,
    isScrollControlled: true,
    backgroundColor: AppColors.surface,
    shape: const RoundedRectangleBorder(borderRadius: BorderRadius.vertical(top: Radius.circular(24))),
    builder: (_) => Padding(
      padding: const EdgeInsets.fromLTRB(20, 0, 20, 24),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(title, style: const TextStyle(fontSize: 18, fontWeight: FontWeight.w700)),
          const SizedBox(height: 14),
          MediaPlayerCard(filePath: filePath, isVideo: isVideo, durationHint: durationHint),
        ],
      ),
    ),
  );
}