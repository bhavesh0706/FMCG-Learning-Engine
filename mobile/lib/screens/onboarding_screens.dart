import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:permission_handler/permission_handler.dart';

import '../main.dart';
import '../services/session.dart';
import '../services/survey_queue.dart';

/*
  First launch:  Splash -> Welcome -> Log in -> Permissions -> Home
  (Accounts are created by the admin on the web Team page.)
  Later:         Splash -> Home  (or the step still missing)
*/

const appName = 'FMCG Field Survey';
const appTagline = 'Customer feedback, captured in the field';

/// Where to go after the splash (or after signing in).
Widget nextScreen() {
  if (!Session.signedIn) return const WelcomeScreen();
  if (!AppProfile.permissionsDone) return const PermissionsScreen();
  return const HomeShell();
}

Route<void> _fadeTo(Widget page) => PageRouteBuilder(
      transitionDuration: const Duration(milliseconds: 450),
      pageBuilder: (_, __, ___) => page,
      transitionsBuilder: (_, a, __, child) => FadeTransition(opacity: a, child: child),
    );

void goReplacingAll(BuildContext context, Widget page) {
  Navigator.of(context).pushAndRemoveUntil(_fadeTo(page), (_) => false);
}

/* ================================================================
   Logo
   ================================================================ */

class AppLogo extends StatelessWidget {
  const AppLogo({super.key, this.size = 88});

  final double size;

  @override
  Widget build(BuildContext context) {
    return Container(
      width: size,
      height: size,
      decoration: BoxDecoration(
        borderRadius: BorderRadius.circular(size * 0.28),
        gradient: const LinearGradient(
          begin: Alignment.topLeft,
          end: Alignment.bottomRight,
          colors: [AppColors.sky, AppColors.teal],
        ),
        boxShadow: [
          BoxShadow(color: AppColors.sky.withValues(alpha: 0.35), blurRadius: size * 0.4, offset: Offset(0, size * 0.12)),
        ],
      ),
      child: Stack(
        alignment: Alignment.center,
        children: [
          Icon(Icons.chat_bubble_rounded, size: size * 0.62, color: AppColors.navy900),
          Padding(
            padding: EdgeInsets.only(bottom: size * 0.06),
            child: Icon(Icons.mic_rounded, size: size * 0.34, color: AppColors.mist),
          ),
        ],
      ),
    );
  }
}

/* ================================================================
   1. Splash: animated logo and name
   ================================================================ */

class SplashScreen extends StatefulWidget {
  const SplashScreen({super.key});

  @override
  State<SplashScreen> createState() => _SplashScreenState();
}

class _SplashScreenState extends State<SplashScreen> with SingleTickerProviderStateMixin {
  late final AnimationController _c = AnimationController(vsync: this, duration: const Duration(milliseconds: 1600));
  late final Animation<double> _logoScale =
      CurvedAnimation(parent: _c, curve: const Interval(0, 0.55, curve: Curves.easeOutBack));
  late final Animation<double> _logoFade = CurvedAnimation(parent: _c, curve: const Interval(0, 0.35, curve: Curves.easeOut));
  late final Animation<double> _nameFade = CurvedAnimation(parent: _c, curve: const Interval(0.35, 0.75, curve: Curves.easeOut));
  late final Animation<double> _tagFade = CurvedAnimation(parent: _c, curve: const Interval(0.6, 1, curve: Curves.easeOut));

  @override
  void initState() {
    super.initState();
    _start();
  }

  Future<void> _start() async {
    await _c.forward();
    await Future.delayed(const Duration(milliseconds: 350));
    if (mounted) Navigator.of(context).pushReplacement(_fadeTo(nextScreen()));
  }

  @override
  void dispose() {
    _c.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return AnnotatedRegion<SystemUiOverlayStyle>(
      value: SystemUiOverlayStyle.light,
      child: Scaffold(
        body: Container(
          decoration: const BoxDecoration(
            gradient: LinearGradient(
              begin: Alignment.topCenter,
              end: Alignment.bottomCenter,
              colors: [AppColors.navy900, AppColors.navy],
            ),
          ),
          child: Center(
            child: AnimatedBuilder(
              animation: _c,
              builder: (context, _) => Column(
                mainAxisSize: MainAxisSize.min,
                children: [
                  Opacity(
                    opacity: _logoFade.value,
                    child: Transform.scale(scale: 0.6 + 0.4 * _logoScale.value, child: const AppLogo(size: 104)),
                  ),
                  const SizedBox(height: 26),
                  Opacity(
                    opacity: _nameFade.value,
                    child: Transform.translate(
                      offset: Offset(0, 16 * (1 - _nameFade.value)),
                      child: const Text(
                        appName,
                        style: TextStyle(color: Colors.white, fontSize: 28, fontWeight: FontWeight.w800, letterSpacing: -0.5),
                      ),
                    ),
                  ),
                  const SizedBox(height: 8),
                  Opacity(
                    opacity: _tagFade.value,
                    child: const Text(appTagline, style: TextStyle(color: AppColors.mist, fontSize: 15)),
                  ),
                ],
              ),
            ),
          ),
        ),
      ),
    );
  }
}

/* ================================================================
   Shared layout: navy header with logo, curved sheet, footer link
   ================================================================ */

class _SheetScaffold extends StatelessWidget {
  const _SheetScaffold({required this.title, required this.subtitle, required this.child, this.footer, this.onBack});

  final String title;
  final String subtitle;
  final Widget child;
  final Widget? footer;
  final VoidCallback? onBack;

  @override
  Widget build(BuildContext context) {
    final h = MediaQuery.sizeOf(context).height;
    return AnnotatedRegion<SystemUiOverlayStyle>(
      value: SystemUiOverlayStyle.light,
      child: Scaffold(
        backgroundColor: AppColors.navy900,
        body: SafeArea(
          bottom: false,
          child: Column(
            children: [
              SizedBox(
                height: h * 0.2,
                child: Stack(
                  children: [
                    const Center(child: AppLogo(size: 72)),
                    if (onBack != null)
                      Positioned(
                        left: 4,
                        top: 4,
                        child: IconButton(
                          onPressed: onBack,
                          tooltip: 'Back',
                          icon: const Icon(Icons.arrow_back_rounded, color: Colors.white),
                        ),
                      ),
                  ],
                ),
              ),
              Expanded(
                child: Container(
                  width: double.infinity,
                  decoration: BoxDecoration(
                    color: AppColors.surface,
                    borderRadius: const BorderRadius.only(topLeft: Radius.circular(40), topRight: Radius.circular(40)),
                  ),
                  child: SingleChildScrollView(
                    padding: const EdgeInsets.fromLTRB(24, 30, 24, 24),
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.stretch,
                      children: [
                        Text(title,
                            textAlign: TextAlign.center,
                            style: TextStyle(fontSize: 28, fontWeight: FontWeight.w800, letterSpacing: -0.5, color: AppColors.ink)),
                        const SizedBox(height: 8),
                        Text(subtitle,
                            textAlign: TextAlign.center,
                            style: TextStyle(fontSize: 15, height: 1.45, color: AppColors.muted)),
                        const SizedBox(height: 28),
                        child,
                      ],
                    ),
                  ),
                ),
              ),
              if (footer != null)
                Container(
                  width: double.infinity,
                  color: AppColors.surface,
                  child: SafeArea(top: false, child: Padding(padding: const EdgeInsets.only(bottom: 12), child: footer!)),
                ),
            ],
          ),
        ),
      ),
    );
  }
}

/* ================================================================
   2. Welcome
   ================================================================ */

class WelcomeScreen extends StatelessWidget {
  const WelcomeScreen({super.key});

  @override
  Widget build(BuildContext context) {
    return _SheetScaffold(
      title: "Let's get started",
      subtitle: 'Collect customer feedback in the field: product, customer details, answers and their voice or video.',
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          for (final f in [
            (Icons.checklist_rounded, 'Quick surveys', 'Product, customer and questions in a few taps'),
            (Icons.mic_rounded, 'Voice or video feedback', 'Analyzed automatically, in any Indian language'),
            (Icons.cloud_off_rounded, 'Works offline', 'Surveys upload by themselves when you\'re back online'),
          ])
            Padding(
              padding: const EdgeInsets.only(bottom: 14),
              child: Row(
                children: [
                  Container(
                    width: 44,
                    height: 44,
                    decoration: BoxDecoration(color: AppColors.brandSoft, borderRadius: BorderRadius.circular(12)),
                    child: Icon(f.$1, color: AppColors.brand),
                  ),
                  const SizedBox(width: 14),
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(f.$2, style: TextStyle(fontSize: 15.5, fontWeight: FontWeight.w700, color: AppColors.ink)),
                        Text(f.$3, style: TextStyle(fontSize: 13.5, color: AppColors.muted)),
                      ],
                    ),
                  ),
                ],
              ),
            ),
          const SizedBox(height: 16),
          FilledButton(
            onPressed: () => Navigator.of(context).push(MaterialPageRoute(builder: (_) => const AuthScreen(signup: false))),
            child: const Text('Log in'),
          ),
          const SizedBox(height: 12),
          Text(
            'No account yet? Your admin creates it and gives you your login details.',
            textAlign: TextAlign.center,
            style: TextStyle(fontSize: 13.5, height: 1.4, color: AppColors.muted),
          ),
        ],
      ),
    );
  }
}

/* ================================================================
   3. Log in / Create account
   ================================================================ */

class AuthScreen extends StatefulWidget {
  const AuthScreen({super.key, required this.signup});

  final bool signup;

  @override
  State<AuthScreen> createState() => _AuthScreenState();
}

class _AuthScreenState extends State<AuthScreen> {
  late bool _signup = widget.signup;
  final _name = TextEditingController();
  final _id = TextEditingController();
  final _password = TextEditingController();
  final _confirm = TextEditingController();
  bool _hide = true;
  bool _busy = false;
  String? _error;

  @override
  void dispose() {
    for (final c in [_name, _id, _password, _confirm]) {
      c.dispose();
    }
    super.dispose();
  }

  String? _validate() {
    if (_signup && _name.text.trim().length < 2) return 'Enter your full name.';
    final id = _id.text.trim();
    if (id.contains('@')) {
      if (!RegExp(r'^[^@\s]+@[^@\s]+\.[^@\s]+$').hasMatch(id)) return 'Enter a valid email address.';
    } else {
      var digits = id.replaceAll(RegExp(r'\D'), '');
      if (digits.length == 12 && digits.startsWith('91')) digits = digits.substring(2);
      if (digits.length != 10) return 'Enter a 10-digit mobile number or an email address.';
    }
    if (_password.text.isEmpty) return 'Enter your password.';
    if (_signup) {
      final p = _password.text;
      if (p.length < 8 || !RegExp(r'[A-Za-z]').hasMatch(p) || !RegExp(r'\d').hasMatch(p)) {
        return 'Use at least 8 characters with letters and numbers.';
      }
      if (_confirm.text != p) return "The passwords don't match.";
    }
    return null;
  }

  Future<void> _submit() async {
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
      if (_signup) {
        await Session.signup(_name.text, _id.text, _password.text);
      } else {
        await Session.login(_id.text, _password.text);
      }
      if (!mounted) return;
      ServerHistory.refresh();
      goReplacingAll(context, nextScreen());
    } catch (e) {
      if (mounted) setState(() => _error = '$e');
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  void _forgot() {
    showDialog<void>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('Forgot your password?'),
        content: const Text('Ask your supervisor or admin to reset it for you. Then log in with the new password.'),
        actions: [TextButton(onPressed: () => Navigator.of(context).pop(), child: const Text('OK'))],
      ),
    );
  }

  Widget _field(TextEditingController c, String label,
      {TextInputType? keyboard, bool password = false, List<String>? autofill, TextInputAction action = TextInputAction.next}) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 14),
      child: TextField(
        controller: c,
        keyboardType: keyboard,
        obscureText: password && _hide,
        autofillHints: autofill,
        textInputAction: action,
        textCapitalization: c == _name ? TextCapitalization.words : TextCapitalization.none,
        onSubmitted: action == TextInputAction.done ? (_) => _submit() : null,
        onChanged: (_) {
          if (_error != null) setState(() => _error = null);
        },
        decoration: InputDecoration(
          labelText: label,
          suffixIcon: password
              ? IconButton(
                  tooltip: _hide ? 'Show password' : 'Hide password',
                  icon: Icon(_hide ? Icons.visibility_outlined : Icons.visibility_off_outlined),
                  onPressed: () => setState(() => _hide = !_hide),
                )
              : null,
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    return _SheetScaffold(
      title: _signup ? 'Create account' : 'Log in',
      subtitle: _signup
          ? 'Your name is added to every survey you do.'
          : 'Sign in with the mobile number or email your admin gave you.',
      onBack: () => Navigator.of(context).maybePop(),
      // Accounts are created by the admin (web Team page); self sign-up is off.
      footer: Text(
        'No account yet? Ask your admin to add you.',
        textAlign: TextAlign.center,
        style: TextStyle(fontSize: 13.5, color: AppColors.muted),
      ),
      child: AutofillGroup(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            if (_signup) _field(_name, 'Full name', keyboard: TextInputType.name, autofill: const [AutofillHints.name]),
            _field(_id, 'Mobile number or email',
                keyboard: TextInputType.emailAddress, autofill: const [AutofillHints.telephoneNumber, AutofillHints.email]),
            _field(_password, _signup ? 'Password (8+ letters and numbers)' : 'Password',
                password: true,
                autofill: [_signup ? AutofillHints.newPassword : AutofillHints.password],
                action: _signup ? TextInputAction.next : TextInputAction.done),
            if (_signup) _field(_confirm, 'Confirm password', password: true, action: TextInputAction.done),
            if (!_signup)
              Align(
                alignment: Alignment.centerRight,
                child: TextButton(onPressed: _forgot, child: const Text('Forgot password?')),
              ),
            if (_error != null)
              Container(
                margin: const EdgeInsets.only(top: 4, bottom: 12),
                padding: const EdgeInsets.all(12),
                decoration: BoxDecoration(color: AppColors.negSoft, borderRadius: BorderRadius.circular(12)),
                child: Text(_error!, style: TextStyle(color: AppColors.neg, fontSize: 14, height: 1.35)),
              ),
            const SizedBox(height: 8),
            FilledButton(
              onPressed: _busy ? null : _submit,
              child: _busy
                  ? SizedBox(width: 22, height: 22, child: CircularProgressIndicator(strokeWidth: 2.5, color: AppColors.onBrand))
                  : Text(_signup ? 'Create account' : 'Log in'),
            ),
          ],
        ),
      ),
    );
  }
}

/* ================================================================
   4. Permissions
   ================================================================ */

class _PermissionInfo {
  const _PermissionInfo(this.permission, this.icon, this.title, this.why, {this.required = false});

  final Permission permission;
  final IconData icon;
  final String title;
  final String why;
  final bool required;
}

const _permissions = [
  _PermissionInfo(Permission.microphone, Icons.mic_rounded, 'Microphone', "Record the customer's voice feedback.", required: true),
  _PermissionInfo(Permission.camera, Icons.videocam_rounded, 'Camera', 'Record video feedback showing the product.'),
  _PermissionInfo(Permission.locationWhenInUse, Icons.location_on_rounded, 'Location',
      'Add where each survey was done. Only while the app is open.'),
  _PermissionInfo(Permission.notification, Icons.notifications_rounded, 'Notifications',
      'Tell you when a survey has been uploaded and analyzed.'),
];

class PermissionsScreen extends StatefulWidget {
  const PermissionsScreen({super.key, this.fromSettings = false});

  /// Opened again from Settings: just go back when done.
  final bool fromSettings;

  @override
  State<PermissionsScreen> createState() => _PermissionsScreenState();
}

class _PermissionsScreenState extends State<PermissionsScreen> with WidgetsBindingObserver {
  final Map<Permission, PermissionStatus> _status = {};
  bool _busy = false;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    _refresh();
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    super.dispose();
  }

  // Back from the phone's Settings app: read the statuses again.
  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state == AppLifecycleState.resumed) _refresh();
  }

  Future<void> _refresh() async {
    for (final p in _permissions) {
      _status[p.permission] = await p.permission.status;
    }
    if (mounted) setState(() {});
  }

  Future<void> _request(_PermissionInfo p) async {
    final current = _status[p.permission];
    if (current != null && current.isPermanentlyDenied) {
      await openAppSettings();
      return;
    }
    final result = await p.permission.request();
    if (mounted) setState(() => _status[p.permission] = result);
  }

  Future<void> _allowAllAndContinue() async {
    setState(() => _busy = true);
    for (final p in _permissions) {
      final s = _status[p.permission];
      if (s == null || (!s.isGranted && !s.isPermanentlyDenied && !s.isRestricted)) {
        final r = await p.permission.request();
        _status[p.permission] = r;
        if (mounted) setState(() {});
      }
    }
    if (mounted) setState(() => _busy = false);
    await _finish();
  }

  Future<void> _finish() async {
    AppProfile.permissionsDone = true;
    await AppProfile.save();
    if (!mounted) return;
    if (widget.fromSettings) {
      Navigator.of(context).pop();
    } else {
      goReplacingAll(context, const HomeShell());
    }
  }

  @override
  Widget build(BuildContext context) {
    final allGranted = _permissions.every((p) => _status[p.permission]?.isGranted == true);
    return _SheetScaffold(
      title: 'Allow access',
      subtitle: 'These let you record feedback and tag surveys. You can change them any time in Settings.',
      onBack: widget.fromSettings ? () => Navigator.of(context).pop() : null,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          for (final p in _permissions) _PermissionCard(info: p, status: _status[p.permission], onAllow: () => _request(p)),
          const SizedBox(height: 12),
          FilledButton(
            onPressed: _busy ? null : (allGranted ? _finish : _allowAllAndContinue),
            child: Text(allGranted ? 'Continue' : (_busy ? 'Asking…' : 'Allow and continue')),
          ),
          if (!allGranted) ...[
            const SizedBox(height: 6),
            TextButton(onPressed: _busy ? null : _finish, child: const Text('Not now')),
          ],
        ],
      ),
    );
  }
}

class _PermissionCard extends StatelessWidget {
  const _PermissionCard({required this.info, required this.status, required this.onAllow});

  final _PermissionInfo info;
  final PermissionStatus? status;
  final VoidCallback onAllow;

  @override
  Widget build(BuildContext context) {
    final granted = status?.isGranted == true || status?.isLimited == true;
    final blocked = status?.isPermanentlyDenied == true;
    return Container(
      margin: const EdgeInsets.only(bottom: 10),
      padding: const EdgeInsets.all(14),
      decoration: BoxDecoration(
        color: granted ? AppColors.posSoft : AppColors.bg,
        borderRadius: BorderRadius.circular(16),
        border: Border.all(color: granted ? AppColors.pos.withValues(alpha: 0.4) : AppColors.line),
      ),
      child: Row(
        children: [
          Container(
            width: 44,
            height: 44,
            decoration: BoxDecoration(color: granted ? AppColors.pos : AppColors.brandSoft, borderRadius: BorderRadius.circular(12)),
            child: Icon(granted ? Icons.check_rounded : info.icon, color: granted ? Colors.white : AppColors.brand),
          ),
          const SizedBox(width: 12),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  children: [
                    Text(info.title, style: TextStyle(fontSize: 15.5, fontWeight: FontWeight.w700, color: AppColors.ink)),
                    if (info.required) ...[
                      const SizedBox(width: 6),
                      Text('needed', style: TextStyle(fontSize: 12, color: AppColors.warn, fontWeight: FontWeight.w700)),
                    ],
                  ],
                ),
                const SizedBox(height: 2),
                Text(info.why, style: TextStyle(fontSize: 13, height: 1.35, color: AppColors.muted)),
              ],
            ),
          ),
          const SizedBox(width: 8),
          if (granted)
            Text('Allowed', style: TextStyle(fontWeight: FontWeight.w700, color: AppColors.pos))
          else
            TextButton(onPressed: onAllow, child: Text(blocked ? 'Settings' : 'Allow')),
        ],
      ),
    );
  }
}