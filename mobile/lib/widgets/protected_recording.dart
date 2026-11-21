import 'package:flutter/material.dart';

import '../main.dart';
import '../services/api_service.dart';
import '../services/session.dart';
import 'media_player.dart';

/*
  A consumer recording on the analysis page. Recordings are encrypted (services/recording_vault.dart,
  backend services/recording_crypto.py):

    survey person   never plays it (a note says it is protected)
    Admin, Super Admin  play it straight away (no passkey): the server checks the role and streams
                        it decrypted (10-minute link, never saved on the phone); every play is logged.
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
  bool _busy = false;
  String? _error;

  bool get _isAdmin => Session.user.value?.role == 'admin' || Session.isClient;
  bool get _isSurveyPerson => !_isAdmin && Session.user.value?.role == 'executive';
  bool get _mayPlay => _isAdmin || _isSurveyPerson;

  @override
  void initState() {
    super.initState();
    if (_isAdmin) _play(); // Super Admin and Admins: the player appears straight away
  }

  // Super Admin and Admins play it straight away; a survey person enters the passkey from their Admin.
  Future<void> _play() async {
    if (_isSurveyPerson) {
      final r = await showDialog<({String url, bool isVideo})>(
        context: context,
        builder: (_) => _PasskeyDialog(feedbackId: widget.feedbackId),
      );
      if (r != null && mounted) {
        setState(() {
          _url = r.url;
          _isVideo = r.isVideo;
        });
      }
      return;
    }
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      final r = await ApiService().authoriseRecording(widget.feedbackId, '');
      if (mounted) {
        setState(() {
          _url = r.url;
          _isVideo = r.isVideo;
        });
      }
    } on ApiException catch (e) {
      if (mounted) setState(() => _error = e.message);
    } finally {
      if (mounted) setState(() => _busy = false);
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
              Text('${widget.isVideo ? 'Video' : 'Audio'} recording', style: TextStyle(fontWeight: FontWeight.w800, color: AppColors.ink)),
              const SizedBox(height: 2),
              Text(
                _isAdmin
                    ? 'Stored encrypted. Every play is logged.'
                    : _isSurveyPerson
                        ? 'Enter the recording passkey from your Admin to play it.'
                        : "Protected for the customer's privacy.",
                style: TextStyle(fontSize: 13, height: 1.35, color: AppColors.muted),
              ),
              if (_error != null) Text(_error!, style: TextStyle(fontSize: 12.5, color: AppColors.neg)),
            ]),
          ),
          if (_isSurveyPerson || (_isAdmin && _error != null)) ...[
            const SizedBox(width: 8),
            FilledButton.icon(
              onPressed: _busy ? null : _play,
              icon: const Icon(Icons.play_arrow_rounded),
              label: Text(_busy ? 'Opening…' : 'Play'),
            ),
          ],
        ],
      ),
    );
  }
}

/// Survey person: the passkey their Admin sent (shown once inside the app) or typed in.
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
  bool _waiting = false;
  bool _revealed = false;
  String? _error;

  @override
  void initState() {
    super.initState();
    ApiService().recordingPasskeyWaiting().then((w) {
      if (mounted) setState(() => _waiting = w);
    });
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
      final r = await ApiService().authoriseRecording(widget.feedbackId, _passkey.text.trim());
      if (mounted) Navigator.of(context).pop(r);
    } on ApiException catch (e) {
      if (mounted) setState(() => _error = e.message);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _reveal() async {
    try {
      final k = await ApiService().revealPasskey();
      if (!mounted) return;
      setState(() {
        _passkey.text = k;
        _hide = false;
        _waiting = false;
        _revealed = true;
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
          Text('Enter the recording passkey your Admin gave you.', style: TextStyle(fontSize: 13.5, height: 1.4, color: AppColors.ink2)),
          const SizedBox(height: 12),
          if (_waiting)
            Padding(
              padding: const EdgeInsets.only(bottom: 10),
              child: OutlinedButton.icon(onPressed: _reveal, icon: const Icon(Icons.key_rounded), label: const Text('Show the passkey sent to me')),
            ),
          if (_revealed)
            Padding(
              padding: const EdgeInsets.only(bottom: 10),
              child: Text('Shown once and deleted from the server: keep it somewhere safe.',
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
              hintText: 'e.g. K7QM-9XPD-2RTF-H4WB',
              suffixIcon: IconButton(
                tooltip: _hide ? 'Show' : 'Hide',
                icon: Icon(_hide ? Icons.visibility_rounded : Icons.visibility_off_rounded),
                onPressed: () => setState(() => _hide = !_hide),
              ),
            ),
          ),
          if (_error != null) ...[
            const SizedBox(height: 8),
            Text(_error!, style: TextStyle(fontSize: 12.5, color: AppColors.neg)),
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
