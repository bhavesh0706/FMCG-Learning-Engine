import 'package:flutter/material.dart';

import '../main.dart';
import '../services/api_service.dart';
import '../services/session.dart';
import 'media_player.dart';

/*
  A consumer recording on the analysis page. Recordings are encrypted (services/recording_vault.dart,
  backend services/recording_crypto.py):

    survey person   never plays it (a note says it is protected)
    Admin           allowed for the product: enters the recording passkey (one for all products) -> the server checks it and
                    the role, and streams the recording decrypted on the server (10-minute link,
                    never saved on the phone). A passkey sent by the Super Admin can be shown once.
    Super Admin     the same, with the passkey they set
*/
class ProtectedRecording extends StatefulWidget {
  const ProtectedRecording({super.key, required this.feedbackId, required this.isVideo, this.durationHint});

  final int feedbackId;
  final bool isVideo;
  final double? durationHint;

  @override
  State<ProtectedRecording> createState() => _ProtectedRecordingState();
}

class _ProtectedRecordingState extends State<ProtectedRecording> {
  String? _url;
  bool _isVideo = false;

  bool get _mayPlay => Session.user.value?.role == 'admin' || Session.isClient;

  Future<void> _unlock() async {
    final result = await showDialog<({String url, bool isVideo})>(
      context: context,
      builder: (_) => _PasskeyDialog(feedbackId: widget.feedbackId),
    );
    if (result != null && mounted) {
      setState(() {
        _url = result.url;
        _isVideo = result.isVideo;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    if (_url != null) {
      return MediaPlayerCard(key: ValueKey(_url), url: _url, isVideo: _isVideo || widget.isVideo, durationHint: widget.durationHint ?? 0);
    }
    return Container(
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(color: AppColors.surface, borderRadius: BorderRadius.circular(16), border: Border.all(color: AppColors.line)),
      child: Row(
        children: [
          Container(
            width: 44,
            height: 44,
            decoration: BoxDecoration(color: AppColors.brandSoft, borderRadius: BorderRadius.circular(12)),
            child: Icon(Icons.lock_rounded, color: AppColors.brand),
          ),
          const SizedBox(width: 12),
          Expanded(
            child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Text('Encrypted ${widget.isVideo ? 'video' : 'audio'} recording',
                  style: TextStyle(fontWeight: FontWeight.w800, color: AppColors.ink)),
              const SizedBox(height: 2),
              Text(
                _mayPlay
                    ? "Enter the recording passkey to play it."
                    : "Protected for the customer's privacy: only an authorised admin can play it.",
                style: TextStyle(fontSize: 13, height: 1.35, color: AppColors.muted),
              ),
            ]),
          ),
          if (_mayPlay) ...[
            const SizedBox(width: 8),
            FilledButton.icon(onPressed: _unlock, icon: const Icon(Icons.play_arrow_rounded), label: const Text('Play')),
          ],
        ],
      ),
    );
  }
}

class _PasskeyDialog extends StatefulWidget {
  const _PasskeyDialog({required this.feedbackId});

  final int feedbackId;

  @override
  State<_PasskeyDialog> createState() => _PasskeyDialogState();
}

class _PasskeyDialogState extends State<_PasskeyDialog> {
  final _passkey = TextEditingController();
  bool _hide = true;
  bool _busy = false;
  String? _error;
  bool _waiting = false;
  String? _revealed;

  @override
  void initState() {
    super.initState();
    if (Session.user.value?.role == 'admin') {
      ApiService().recordingPasskeyWaiting().then((waiting) {
        if (mounted) setState(() => _waiting = waiting);
      });
    }
  }

  @override
  void dispose() {
    _passkey.dispose();
    super.dispose();
  }

  Future<void> _submit() async {
    if (_passkey.text.trim().isEmpty) return;
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      final r = await ApiService().authoriseRecording(widget.feedbackId, _passkey.text);
      if (mounted) Navigator.of(context).pop(r);
    } on ApiException catch (e) {
      if (mounted) setState(() => _error = e.message);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _reveal() async {
    try {
      final passkey = await ApiService().revealPasskey();
      if (!mounted) return;
      setState(() {
        _revealed = passkey;
        _passkey.text = passkey;
        _waiting = false;
      });
    } on ApiException catch (e) {
      if (mounted) setState(() => _error = e.message);
    }
  }

  @override
  Widget build(BuildContext context) {
    return AlertDialog(
      title: const Text('Recording passkey'),
      content: SingleChildScrollView(
        child: Column(mainAxisSize: MainAxisSize.min, crossAxisAlignment: CrossAxisAlignment.stretch, children: [
          Text("Recordings are encrypted. Enter the recording passkey from the Super Admin (the same for every product). Every attempt is logged.",
              style: TextStyle(fontSize: 13.5, height: 1.4, color: AppColors.ink2)),
          const SizedBox(height: 12),
          if (_waiting)
            Padding(
              padding: const EdgeInsets.only(bottom: 10),
              child: OutlinedButton.icon(
                onPressed: _reveal,
                icon: const Icon(Icons.key_rounded),
                label: const Text('Show the passkey sent to me'),
              ),
            ),
          if (_revealed != null)
            Container(
              margin: const EdgeInsets.only(bottom: 10),
              padding: const EdgeInsets.all(10),
              decoration: BoxDecoration(color: AppColors.warnSoft, borderRadius: BorderRadius.circular(10)),
              child: Text('Shown once and deleted from the server: note it somewhere safe. Never share it with survey persons.',
                  style: TextStyle(fontSize: 12.5, color: AppColors.warnDeep)),
            ),
          TextField(
            controller: _passkey,
            obscureText: _hide,
            autocorrect: false,
            enableSuggestions: false,
            textCapitalization: TextCapitalization.characters,
            onSubmitted: (_) => _submit(),
            decoration: InputDecoration(
              labelText: 'Passkey',
              suffixIcon: IconButton(
                tooltip: _hide ? 'Show' : 'Hide',
                icon: Icon(_hide ? Icons.visibility_outlined : Icons.visibility_off_outlined),
                onPressed: () => setState(() => _hide = !_hide),
              ),
            ),
          ),
          if (_error != null) ...[
            const SizedBox(height: 10),
            Text(_error!, style: TextStyle(color: AppColors.neg, fontSize: 13.5)),
          ],
        ]),
      ),
      actions: [
        TextButton(onPressed: () => Navigator.of(context).pop(), child: const Text('Cancel')),
        FilledButton(onPressed: _busy ? null : _submit, child: Text(_busy ? 'Checking…' : 'Play')),
      ],
    );
  }
}
