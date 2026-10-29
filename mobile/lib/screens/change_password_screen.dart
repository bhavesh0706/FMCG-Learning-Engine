import 'package:flutter/material.dart';

import '../main.dart';
import '../services/session.dart';
import 'onboarding_screens.dart';

/* ============================================================
   Choose a new password
   ============================================================
   Shown right after sign-in when the server requires a new password
   (users.must_change_password): the organization's first sign-in with the
   one-time password from app.tools.organization, or after a password reset.
   Until then the server refuses every other request ("Please choose your own
   password first."), so the app must not open any dashboard before this.
*/

class ChangePasswordScreen extends StatefulWidget {
  const ChangePasswordScreen({super.key});

  @override
  State<ChangePasswordScreen> createState() => _ChangePasswordScreenState();
}

class _ChangePasswordScreenState extends State<ChangePasswordScreen> {
  final _current = TextEditingController();
  final _next = TextEditingController();
  final _confirm = TextEditingController();
  bool _hide = true;
  bool _busy = false;
  String? _error;

  @override
  void dispose() {
    _current.dispose();
    _next.dispose();
    _confirm.dispose();
    super.dispose();
  }

  String? _validate() {
    if (_current.text.isEmpty) return 'Enter the password you signed in with.';
    final p = _next.text;
    // The server's rule (backend auth.check_password): 8+ characters, letters and numbers.
    if (p.length < 8 || !RegExp(r'[A-Za-z]').hasMatch(p) || !RegExp(r'\d').hasMatch(p)) {
      return 'Use at least 8 characters with letters and numbers.';
    }
    if (p == _current.text) return 'Choose a password different from the current one.';
    if (_confirm.text != p) return "The new passwords don't match.";
    return null;
  }

  Future<void> _save() async {
    FocusScope.of(context).unfocus();
    final problem = _validate();
    if (problem != null) {
      setState(() => _error = problem);
      return;
    }
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      await Session.changePassword(_current.text, _next.text);
      if (!mounted) return;
      goReplacingAll(context, nextScreen()); // now the account's own dashboard
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _busy = false;
        _error = '$e';
      });
    }
  }

  Future<void> _logout() async {
    await Session.logout();
    if (mounted) goReplacingAll(context, const WelcomeScreen());
  }

  Widget _field(TextEditingController c, String label, {TextInputAction action = TextInputAction.next}) => Padding(
        padding: const EdgeInsets.only(bottom: 14),
        child: TextField(
          controller: c,
          obscureText: _hide,
          textInputAction: action,
          onSubmitted: action == TextInputAction.done ? (_) => _save() : null,
          decoration: InputDecoration(
            labelText: label,
            suffixIcon: IconButton(
              tooltip: _hide ? 'Show passwords' : 'Hide passwords',
              icon: Icon(_hide ? Icons.visibility_outlined : Icons.visibility_off_outlined),
              onPressed: () => setState(() => _hide = !_hide),
            ),
          ),
        ),
      );

  @override
  Widget build(BuildContext context) {
    final name = Session.user.value?.name ?? '';
    return Scaffold(
      backgroundColor: AppColors.bg,
      body: SafeArea(
        child: ListView(
          padding: const EdgeInsets.fromLTRB(24, 32, 24, 24),
          children: [
            Text('Choose your own password', style: TextStyle(fontSize: 26, fontWeight: FontWeight.w800, color: AppColors.ink)),
            const SizedBox(height: 8),
            Text(
              '${name.isEmpty ? 'This account' : name} signed in with a one-time password. '
              'Choose your own password to continue. Other devices signed in to this account will be signed out.',
              style: TextStyle(color: AppColors.muted, height: 1.45),
            ),
            const SizedBox(height: 24),
            _field(_current, 'Current (one-time) password'),
            _field(_next, 'New password'),
            _field(_confirm, 'Repeat the new password', action: TextInputAction.done),
            Text('At least 8 characters, with letters and numbers.', style: TextStyle(fontSize: 12.5, color: AppColors.muted)),
            if (_error != null) ...[
              const SizedBox(height: 12),
              Text(_error!, style: TextStyle(color: AppColors.neg)),
            ],
            const SizedBox(height: 20),
            FilledButton(onPressed: _busy ? null : _save, child: Text(_busy ? 'Saving…' : 'Save and continue')),
            const SizedBox(height: 6),
            TextButton(onPressed: _busy ? null : _logout, child: const Text('Log out')),
          ],
        ),
      ),
    );
  }
}
