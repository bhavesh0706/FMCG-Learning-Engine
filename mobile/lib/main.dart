import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import 'package:file_picker/file_picker.dart';

import 'screens/capture_screens.dart';
import 'screens/home_screens.dart';
import 'screens/notifications_screen.dart';
import 'screens/onboarding_screens.dart';
import 'services/api_service.dart';
import 'services/notification_service.dart';
import 'services/recording_store.dart';
import 'services/session.dart';
import 'services/survey_queue.dart';
import 'widgets/media_player.dart';

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();
  await AppProfile.load();
  await Session.load();
  SurveyQueue.load(); // also resumes any uploads left from last time
  if (Session.signedIn) ServerHistory.refresh(); // surveys from the server (web, other phones)
  runApp(const FmcgLearningApp());
}

/* ============================================================
   Colours (same palette as the web app)
   ============================================================ */

class AppColors {
  AppColors._();

  /// Set by the app from the light/dark setting. Screens read colours through
  /// the getters below, so every colour follows the current mode.
  static bool isDark = false;

  static Color _m(Color light, Color dark) => isDark ? dark : light;

  // ---- Brand palette (same in both modes) ----
  static const navy900 = Color(0xFF001D39);
  static const navy = Color(0xFF0A4174);
  static const steel = Color(0xFF49769F);
  static const teal = Color(0xFF4E8EA2);
  static const tealLight = Color(0xFF6EA2B3);
  static const sky = Color(0xFF7BBDE8);
  static const mist = Color(0xFFBDD8E9);
  static const gold = Color(0xFFE0A526);

  // ---- Surfaces and text ----
  static Color get bg => _m(const Color(0xFFF2F6F9), const Color(0xFF00121F));
  static Color get surface => _m(Colors.white, const Color(0xFF0A2236));
  static Color get ink => _m(const Color(0xFF0B1B2B), const Color(0xFFE6F0F7));
  static Color get ink2 => _m(const Color(0xFF26394D), const Color(0xFFC3D5E3));
  static Color get muted => _m(const Color(0xFF55728F), const Color(0xFF8DAEC5));
  static Color get line => _m(const Color(0xFFD7E3EC), const Color(0xFF1C3A54));
  static Color get scrim => _m(const Color(0x88F2F6F9), const Color(0x8800121F));

  // ---- Brand roles ----
  static Color get brand => _m(navy, sky);
  static Color get onBrand => _m(Colors.white, navy900);
  static Color get brandSoft => _m(const Color(0xFFDDEBF5), const Color(0xFF113656));
  static Color get accent => _m(teal, tealLight);

  /// Deep header and hero cards: navy in both modes, always with white text.
  static Color get brandDark => navy900;
  static Color get hero => navy;

  // ---- Recording ----
  static Color get record => _m(const Color(0xFFE5484D), const Color(0xFFFF6B70));
  static Color get video => _m(const Color(0xFF2F5BA8), const Color(0xFF8FB3F2));
  static Color get videoSoft => _m(const Color(0xFFE6EEFB), const Color(0xFF14294A));

  // ---- Status ----
  static Color get pos => _m(const Color(0xFF1E8A5A), const Color(0xFF4CC38A));
  static Color get posSoft => _m(const Color(0xFFE4F3EB), const Color(0xFF0E3326));
  static Color get warn => _m(const Color(0xFF8A560C), const Color(0xFFE9B04D));
  static Color get warnDeep => _m(const Color(0xFF7A4E0E), const Color(0xFFF2C77A));
  static Color get warnSoft => _m(const Color(0xFFFBF1E1), const Color(0xFF33260E));
  static Color get warnLine => _m(const Color(0xFFE7C88E), const Color(0xFF5C4419));
  static Color get neg => _m(const Color(0xFFC8414F), const Color(0xFFFF7B86));
  static Color get negDeep => _m(const Color(0xFF7A3A40), const Color(0xFFFFB3B9));
  static Color get negSoft => _m(const Color(0xFFFBE9EB), const Color(0xFF3A1519));
  static Color get negLine => _m(const Color(0xFFF3C4C6), const Color(0xFF6B2A31));
  static Color get info => _m(const Color(0xFF2F5BA8), const Color(0xFF8FB3F2));
  static Color get infoSoft => _m(const Color(0xFFE6EEFB), const Color(0xFF14294A));
  static Color get infoLine => _m(const Color(0xFFC3D0F0), const Color(0xFF2A4475));
  static Color get neutralSoft => _m(const Color(0xFFE6EEF3), const Color(0xFF15304A));
  static Color get neutralBar => _m(const Color(0xFFA9BAC7), const Color(0xFF4A6780));
  static Color get starEmpty => _m(const Color(0xFFD3DEE6), const Color(0xFF2A4560));
}

/* ============================================================
   App & theme
   ============================================================ */

/// Light or dark theme built from the palette.
ThemeData buildAppTheme(bool dark) {
  final previous = AppColors.isDark;
  AppColors.isDark = dark; // read the right set of colours while building
  final scheme = ColorScheme.fromSeed(
    seedColor: AppColors.navy,
    brightness: dark ? Brightness.dark : Brightness.light,
    primary: AppColors.brand,
    onPrimary: AppColors.onBrand,
    secondary: AppColors.accent,
    surface: AppColors.surface,
    onSurface: AppColors.ink,
    error: AppColors.neg,
  );
  final base = ThemeData(useMaterial3: true, colorScheme: scheme, scaffoldBackgroundColor: AppColors.bg);
  final theme = base.copyWith(
    textTheme: base.textTheme.apply(bodyColor: AppColors.ink, displayColor: AppColors.ink),
    dividerColor: AppColors.line,
    appBarTheme: AppBarTheme(
      backgroundColor: AppColors.bg,
      foregroundColor: AppColors.ink,
      surfaceTintColor: Colors.transparent,
      elevation: 0,
    ),
    filledButtonTheme: FilledButtonThemeData(
      style: FilledButton.styleFrom(
        backgroundColor: AppColors.brand,
        foregroundColor: AppColors.onBrand,
        minimumSize: const Size(0, 40),
        textStyle: const TextStyle(fontSize: 14, fontWeight: FontWeight.w600),
        padding: const EdgeInsets.symmetric(horizontal: 16),
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(8)),
      ),
    ),
    outlinedButtonTheme: OutlinedButtonThemeData(
      style: OutlinedButton.styleFrom(
        foregroundColor: AppColors.ink,
        minimumSize: const Size(0, 40),
        side: BorderSide(color: AppColors.line),
        textStyle: const TextStyle(fontSize: 14, fontWeight: FontWeight.w600),
        padding: const EdgeInsets.symmetric(horizontal: 16),
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(8)),
      ),
    ),
    // Size standard: medium buttons are 40 high (small 32 via styleFrom); Flutter still
    // pads the touch area to 48. Icons: 16 inline, 20 standalone, 24 feature tiles.
    textButtonTheme: TextButtonThemeData(
      style: TextButton.styleFrom(foregroundColor: AppColors.brand, minimumSize: const Size(0, 40)),
    ),
    inputDecorationTheme: InputDecorationTheme(
      filled: true,
      fillColor: AppColors.surface,
      labelStyle: TextStyle(color: AppColors.muted),
      hintStyle: TextStyle(color: AppColors.muted),
      border: OutlineInputBorder(borderRadius: BorderRadius.circular(14), borderSide: BorderSide(color: AppColors.line)),
      enabledBorder: OutlineInputBorder(borderRadius: BorderRadius.circular(14), borderSide: BorderSide(color: AppColors.line)),
      focusedBorder: OutlineInputBorder(borderRadius: BorderRadius.circular(14), borderSide: BorderSide(color: AppColors.brand, width: 2)),
    ),
    chipTheme: ChipThemeData(
      backgroundColor: AppColors.surface,
      selectedColor: AppColors.brandSoft,
      side: BorderSide(color: AppColors.line),
      labelStyle: TextStyle(color: AppColors.ink2),
    ),
    navigationBarTheme: NavigationBarThemeData(
      height: 68,
      backgroundColor: AppColors.surface,
      surfaceTintColor: Colors.transparent,
      indicatorColor: AppColors.brandSoft,
      iconTheme: WidgetStateProperty.resolveWith(
        (states) => IconThemeData(color: states.contains(WidgetState.selected) ? AppColors.brand : AppColors.muted),
      ),
      labelTextStyle: WidgetStateProperty.resolveWith(
        (states) => TextStyle(
          fontSize: 12.5,
          fontWeight: states.contains(WidgetState.selected) ? FontWeight.w700 : FontWeight.w500,
          color: states.contains(WidgetState.selected) ? AppColors.brand : AppColors.muted,
        ),
      ),
    ),
    dialogTheme: DialogThemeData(backgroundColor: AppColors.surface),
    bottomSheetTheme: BottomSheetThemeData(backgroundColor: AppColors.surface),
    snackBarTheme: const SnackBarThemeData(behavior: SnackBarBehavior.floating),
  );
  AppColors.isDark = previous;
  return theme;
}

ThemeMode _themeModeFrom(String v) => v == 'dark' ? ThemeMode.dark : (v == 'light' ? ThemeMode.light : ThemeMode.system);

class FmcgLearningApp extends StatefulWidget {
  const FmcgLearningApp({super.key});

  @override
  State<FmcgLearningApp> createState() => _FmcgLearningAppState();
}

class _FmcgLearningAppState extends State<FmcgLearningApp> with WidgetsBindingObserver {
  late final ThemeData _light = buildAppTheme(false);
  late final ThemeData _dark = buildAppTheme(true);

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    AppProfile.themeMode.addListener(_onThemeChanged);
  }

  @override
  void dispose() {
    AppProfile.themeMode.removeListener(_onThemeChanged);
    WidgetsBinding.instance.removeObserver(this);
    super.dispose();
  }

  // The phone switched between light and dark (when following the system).
  @override
  void didChangePlatformBrightness() => _onThemeChanged();

  void _onThemeChanged() {
    if (!mounted) return;
    setState(() {});
    // Every screen reads AppColors directly, so ask all of them to repaint.
    WidgetsBinding.instance.addPostFrameCallback((_) {
      void rebuild(Element e) {
        e.markNeedsBuild();
        e.visitChildren(rebuild);
      }

      if (mounted) (context as Element).visitChildren(rebuild);
    });
  }

  @override
  Widget build(BuildContext context) {
    final mode = _themeModeFrom(AppProfile.themeMode.value);
    final platformDark = WidgetsBinding.instance.platformDispatcher.platformBrightness == Brightness.dark;
    AppColors.isDark = mode == ThemeMode.dark || (mode == ThemeMode.system && platformDark);

    return MaterialApp(
      debugShowCheckedModeBanner: false,
      title: 'FMCG Field Survey',
      theme: _light,
      darkTheme: _dark,
      themeMode: mode,
      // No cross-fade between themes: every colour switches in the same frame.
      themeAnimationDuration: Duration.zero,
      home: const SplashScreen(),
    );
  }
}

/* ============================================================
   Shell: app bar + bottom navigation
   ============================================================ */

class HomeShell extends StatefulWidget {
  const HomeShell({super.key});

  @override
  State<HomeShell> createState() => _HomeShellState();
}

class _HomeShellState extends State<HomeShell> with WidgetsBindingObserver {
  int _tab = 0; // 0 = Home, 1 = History (Survey opens as its own screen)

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    Session.user.addListener(_onUserChanged);
    Notifications.arrived.addListener(_onNotification);
    Notifications.start(); // bell count, checked every 30 s while the app is open
    _refreshAccount();
  }

  @override
  void dispose() {
    Notifications.arrived.removeListener(_onNotification);
    Notifications.stop();
    Session.user.removeListener(_onUserChanged);
    WidgetsBinding.instance.removeObserver(this);
    super.dispose();
  }

  // Back in the app: an admin may have changed this executive's permissions.
  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state == AppLifecycleState.resumed) {
      _refreshAccount();
      Notifications.start(); // checks at once, then every 30 s
    } else if (state == AppLifecycleState.paused) {
      Notifications.stop(); // no checks while the app is in the background
    }
  }

  // A new notification arrived while the app is open: say so, with a shortcut.
  void _onNotification() {
    final n = Notifications.arrived.value;
    if (n == null || !mounted) return;
    if (n.kind == AppNotification.productAssigned) {
      AppProfile.refreshRecentProducts(); // the new product appears in the survey list
    }
    ScaffoldMessenger.maybeOf(context)
      ?..hideCurrentSnackBar()
      ..showSnackBar(SnackBar(
        content: Text(n.title),
        action: SnackBarAction(
          label: 'View',
          onPressed: () => Navigator.of(context).push(
            MaterialPageRoute(builder: (_) => const NotificationsPage()),
          ),
        ),
      ));
  }

  Future<void> _refreshAccount() async {
    await Session.refreshMe();
    await Session.refreshStats();
    if (Session.canViewHistory) ServerHistory.refresh();
    SurveyQueue.reconcileWithServer(); // drop surveys deleted on the server
    AppProfile.refreshRecentProducts(); // drop products the admin gave to someone else
    SurveyQueue.process(); // resume uploads and running analyses
  }

  void _onUserChanged() {
    if (!mounted) return;
    if (Session.user.value == null) {
      Notifications.clear(); // the next account starts with an empty bell
      // Signed out by the server (account disabled or login expired).
      final reason = Session.signedOutReason.value;
      goReplacingAll(context, const WelcomeScreen());
      if (reason != null) {
        ScaffoldMessenger.maybeOf(context)?.showSnackBar(SnackBar(content: Text(reason)));
        Session.signedOutReason.value = null;
      }
      return;
    }
    setState(() {
      if (!Session.canViewHistory) _tab = 0; // History was switched off
    });
  }

  void _select(int index) {
    if (index == 1) {
      openSurvey(context); // the big middle button
      return;
    }
    if (index == 2) ServerHistory.refresh(); // fresh list each time History opens
    setState(() => _tab = index == 2 ? 1 : 0);
  }

  @override
  Widget build(BuildContext context) {
    final allowed = Session.canViewHistory;
    return Scaffold(
      appBar: AppBar(
        backgroundColor: AppColors.brandDark,
        foregroundColor: Colors.white,
        surfaceTintColor: Colors.transparent,
        systemOverlayStyle: SystemUiOverlayStyle.light,
        toolbarHeight: 56,
        titleSpacing: 16,
        title: Row(
          children: [
            Container(
              width: 32,
              height: 32,
              decoration: BoxDecoration(
                color: AppColors.accent,
                borderRadius: BorderRadius.circular(9),
              ),
              child: Icon(Icons.play_arrow_rounded, color: AppColors.brandDark, size: 20),
            ),
            const SizedBox(width: 12),
            const Flexible(
              child: Text(
                'FMCG Field Survey',
                overflow: TextOverflow.ellipsis,
                style: TextStyle(fontSize: 18, fontWeight: FontWeight.w700, letterSpacing: -0.2),
              ),
            ),
          ],
        ),
        actions: const [
          NotificationBellButton(),
          SizedBox(width: 6),
        ],
      ),
      // IndexedStack keeps each tab's scroll position when switching.
      body: IndexedStack(
        index: allowed ? _tab : 0,
        children: [
          MobileHomePage(
            onOpenHistory: () {
              ServerHistory.refresh();
              setState(() => _tab = 1);
            },
            onOpenSettings: () => Navigator.of(context).push(
              MaterialPageRoute(builder: (_) => const MobileSettingsPage()),
            ),
          ),
          // History only for executives an admin has allowed.
          if (allowed) const MobileHistoryPage() else const SizedBox.shrink(),
        ],
      ),
      bottomNavigationBar: ValueListenableBuilder<List<QueuedSurvey>>(
        valueListenable: SurveyQueue.items,
        builder: (context, items, _) {
          final waiting = items.where((s) => s.needsUpload).length;
          final surveyButton = NavigationDestination(
            icon: Container(
              width: 52,
              height: 36,
              decoration: BoxDecoration(color: AppColors.brand, borderRadius: BorderRadius.circular(18)),
              child: Icon(Icons.add_rounded, color: AppColors.onBrand, size: 24),
            ),
            label: 'Survey',
            tooltip: 'Start a survey',
          );
          return NavigationBar(
            selectedIndex: allowed && _tab == 1 ? 2 : 0,
            onDestinationSelected: _select,
            destinations: [
              NavigationDestination(
                icon: Badge(
                  isLabelVisible: !allowed && waiting > 0,
                  label: Text('$waiting'),
                  child: const Icon(Icons.home_outlined),
                ),
                selectedIcon: Badge(
                  isLabelVisible: !allowed && waiting > 0,
                  label: Text('$waiting'),
                  child: const Icon(Icons.home_rounded),
                ),
                label: 'Home',
              ),
              surveyButton,
              if (allowed)
                NavigationDestination(
                  icon: Badge(
                    isLabelVisible: waiting > 0,
                    label: Text('$waiting'),
                    child: const Icon(Icons.history_rounded),
                  ),
                  label: 'History',
                ),
            ],
          );
        },
      ),
    );
  }
}

/// Confirmation for destructive actions, matching the web app's dialog:
/// icon, clear title and message, "Keep" as the safe choice, red action.
Future<bool> confirmAction(
  BuildContext context, {
  required String title,
  required String message,
  String confirmLabel = 'Delete',
  String cancelLabel = 'Keep',
}) async {
  final result = await showDialog<bool>(
    context: context,
    builder: (ctx) => AlertDialog(
      icon: Container(
        width: 48,
        height: 48,
        decoration: BoxDecoration(
          color: AppColors.negSoft,
          borderRadius: BorderRadius.circular(14),
        ),
        child: Icon(Icons.delete_outline_rounded, color: AppColors.record),
      ),
      title: Text(title, textAlign: TextAlign.center),
      content: Text(
        message,
        textAlign: TextAlign.center,
        style: TextStyle(fontSize: 15, height: 1.45, color: AppColors.ink2),
      ),
      actionsAlignment: MainAxisAlignment.center,
      actions: [
        OutlinedButton(
          onPressed: () => Navigator.pop(ctx, false),
          child: Text(cancelLabel),
        ),
        FilledButton(
          style: FilledButton.styleFrom(backgroundColor: AppColors.record, minimumSize: const Size(0, 40)),
          onPressed: () => Navigator.pop(ctx, true),
          child: Text(confirmLabel),
        ),
      ],
    ),
  );
  return result ?? false;
}

void showComingSoon(BuildContext context, String message) {
  ScaffoldMessenger.of(context)
    ..hideCurrentSnackBar()
    ..showSnackBar(SnackBar(content: Text(message)));
}

/* ============================================================
   Analyze tab
   ============================================================ */

enum RecordKind { video, voice }

class AnalyzePage extends StatefulWidget {
  const AnalyzePage({super.key, required this.onOpenHistory});

  final VoidCallback onOpenHistory;

  @override
  State<AnalyzePage> createState() => _AnalyzePageState();
}

class _AnalyzePageState extends State<AnalyzePage> {
  List<SavedRecording> _saved = [];

  static const _audioExtensions = ['mp3', 'wav', 'm4a', 'ogg', 'aac'];
  static const _allowedExtensions = ['mp4', 'mov', 'avi', 'mkv', 'webm', 'mp3', 'wav', 'm4a', 'ogg', 'aac'];

  @override
  void initState() {
    super.initState();
    _loadSaved();
  }

  Future<void> _loadSaved() async {
    final items = await RecordingStore.list();
    if (mounted) setState(() => _saved = items);
  }

  Future<void> _openRecordSheet(RecordKind kind) async {
    final start = await showModalBottomSheet<bool>(
      context: context,
      useSafeArea: true,
      showDragHandle: true,
      isScrollControlled: true,
      backgroundColor: AppColors.surface,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(24)),
      ),
      builder: (_) => RecordSheet(kind: kind),
    );
    if (start != true || !mounted) return;

    await Navigator.of(context).push(
      MaterialPageRoute(
        builder: (_) => kind == RecordKind.video ? const RecordVideoScreen() : const RecordAudioScreen(),
      ),
    );
    _loadSaved(); // a recording may have been saved for later
  }

  Future<void> _pickFile() async {
    // Newer file_picker versions call pickFiles directly on FilePicker
    // (the old FilePicker.platform was removed) and return a non-null list,
    // so errors are handled with try/catch and a cancelled pick is an empty list.
    final List<PlatformFile> picked;
    try {
      picked = await FilePicker.pickFiles(
        type: FileType.custom,
        allowedExtensions: _allowedExtensions,
      );
    } catch (e) {
      if (mounted) showComingSoon(context, "Couldn't open your files: $e");
      return;
    }
    if (picked.isEmpty || !mounted) return;
    final file = picked.first;
    if (file.path == null) return;

    final ext = file.name.contains('.') ? file.name.split('.').last.toLowerCase() : '';
    await Navigator.of(context).push(
      MaterialPageRoute(
        builder: (_) => AnalyzeScreen(
          path: file.path!,
          name: file.name,
          kind: _audioExtensions.contains(ext) ? 'audio' : 'video',
        ),
      ),
    );
  }

  Future<void> _analyzeSaved(SavedRecording item) async {
    await Navigator.of(context).push(
      MaterialPageRoute(
        builder: (_) => AnalyzeScreen(
          path: item.path,
          name: item.name,
          kind: item.kind,
          durationSeconds: item.durationSeconds,
          savedId: item.id,
        ),
      ),
    );
    _loadSaved();
  }

  Future<void> _deleteSaved(SavedRecording item) async {
    final ok = await confirmAction(
      context,
      title: 'Delete this recording?',
      message: "It's saved only on this phone and hasn't been analyzed, so it can't be recovered.",
    );
    if (!ok) return;
    await RecordingStore.delete(item.id);
    _loadSaved();
  }

  @override
  Widget build(BuildContext context) {
    return RefreshIndicator(
      color: AppColors.brand,
      onRefresh: _loadSaved,
      child: ListView(
        physics: const AlwaysScrollableScrollPhysics(),
        padding: const EdgeInsets.fromLTRB(20, 24, 20, 32),
        children: [
          // Short header: one title, one sentence.
          const Text(
            'Capture a product review',
            style: TextStyle(
              fontSize: 26,
              height: 1.15,
              fontWeight: FontWeight.w800,
              letterSpacing: -0.6,
            ),
          ),
          const SizedBox(height: 8),
          Text(
            'Record or upload what a customer says. We\'ll flag what they '
            'liked and what needs fixing.',
            style: TextStyle(fontSize: 15, height: 1.45, color: AppColors.muted),
          ),
          const SizedBox(height: 24),

          // Main action
          _PrimaryActionCard(onTap: () => _openRecordSheet(RecordKind.video)),
          const SizedBox(height: 12),

          // Secondary actions
          Row(
            children: [
              Expanded(
                child: _ActionTile(
                  icon: Icons.mic_none_rounded,
                  title: 'Voice only',
                  subtitle: 'Small file, good on weak networks',
                  onTap: () => _openRecordSheet(RecordKind.voice),
                ),
              ),
              const SizedBox(width: 12),
              Expanded(
                child: _ActionTile(
                  icon: Icons.upload_file_rounded,
                  title: 'Upload file',
                  subtitle: 'Video or audio from your phone',
                  onTap: _pickFile,
                ),
              ),
            ],
          ),
          const SizedBox(height: 14),

          // The rules in one quiet line instead of a paragraph
          const Wrap(
            spacing: 8,
            runSpacing: 8,
            children: [
              _InfoChip(icon: Icons.schedule_rounded, label: '30 sec – 35 min'),
              _InfoChip(icon: Icons.wifi_off_rounded, label: 'Works offline'),
              _InfoChip(icon: Icons.history_rounded, label: 'Saved to history'),
            ],
          ),

          const SizedBox(height: 32),
          _SectionHeader(title: 'Saved on this phone', count: _saved.length),
          const SizedBox(height: 12),
          if (_saved.isEmpty)
            const _EmptyCard(
              icon: Icons.inventory_2_outlined,
              title: 'Nothing saved yet',
              message: 'Recordings you save while offline wait here until '
                  'you upload them.',
            )
          else
            for (final item in _saved)
              _SavedTile(
                item: item,
                onPlay: () => showRecordingPlayer(
                  context,
                  filePath: item.path,
                  isVideo: item.isVideo,
                  title: item.isVideo ? 'Video recording' : 'Voice recording',
                  durationHint: item.durationSeconds,
                ),
                onAnalyze: () => _analyzeSaved(item),
                onDelete: () => _deleteSaved(item),
              ),

          const SizedBox(height: 32),
          const _SectionHeader(title: 'What you get'),
          const SizedBox(height: 12),
          const _BenefitsCard(),
        ],
      ),
    );
  }
}

class _SavedTile extends StatelessWidget {
  const _SavedTile({
    required this.item,
    required this.onPlay,
    required this.onAnalyze,
    required this.onDelete,
  });

  final SavedRecording item;
  final VoidCallback onPlay;
  final VoidCallback onAnalyze;
  final VoidCallback onDelete;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 10),
      child: Semantics(
        label: '${item.isVideo ? 'Video' : 'Voice'} recording, ${formatDuration(item.durationSeconds)}. Tap to play.',
        child: Material(
        color: AppColors.surface,
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(18),
          side: BorderSide(color: AppColors.line),
        ),
        clipBehavior: Clip.antiAlias,
        child: InkWell(
          onTap: onPlay,
          child: Padding(
      padding: const EdgeInsets.fromLTRB(14, 12, 6, 12),
      child: Row(
        children: [
          Stack(
            clipBehavior: Clip.none,
            children: [
              Container(
                width: 44,
                height: 44,
                decoration: BoxDecoration(
                  color: item.isVideo ? AppColors.videoSoft : AppColors.brandSoft,
                  borderRadius: BorderRadius.circular(13),
                ),
                child: Icon(
                  item.isVideo ? Icons.videocam_rounded : Icons.mic_rounded,
                  color: item.isVideo ? AppColors.video : AppColors.brand,
                  size: 20,
                ),
              ),
              Positioned(
                right: -4,
                bottom: -4,
                child: Container(
                  width: 22,
                  height: 22,
                  decoration: BoxDecoration(
                    color: AppColors.brand,
                    shape: BoxShape.circle,
                    border: Border.all(color: AppColors.surface, width: 2),
                  ),
                  child: Icon(Icons.play_arrow_rounded, size: 16, color: AppColors.onBrand),
                ),
              ),
            ],
          ),
          const SizedBox(width: 12),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  item.isVideo ? 'Video recording' : 'Voice recording',
                  style: const TextStyle(fontSize: 15, fontWeight: FontWeight.w700),
                ),
                const SizedBox(height: 2),
                Text(
                  '${timeAgo(item.createdAt)}  ·  ${formatDuration(item.durationSeconds)}  ·  ${fileSizeLabel(item.sizeBytes)}',
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: TextStyle(fontSize: 13, color: AppColors.muted),
                ),
              ],
            ),
          ),
          const SizedBox(width: 8),
          FilledButton(
            onPressed: onAnalyze,
            style: FilledButton.styleFrom(
              minimumSize: const Size(0, 40),
              padding: const EdgeInsets.symmetric(horizontal: 14),
              textStyle: const TextStyle(fontSize: 14, fontWeight: FontWeight.w600),
            ),
            child: const Text('Analyze'),
          ),
          IconButton(
            onPressed: onDelete,
            tooltip: 'Delete',
            icon: Icon(Icons.delete_outline_rounded, color: AppColors.muted),
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

class _PrimaryActionCard extends StatelessWidget {
  const _PrimaryActionCard({required this.onTap});

  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return Material(
      color: AppColors.brand,
      borderRadius: BorderRadius.circular(20),
      clipBehavior: Clip.antiAlias,
      child: InkWell(
        onTap: onTap,
        child: Ink(
          decoration: const BoxDecoration(
            gradient: LinearGradient(
              begin: Alignment.topLeft,
              end: Alignment.bottomRight,
              colors: [AppColors.navy, AppColors.navy900],
            ),
          ),
          padding: const EdgeInsets.all(20),
          child: Row(
            children: [
              Container(
                width: 56,
                height: 56,
                decoration: BoxDecoration(
                  color: Colors.white.withValues(alpha: 0.14),
                  borderRadius: BorderRadius.circular(16),
                ),
                child: const Icon(
                  Icons.videocam_rounded,
                  color: Colors.white,
                  size: 24,
                ),
              ),
              const SizedBox(width: 16),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      'Record a video',
                      style: TextStyle(
                        color: Colors.white,
                        fontSize: 18,
                        fontWeight: FontWeight.w700,
                      ),
                    ),
                    SizedBox(height: 4),
                    Text(
                      'Show the product while you talk',
                      style: TextStyle(
                        color: AppColors.mist,
                        fontSize: 14,
                        height: 1.35,
                      ),
                    ),
                  ],
                ),
              ),
              Container(
                width: 36,
                height: 36,
                decoration: BoxDecoration(
                  color: AppColors.record,
                  shape: BoxShape.circle,
                ),
                child: const Icon(
                  Icons.fiber_manual_record_rounded,
                  color: Colors.white,
                  size: 16,
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

class _ActionTile extends StatelessWidget {
  const _ActionTile({
    required this.icon,
    required this.title,
    required this.subtitle,
    required this.onTap,
  });

  final IconData icon;
  final String title;
  final String subtitle;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return Material(
      color: AppColors.surface,
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(18),
        side: BorderSide(color: AppColors.line),
      ),
      clipBehavior: Clip.antiAlias,
      child: InkWell(
        onTap: onTap,
        child: Padding(
          padding: const EdgeInsets.all(16),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Container(
                width: 44,
                height: 44,
                decoration: BoxDecoration(
                  color: AppColors.brandSoft,
                  borderRadius: BorderRadius.circular(13),
                ),
                child: Icon(icon, color: AppColors.brand, size: 24),
              ),
              const SizedBox(height: 14),
              Text(
                title,
                style: const TextStyle(
                  fontSize: 16,
                  fontWeight: FontWeight.w700,
                ),
              ),
              const SizedBox(height: 4),
              Text(
                subtitle,
                maxLines: 2,
                overflow: TextOverflow.ellipsis,
                style: TextStyle(
                  fontSize: 13,
                  height: 1.35,
                  color: AppColors.muted,
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

class _InfoChip extends StatelessWidget {
  const _InfoChip({required this.icon, required this.label});

  final IconData icon;
  final String label;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
      decoration: BoxDecoration(
        color: AppColors.neutralSoft,
        borderRadius: BorderRadius.circular(999),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(icon, size: 16, color: AppColors.muted),
          const SizedBox(width: 6),
          Text(
            label,
            style: TextStyle(
              fontSize: 13,
              fontWeight: FontWeight.w500,
              color: AppColors.ink2,
            ),
          ),
        ],
      ),
    );
  }
}

class _SectionHeader extends StatelessWidget {
  const _SectionHeader({required this.title, this.count});

  final String title;
  final int? count;

  @override
  Widget build(BuildContext context) {
    return Row(
      children: [
        Text(
          title,
          style: const TextStyle(fontSize: 18, fontWeight: FontWeight.w700),
        ),
        if (count != null) ...[
          const SizedBox(width: 8),
          Container(
            padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 2),
            decoration: BoxDecoration(
              color: AppColors.neutralSoft,
              borderRadius: BorderRadius.circular(999),
            ),
            child: Text(
              '$count',
              style: TextStyle(
                fontSize: 13,
                fontWeight: FontWeight.w600,
                color: AppColors.muted,
              ),
            ),
          ),
        ],
      ],
    );
  }
}

class _EmptyCard extends StatelessWidget {
  const _EmptyCard({
    required this.icon,
    required this.title,
    required this.message,
  });

  final IconData icon;
  final String title;
  final String message;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: AppColors.surface,
        borderRadius: BorderRadius.circular(18),
        border: Border.all(color: AppColors.line),
      ),
      child: Row(
        children: [
          Container(
            width: 44,
            height: 44,
            decoration: BoxDecoration(
              color: AppColors.bg,
              borderRadius: BorderRadius.circular(13),
            ),
            child: Icon(icon, color: AppColors.muted, size: 20),
          ),
          const SizedBox(width: 14),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  title,
                  style: const TextStyle(
                    fontSize: 15,
                    fontWeight: FontWeight.w700,
                  ),
                ),
                const SizedBox(height: 3),
                Text(
                  message,
                  style: TextStyle(
                    fontSize: 13.5,
                    height: 1.4,
                    color: AppColors.muted,
                  ),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}

class _BenefitsCard extends StatelessWidget {
  const _BenefitsCard();

  @override
  Widget build(BuildContext context) {
    const items = [
      (
        Icons.subject_rounded,
        'Timestamped transcript',
        'Every line, with the moment it was said',
      ),
      (
        Icons.thumbs_up_down_outlined,
        'Praise and issues flagged',
        'See what customers liked and what to fix',
      ),
      (
        Icons.star_outline_rounded,
        'Ratings and next steps',
        'Scores for taste, packaging, price and more',
      ),
    ];

    return Container(
      decoration: BoxDecoration(
        color: AppColors.surface,
        borderRadius: BorderRadius.circular(18),
        border: Border.all(color: AppColors.line),
      ),
      child: Column(
        children: [
          for (var i = 0; i < items.length; i++) ...[
            if (i > 0)
              Divider(height: 1, indent: 66, color: AppColors.line),
            Padding(
              padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 14),
              child: Row(
                children: [
                  Icon(items[i].$1, color: AppColors.brand, size: 24),
                  const SizedBox(width: 26),
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(
                          items[i].$2,
                          style: const TextStyle(
                            fontSize: 15,
                            fontWeight: FontWeight.w600,
                          ),
                        ),
                        const SizedBox(height: 2),
                        Text(
                          items[i].$3,
                          style: TextStyle(
                            fontSize: 13.5,
                            color: AppColors.muted,
                          ),
                        ),
                      ],
                    ),
                  ),
                ],
              ),
            ),
          ],
        ],
      ),
    );
  }
}

/* ============================================================
   Bottom sheet shown before recording starts
   ============================================================ */

class RecordSheet extends StatelessWidget {
  const RecordSheet({super.key, required this.kind});

  final RecordKind kind;

  @override
  Widget build(BuildContext context) {
    final isVideo = kind == RecordKind.video;

    final tips = [
      'Find a quiet spot so your voice is clear',
      if (isVideo) 'Keep the product in view of the camera',
      'Talk about taste, packaging, price and what you\'d change',
      'Record up to 35 minutes. You can pause at any time',
    ];

    return Padding(
      padding: const EdgeInsets.fromLTRB(20, 0, 20, 20),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Container(
                width: 52,
                height: 52,
                decoration: BoxDecoration(
                  color: isVideo ? AppColors.videoSoft : AppColors.brandSoft,
                  borderRadius: BorderRadius.circular(15),
                ),
                child: Icon(
                  isVideo ? Icons.videocam_rounded : Icons.mic_rounded,
                  color: isVideo ? AppColors.video : AppColors.brand,
                  size: 24,
                ),
              ),
              const SizedBox(width: 14),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      isVideo ? 'Record a video' : 'Record your voice',
                      style: const TextStyle(
                        fontSize: 20,
                        fontWeight: FontWeight.w700,
                      ),
                    ),
                    const SizedBox(height: 2),
                    Text(
                      isVideo
                          ? 'Uses the camera and microphone'
                          : 'Uses the microphone only',
                      style: TextStyle(
                        fontSize: 14,
                        color: AppColors.muted,
                      ),
                    ),
                  ],
                ),
              ),
            ],
          ),
          const SizedBox(height: 20),
          Text(
            'Before you start',
            style: TextStyle(
              fontSize: 13,
              fontWeight: FontWeight.w700,
              letterSpacing: 0.3,
              color: AppColors.muted,
            ),
          ),
          const SizedBox(height: 10),
          for (final tip in tips)
            Padding(
              padding: const EdgeInsets.only(bottom: 10),
              child: Row(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Padding(
                    padding: EdgeInsets.only(top: 1),
                    child: Icon(
                      Icons.check_circle_rounded,
                      size: 20,
                      color: AppColors.accent,
                    ),
                  ),
                  const SizedBox(width: 10),
                  Expanded(
                    child: Text(
                      tip,
                      style: TextStyle(
                        fontSize: 15,
                        height: 1.4,
                        color: AppColors.ink2,
                      ),
                    ),
                  ),
                ],
              ),
            ),
          const SizedBox(height: 10),
          SizedBox(
            width: double.infinity,
            child: FilledButton.icon(
              onPressed: () {
                Navigator.of(context).pop(true);
              },
              style: FilledButton.styleFrom(
                backgroundColor: AppColors.record,
              ),
              icon: const Icon(Icons.fiber_manual_record_rounded, size: 16),
              label: const Text('Start recording'),
            ),
          ),
          const SizedBox(height: 8),
          Center(
            child: Text(
              'Your phone will ask for permission the first time.',
              style: TextStyle(
                fontSize: 13,
                color: AppColors.muted.withValues(alpha: 0.9),
              ),
            ),
          ),
        ],
      ),
    );
  }
}

/* ============================================================
   History tab
   ============================================================ */

Color toneColor(ReviewTone tone) {
  switch (tone) {
    case ReviewTone.positive:
      return AppColors.pos;
    case ReviewTone.negative:
      return AppColors.neg;
    case ReviewTone.mixed:
      return AppColors.warn;
    case ReviewTone.neutral:
      return AppColors.muted;
  }
}

Color toneSoft(ReviewTone tone) {
  switch (tone) {
    case ReviewTone.positive:
      return AppColors.posSoft;
    case ReviewTone.negative:
      return AppColors.negSoft;
    case ReviewTone.mixed:
      return AppColors.warnSoft;
    case ReviewTone.neutral:
      return AppColors.neutralSoft;
  }
}

ReviewTone toneForRating(double v) =>
    v >= 4 ? ReviewTone.positive : (v >= 3 ? ReviewTone.mixed : ReviewTone.negative);

String formatDuration(double seconds) {
  final s = seconds.round();
  if (s < 60) return '$s sec';
  final h = s ~/ 3600;
  final m = (s % 3600) ~/ 60;
  final rest = (s % 60).toString().padLeft(2, '0');
  return h > 0 ? '$h hr $m min' : '$m min $rest sec';
}

String formatClock(double seconds) {
  final s = seconds.round();
  final m = (s ~/ 60).toString().padLeft(2, '0');
  final sec = (s % 60).toString().padLeft(2, '0');
  return '$m:$sec';
}

String timeAgo(DateTime? date) {
  if (date == null) return '';
  final diff = DateTime.now().difference(date);
  if (diff.inMinutes < 1) return 'Just now';
  if (diff.inMinutes < 60) return '${diff.inMinutes} min ago';
  if (diff.inHours < 24) return diff.inHours == 1 ? '1 hour ago' : '${diff.inHours} hours ago';
  if (diff.inDays == 1) return 'Yesterday';
  if (diff.inDays < 7) return '${diff.inDays} days ago';
  return formatDate(date);
}

String formatDate(DateTime? d) {
  if (d == null) return '';
  const months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  final hour = d.hour % 12 == 0 ? 12 : d.hour % 12;
  final minute = d.minute.toString().padLeft(2, '0');
  return '${d.day} ${months[d.month - 1]} ${d.year}, $hour:$minute ${d.hour < 12 ? 'am' : 'pm'}';
}

class HistoryPage extends StatefulWidget {
  const HistoryPage({super.key, required this.onAnalyze});

  final VoidCallback onAnalyze;

  @override
  State<HistoryPage> createState() => _HistoryPageState();
}

class _HistoryPageState extends State<HistoryPage> {
  final ApiService _api = ApiService();
  late Future<List<FeedbackRecord>> _future;
  List<FeedbackRecord> _records = const []; // last loaded list, shown while refreshing
  DateTime? _lastUpdated;
  bool _refreshing = false;

  @override
  void initState() {
    super.initState();
    _future = _fetch();
    historyRefresh.addListener(_refresh);
  }

  @override
  void dispose() {
    historyRefresh.removeListener(_refresh);
    super.dispose();
  }

  Future<List<FeedbackRecord>> _fetch() async {
    final records = await _api.getFeedbackHistory();
    records.sort((a, b) {
      final da = a.uploadedAt?.millisecondsSinceEpoch ?? 0;
      final db = b.uploadedAt?.millisecondsSinceEpoch ?? 0;
      return db.compareTo(da); // newest first
    });
    _records = records;
    _lastUpdated = DateTime.now();
    return records;
  }

  Future<void> _refresh() async {
    if (_refreshing || !mounted) return;
    final future = _fetch();
    setState(() {
      _refreshing = true;
      _future = future;
    });
    try {
      await future;
    } catch (_) {
      // The error is shown by the FutureBuilder.
    } finally {
      if (mounted) setState(() => _refreshing = false);
    }
  }

  void _open(FeedbackRecord record) {
    Navigator.of(context).push(
      MaterialPageRoute(
        builder: (_) => FeedbackDetailPage(id: record.id, preview: record),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    return RefreshIndicator(
      color: AppColors.brand,
      onRefresh: _refresh,
      child: FutureBuilder<List<FeedbackRecord>>(
        future: _future,
        builder: (context, snapshot) {
          final loading = snapshot.connectionState != ConnectionState.done;
          final records = snapshot.data ?? _records;
          final refreshFailed = snapshot.hasError && records.isNotEmpty;

          return ListView(
            physics: const AlwaysScrollableScrollPhysics(),
            padding: const EdgeInsets.fromLTRB(20, 24, 20, 32),
            children: [
              Row(
                children: [
                  const Expanded(
                    child: Text(
                      'Feedback history',
                      style: TextStyle(
                        fontSize: 26,
                        height: 1.15,
                        fontWeight: FontWeight.w800,
                        letterSpacing: -0.6,
                      ),
                    ),
                  ),
                  _RefreshButton(spinning: _refreshing || loading, onTap: _refresh),
                ],
              ),
              const SizedBox(height: 8),
              Text(
                records.isNotEmpty
                    ? '${records.length} ${records.length == 1 ? 'review' : 'reviews'} analyzed. Tap one to see the full analysis.'
                    : 'Every review analyzed so far, with its summary, ratings and transcript.',
                style: TextStyle(fontSize: 15, height: 1.45, color: AppColors.muted),
              ),
              if (_lastUpdated != null && !loading) ...[
                const SizedBox(height: 4),
                Text(
                  'Updated ${timeAgo(_lastUpdated).toLowerCase()}',
                  style: TextStyle(fontSize: 12.5, color: AppColors.muted),
                ),
              ],
              const SizedBox(height: 20),
              if (refreshFailed) ...[
                Container(
                  padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
                  decoration: BoxDecoration(
                    color: AppColors.negSoft,
                    borderRadius: BorderRadius.circular(12),
                  ),
                  child: Row(
                    children: [
                      Icon(Icons.cloud_off_rounded, size: 16, color: AppColors.neg),
                      const SizedBox(width: 10),
                      Expanded(
                        child: Text(
                          "Couldn't refresh. Showing the last loaded list.",
                          style: TextStyle(fontSize: 13.5, color: AppColors.negDeep),
                        ),
                      ),
                    ],
                  ),
                ),
                const SizedBox(height: 12),
              ],
              if (loading && records.isEmpty)
                ...List.generate(3, (_) => const _SkeletonCard())
              else if (snapshot.hasError && records.isEmpty)
                _ErrorCard(message: '${snapshot.error}', onRetry: _refresh)
              else if (records.isEmpty)
                _HistoryEmpty(onAnalyze: widget.onAnalyze, onRefresh: _refresh)
              else ...[
                _HistoryStats(records: records),
                const SizedBox(height: 16),
                for (final r in records) _HistoryCard(record: r, onTap: () => _open(r)),
              ],
            ],
          );
        },
      ),
    );
  }
}

class _RefreshButton extends StatelessWidget {
  const _RefreshButton({required this.spinning, required this.onTap});

  final bool spinning;
  final Future<void> Function() onTap;

  @override
  Widget build(BuildContext context) {
    return Tooltip(
      message: 'Refresh',
      child: Material(
        color: AppColors.surface,
        shape: CircleBorder(side: BorderSide(color: AppColors.line)),
        child: InkWell(
          customBorder: const CircleBorder(),
          onTap: spinning ? null : onTap,
          child: SizedBox(
            width: 48,
            height: 48,
            child: Center(
              child: spinning
                  ? SizedBox(
                      width: 20,
                      height: 20,
                      child: CircularProgressIndicator(strokeWidth: 2.4, color: AppColors.brand),
                    )
                  : Icon(Icons.refresh_rounded, color: AppColors.brand),
            ),
          ),
        ),
      ),
    );
  }
}

class _HistoryStats extends StatelessWidget {
  const _HistoryStats({required this.records});

  final List<FeedbackRecord> records;

  @override
  Widget build(BuildContext context) {
    final rated = records.map((r) => r.overallRating).whereType<double>().where((v) => v > 0).toList();
    final avg = rated.isEmpty ? null : rated.reduce((a, b) => a + b) / rated.length;
    final totalMinutes = records.fold<double>(0, (sum, r) => sum + r.durationSeconds) / 60;

    Widget stat(String label, String value, {Widget? trailing}) {
      return Expanded(
        child: Padding(
          padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 14),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(label, style: TextStyle(fontSize: 12.5, color: AppColors.muted)),
              const SizedBox(height: 4),
              Row(
                children: [
                  Flexible(
                    child: Text(
                      value,
                      overflow: TextOverflow.ellipsis,
                      style: const TextStyle(fontSize: 20, fontWeight: FontWeight.w800),
                    ),
                  ),
                  if (trailing != null) ...[const SizedBox(width: 4), trailing],
                ],
              ),
            ],
          ),
        ),
      );
    }

    return Container(
      decoration: BoxDecoration(
        color: AppColors.surface,
        borderRadius: BorderRadius.circular(18),
        border: Border.all(color: AppColors.line),
      ),
      child: IntrinsicHeight(
        child: Row(
          children: [
            stat('Reviews', '${records.length}'),
            VerticalDivider(width: 1, color: AppColors.line),
            stat(
              'Avg rating',
              avg == null ? '—' : avg.toStringAsFixed(1),
              trailing: avg == null
                  ? null
                  : const Icon(Icons.star_rounded, size: 20, color: AppColors.gold),
            ),
            VerticalDivider(width: 1, color: AppColors.line),
            stat('Minutes', totalMinutes < 1 ? '<1' : totalMinutes.round().toString()),
          ],
        ),
      ),
    );
  }
}

class _HistoryCard extends StatelessWidget {
  const _HistoryCard({required this.record, required this.onTap});

  final FeedbackRecord record;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final tone = record.tone;
    final overview = record.overview;

    return Padding(
      padding: const EdgeInsets.only(bottom: 12),
      child: Material(
        color: AppColors.surface,
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(18),
          side: BorderSide(color: AppColors.line),
        ),
        clipBehavior: Clip.antiAlias,
        child: InkWell(
          onTap: onTap,
          child: Padding(
            padding: const EdgeInsets.all(16),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  children: [
                    Container(
                      width: 44,
                      height: 44,
                      decoration: BoxDecoration(
                        color: toneSoft(tone.tone),
                        borderRadius: BorderRadius.circular(13),
                      ),
                      child: Icon(
                        record.isAudio ? Icons.mic_rounded : Icons.videocam_rounded,
                        color: toneColor(tone.tone),
                        size: 20,
                      ),
                    ),
                    const SizedBox(width: 12),
                    Expanded(
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Text(
                            record.filename,
                            maxLines: 1,
                            overflow: TextOverflow.ellipsis,
                            style: const TextStyle(fontSize: 15, fontWeight: FontWeight.w700),
                          ),
                          const SizedBox(height: 3),
                          Text(
                            [
                              if (record.uploadedAt != null) timeAgo(record.uploadedAt),
                              if (record.durationSeconds > 0) formatDuration(record.durationSeconds),
                            ].join('  ·  '),
                            style: TextStyle(fontSize: 13, color: AppColors.muted),
                          ),
                        ],
                      ),
                    ),
                    Icon(Icons.chevron_right_rounded, color: AppColors.muted),
                  ],
                ),
                if (overview.isNotEmpty) ...[
                  const SizedBox(height: 12),
                  Text(
                    overview,
                    maxLines: 3,
                    overflow: TextOverflow.ellipsis,
                    style: TextStyle(fontSize: 14, height: 1.45, color: AppColors.ink2),
                  ),
                ],
                const SizedBox(height: 12),
                Row(
                  children: [
                    TonePill(tone: tone),
                    const Spacer(),
                    if (record.overallRating != null) ...[
                      RatingStars(rating: record.overallRating!, size: 16),
                      const SizedBox(width: 6),
                      Text(
                        record.overallRating!.toStringAsFixed(1),
                        style: const TextStyle(fontSize: 14, fontWeight: FontWeight.w700),
                      ),
                    ],
                  ],
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }
}

class TonePill extends StatelessWidget {
  const TonePill({super.key, required this.tone});

  final ToneInfo tone;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 5),
      decoration: BoxDecoration(
        color: toneSoft(tone.tone),
        borderRadius: BorderRadius.circular(999),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Container(
            width: 7,
            height: 7,
            decoration: BoxDecoration(color: toneColor(tone.tone), shape: BoxShape.circle),
          ),
          const SizedBox(width: 6),
          Text(
            tone.label,
            style: TextStyle(fontSize: 13, fontWeight: FontWeight.w600, color: toneColor(tone.tone)),
          ),
        ],
      ),
    );
  }
}

class RatingStars extends StatelessWidget {
  const RatingStars({super.key, required this.rating, this.size = 18});

  final double rating;
  final double size;

  static const _gold = AppColors.gold;
  static Color get _grey => AppColors.starEmpty;

  @override
  Widget build(BuildContext context) {
    // Round like the number shown next to the stars (4.47 -> 4.5), so they agree.
    final v = (rating.clamp(0, 5) * 10).round() / 10;
    return Semantics(
      label: '${v.toStringAsFixed(1)} out of 5 stars',
      excludeSemantics: true,
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          // Each star is filled by its share: 4.4 -> four full stars + 40% of the fifth.
          for (var i = 0; i < 5; i++) _star((v - i).clamp(0.0, 1.0).toDouble()),
        ],
      ),
    );
  }

  Widget _star(double fill) {
    return SizedBox(
      width: size,
      height: size,
      child: Stack(
        children: [
          Icon(Icons.star_rounded, size: size, color: _grey),
          if (fill > 0)
            ClipRect(
              child: Align(
                alignment: Alignment.centerLeft,
                widthFactor: fill,
                child: Icon(Icons.star_rounded, size: size, color: _gold),
              ),
            ),
        ],
      ),
    );
  }
}

class _SkeletonCard extends StatelessWidget {
  const _SkeletonCard();

  @override
  Widget build(BuildContext context) {
    Widget bar(double w, double h) => Container(
          width: w,
          height: h,
          decoration: BoxDecoration(
            color: AppColors.neutralSoft,
            borderRadius: BorderRadius.circular(6),
          ),
        );

    return Container(
      margin: const EdgeInsets.only(bottom: 12),
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: AppColors.surface,
        borderRadius: BorderRadius.circular(18),
        border: Border.all(color: AppColors.line),
      ),
      child: Row(
        children: [
          bar(44, 44),
          const SizedBox(width: 12),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [bar(180, 14), const SizedBox(height: 8), bar(110, 12)],
            ),
          ),
        ],
      ),
    );
  }
}

class _ErrorCard extends StatelessWidget {
  const _ErrorCard({required this.message, required this.onRetry});

  final String message;
  final Future<void> Function() onRetry;

  @override
  Widget build(BuildContext context) {
    return Container(
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
              Text(
                "Couldn't load feedback history",
                style: TextStyle(fontSize: 16, fontWeight: FontWeight.w700, color: AppColors.neg),
              ),
            ],
          ),
          const SizedBox(height: 8),
          Text(message, style: TextStyle(fontSize: 14, height: 1.45, color: AppColors.negDeep)),
          const SizedBox(height: 14),
          FilledButton.icon(
            onPressed: onRetry,
            icon: const Icon(Icons.refresh_rounded),
            label: const Text('Try again'),
          ),
        ],
      ),
    );
  }
}

class _HistoryEmpty extends StatelessWidget {
  const _HistoryEmpty({required this.onAnalyze, required this.onRefresh});

  final VoidCallback onAnalyze;
  final Future<void> Function() onRefresh;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.fromLTRB(24, 32, 24, 24),
      decoration: BoxDecoration(
        color: AppColors.surface,
        borderRadius: BorderRadius.circular(20),
        border: Border.all(color: AppColors.line),
      ),
      child: Column(
        children: [
          Container(
            width: 64,
            height: 64,
            decoration: BoxDecoration(color: AppColors.brandSoft, shape: BoxShape.circle),
            child: Icon(Icons.history_rounded, size: 24, color: AppColors.brand),
          ),
          const SizedBox(height: 16),
          const Text('No feedback yet', style: TextStyle(fontSize: 18, fontWeight: FontWeight.w700)),
          const SizedBox(height: 6),
          Text(
            'Analyze your first customer review and it will show up here.',
            textAlign: TextAlign.center,
            style: TextStyle(fontSize: 14.5, height: 1.45, color: AppColors.muted),
          ),
          const SizedBox(height: 20),
          SizedBox(
            width: double.infinity,
            child: FilledButton.icon(
              onPressed: onAnalyze,
              icon: const Icon(Icons.add_rounded),
              label: const Text('Analyze a review'),
            ),
          ),
          const SizedBox(height: 4),
          TextButton.icon(
            onPressed: onRefresh,
            icon: const Icon(Icons.refresh_rounded, size: 16),
            label: const Text('Refresh'),
            style: TextButton.styleFrom(foregroundColor: AppColors.muted, minimumSize: const Size(0, 40)),
          ),
        ],
      ),
    );
  }
}

/* ============================================================
   Feedback detail (GET /feedback/{id})
   ============================================================ */

class FeedbackDetailPage extends StatefulWidget {
  const FeedbackDetailPage({super.key, required this.id, this.preview});

  final int id;
  final FeedbackRecord? preview; // list data, shown while the full record loads

  @override
  State<FeedbackDetailPage> createState() => _FeedbackDetailPageState();
}

class _FeedbackDetailPageState extends State<FeedbackDetailPage> {
  final ApiService _api = ApiService();
  late Future<FeedbackRecord> _future;

  @override
  void initState() {
    super.initState();
    _future = widget.id > 0 || widget.preview == null
        ? _api.getFeedbackById(widget.id)
        : Future.value(widget.preview!);
  }

  void _retry() => setState(() => _future = _api.getFeedbackById(widget.id));

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        backgroundColor: AppColors.bg,
        surfaceTintColor: Colors.transparent,
        systemOverlayStyle: SystemUiOverlayStyle.dark,
        title: Text(
          'Feedback #${widget.id}',
          style: const TextStyle(fontSize: 17, fontWeight: FontWeight.w700),
        ),
      ),
      body: FutureBuilder<FeedbackRecord>(
        future: _future,
        builder: (context, snapshot) {
          if (snapshot.hasError) {
            return ListView(
              padding: const EdgeInsets.all(20),
              children: [_ErrorCard(message: '${snapshot.error}', onRetry: () async => _retry())],
            );
          }
          final record = snapshot.data ?? widget.preview;
          if (record == null) return const Center(child: CircularProgressIndicator());
          final loadingFull = snapshot.connectionState != ConnectionState.done;
          return _DetailBody(record: record, loadingFull: loadingFull);
        },
      ),
    );
  }
}

class _DetailBody extends StatelessWidget {
  const _DetailBody({required this.record, required this.loadingFull});

  final FeedbackRecord record;
  final bool loadingFull;

  @override
  Widget build(BuildContext context) {
    final lines = parseSummaryLines(record.summary);
    final insights = buildInsights(record);
    final rating = record.overallRating;

    return ListView(
      padding: const EdgeInsets.fromLTRB(20, 4, 20, 40),
      children: [
        // Title block
        Text(
          record.filename,
          style: const TextStyle(fontSize: 20, height: 1.25, fontWeight: FontWeight.w800),
        ),
        const SizedBox(height: 6),
        Wrap(
          spacing: 12,
          runSpacing: 4,
          children: [
            if (record.uploadedAt != null) _Meta(Icons.event_rounded, formatDate(record.uploadedAt)),
            if (record.durationSeconds > 0) _Meta(Icons.schedule_rounded, formatDuration(record.durationSeconds)),
            if (record.chapters.isNotEmpty)
              _Meta(Icons.view_agenda_outlined, '${record.chapters.length} ${record.chapters.length == 1 ? 'part' : 'parts'}'),
          ],
        ),
        const SizedBox(height: 12),
        Align(alignment: Alignment.centerLeft, child: TonePill(tone: record.tone)),
        if (record.id > 0) ...[
          const SizedBox(height: 16),
          MediaPlayerCard(
            key: ValueKey('media-${record.id}'),
            url: ApiService().mediaUrl(record.id),
            isVideo: !record.isAudio,
            durationHint: record.durationSeconds,
          ),
        ],
        if (loadingFull) ...[
          const SizedBox(height: 16),
          LinearProgressIndicator(minHeight: 3, color: AppColors.brand, backgroundColor: AppColors.brandSoft),
        ],

        // Overall rating
        if (rating != null) ...[
          const SizedBox(height: 20),
          _Card(
            child: Row(
              children: [
                Container(
                  width: 72,
                  height: 72,
                  decoration: BoxDecoration(
                    color: toneSoft(toneForRating(rating)),
                    borderRadius: BorderRadius.circular(18),
                  ),
                  child: Column(
                    mainAxisAlignment: MainAxisAlignment.center,
                    children: [
                      Text(
                        rating.toStringAsFixed(1),
                        style: TextStyle(
                          fontSize: 28,
                          height: 1,
                          fontWeight: FontWeight.w800,
                          color: toneColor(toneForRating(rating)),
                        ),
                      ),
                      const SizedBox(height: 3),
                      Text(
                        'out of 5',
                        style: TextStyle(fontSize: 11.5, color: toneColor(toneForRating(rating))),
                      ),
                    ],
                  ),
                ),
                const SizedBox(width: 16),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      const Text('Overall product rating', style: TextStyle(fontSize: 16, fontWeight: FontWeight.w700)),
                      const SizedBox(height: 6),
                      RatingStars(rating: rating, size: 24),
                      if (record.aspectRatings.isNotEmpty) ...[
                        const SizedBox(height: 4),
                        Text(
                          'Across ${record.aspectRatings.length} feedback areas',
                          style: TextStyle(fontSize: 13, color: AppColors.muted),
                        ),
                      ],
                    ],
                  ),
                ),
              ],
            ),
          ),
        ],

        // Summary: overview, sentiment bar, then liked / issues / actions
        if (lines.isNotEmpty || insights.hasLists) ...[
          const _DetailHeading('AI summary'),
          _Card(
            child: insights.overview.isEmpty && !insights.hasLists
                // Nothing recognisable: show the summary text as it came.
                ? Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [for (final l in lines) _summaryLine(l)],
                  )
                : _InsightsView(insights: insights),
          ),
        ],

        // Ratings by area
        if (record.aspectRatings.isNotEmpty) ...[
          const _DetailHeading('Ratings by feedback area'),
          _Card(
            padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 4),
            child: Column(
              children: [
                for (var i = 0; i < record.aspectRatings.length; i++) ...[
                  if (i > 0) Divider(height: 1, color: AppColors.line),
                  _AspectRow(item: record.aspectRatings[i]),
                ],
              ],
            ),
          ),
        ],

        // Parts
        if (record.chapters.isNotEmpty) ...[
          const _DetailHeading('Parts analyzed'),
          for (final p in record.chapters) _PartTile(part: p, videoLength: record.durationSeconds),
        ],

        // Transcript
        if (record.transcript.isNotEmpty) ...[
          const _DetailHeading('Transcript'),
          _Card(
            padding: EdgeInsets.zero,
            child: Theme(
              data: Theme.of(context).copyWith(dividerColor: Colors.transparent),
              child: ExpansionTile(
                tilePadding: const EdgeInsets.symmetric(horizontal: 16),
                childrenPadding: const EdgeInsets.fromLTRB(16, 0, 16, 16),
                title: const Text('Show full transcript', style: TextStyle(fontSize: 15, fontWeight: FontWeight.w600)),
                children: [
                  SelectableText(
                    record.transcript,
                    style: TextStyle(fontSize: 15, height: 1.6, color: AppColors.ink2),
                  ),
                ],
              ),
            ),
          ),
        ],
      ],
    );
  }
}

Widget _summaryLine(SummaryLine l) {
  switch (l.kind) {
    case SummaryLineKind.heading:
      return Padding(
        padding: const EdgeInsets.only(top: 12, bottom: 6),
        child: Text(
          l.text,
          style: TextStyle(fontSize: 14, fontWeight: FontWeight.w700, color: AppColors.brand),
        ),
      );
    case SummaryLineKind.bullet:
      return Padding(
        padding: const EdgeInsets.only(bottom: 6),
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Padding(
              padding: EdgeInsets.only(top: 8, right: 10),
              child: CircleAvatar(radius: 3, backgroundColor: AppColors.brand),
            ),
            Expanded(
              child: Text(l.text, style: TextStyle(fontSize: 15, height: 1.45, color: AppColors.ink2)),
            ),
          ],
        ),
      );
    case SummaryLineKind.text:
      return Padding(
        padding: const EdgeInsets.only(bottom: 6),
        child: Text(l.text, style: const TextStyle(fontSize: 15.5, height: 1.5)),
      );
  }
}

/// Rounded box with a coloured strip on the left. Flutter doesn't allow a
/// one-sided border on a rounded box, so the strip is a separate child.
class _AccentBox extends StatelessWidget {
  const _AccentBox({
    required this.accent,
    required this.child,
    this.background,
    this.outline,
  });

  final Color accent;
  final Color? background;
  final Color? outline;
  final Widget child;

  @override
  Widget build(BuildContext context) {
    return Container(
      width: double.infinity,
      clipBehavior: Clip.antiAlias,
      decoration: BoxDecoration(
        color: background ?? AppColors.surface,
        borderRadius: BorderRadius.circular(12),
        border: outline == null ? null : Border.all(color: outline!),
      ),
      child: IntrinsicHeight(
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Container(width: 3, color: accent),
            Expanded(
              child: Padding(padding: const EdgeInsets.all(12), child: child),
            ),
          ],
        ),
      ),
    );
  }
}

/* ---------- AI summary insights ---------- */

class _InsightsView extends StatelessWidget {
  const _InsightsView({required this.insights});

  final ReviewInsights insights;

  @override
  Widget build(BuildContext context) {
    final i = insights;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        for (final p in i.overview)
          Padding(
            padding: const EdgeInsets.only(bottom: 8),
            child: Text(p, style: const TextStyle(fontSize: 15.5, height: 1.5)),
          ),
        if (i.counts.total > 0) ...[
          const SizedBox(height: 6),
          _ToneBar(counts: i.counts),
        ],
        _InsightGroup(
          title: 'What customers liked',
          items: i.positives,
          color: toneColor(ReviewTone.positive),
          soft: toneSoft(ReviewTone.positive),
          icon: Icons.check_rounded,
        ),
        _InsightGroup(
          title: 'Issues raised',
          items: i.issues,
          color: toneColor(ReviewTone.negative),
          soft: toneSoft(ReviewTone.negative),
          icon: Icons.priority_high_rounded,
        ),
        _InsightGroup(
          title: 'Recommended actions',
          items: i.actions,
          color: toneColor(ReviewTone.mixed),
          soft: toneSoft(ReviewTone.mixed),
          icon: Icons.arrow_forward_rounded,
        ),
        _InsightGroup(
          title: 'Other notes',
          items: i.notes,
          color: AppColors.muted,
          soft: toneSoft(ReviewTone.neutral),
          icon: Icons.notes_rounded,
        ),
        for (final sec in i.other)
          _InsightGroup(
            title: sec.heading ?? 'Notes',
            items: [...sec.paragraphs, ...sec.items],
            color: AppColors.muted,
            soft: toneSoft(ReviewTone.neutral),
            icon: Icons.notes_rounded,
          ),
        const SizedBox(height: 14),
        Divider(height: 1, color: AppColors.line),
        const SizedBox(height: 10),
        Text(
          i.derived
              ? 'Liked and issue points are taken from the customer feedback found in each part.'
              : 'Liked and issue points come from the AI summary.',
          style: TextStyle(fontSize: 12.5, height: 1.4, color: AppColors.muted),
        ),
      ],
    );
  }
}

class _InsightGroup extends StatelessWidget {
  const _InsightGroup({
    required this.title,
    required this.items,
    required this.color,
    required this.soft,
    required this.icon,
  });

  final String title;
  final List<String> items;
  final Color color;
  final Color soft;
  final IconData icon;

  @override
  Widget build(BuildContext context) {
    if (items.isEmpty) return const SizedBox.shrink();
    return Padding(
      padding: const EdgeInsets.only(top: 18),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Text(title, style: const TextStyle(fontSize: 15, fontWeight: FontWeight.w700)),
              const SizedBox(width: 8),
              Container(
                padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 1),
                decoration: BoxDecoration(color: soft, borderRadius: BorderRadius.circular(999)),
                child: Text(
                  '${items.length}',
                  style: TextStyle(fontSize: 12.5, fontWeight: FontWeight.w700, color: color),
                ),
              ),
            ],
          ),
          const SizedBox(height: 10),
          for (final item in items)
            Container(
              width: double.infinity,
              margin: const EdgeInsets.only(bottom: 8),
              padding: const EdgeInsets.all(12),
              decoration: BoxDecoration(color: soft, borderRadius: BorderRadius.circular(12)),
              child: Row(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Container(
                    width: 24,
                    height: 24,
                    decoration: BoxDecoration(color: color, shape: BoxShape.circle),
                    child: Icon(icon, size: 16, color: Colors.white),
                  ),
                  const SizedBox(width: 10),
                  Expanded(
                    child: Text(item, style: TextStyle(fontSize: 14.5, height: 1.45, color: AppColors.ink2)),
                  ),
                ],
              ),
            ),
        ],
      ),
    );
  }
}

class _ToneBar extends StatelessWidget {
  const _ToneBar({required this.counts});

  final ToneCounts counts;

  @override
  Widget build(BuildContext context) {
    Widget seg(int n, Color c) => n == 0
        ? const SizedBox.shrink()
        : Expanded(flex: n, child: Container(height: 10, color: c));

    Widget legend(Color c, String text) => Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            Container(width: 8, height: 8, decoration: BoxDecoration(color: c, shape: BoxShape.circle)),
            const SizedBox(width: 6),
            Text(text, style: TextStyle(fontSize: 13, color: AppColors.muted)),
          ],
        );

    return Container(
      padding: const EdgeInsets.all(14),
      decoration: BoxDecoration(color: AppColors.bg, borderRadius: BorderRadius.circular(14)),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          ClipRRect(
            borderRadius: BorderRadius.circular(999),
            child: Row(
              children: [
                seg(counts.positive, toneColor(ReviewTone.positive)),
                seg(counts.neutral, AppColors.neutralBar),
                seg(counts.negative, toneColor(ReviewTone.negative)),
              ],
            ),
          ),
          const SizedBox(height: 10),
          Wrap(
            spacing: 14,
            runSpacing: 4,
            children: [
              legend(toneColor(ReviewTone.positive), '${counts.positive} praise'),
              legend(AppColors.neutralBar, '${counts.neutral} neutral'),
              legend(toneColor(ReviewTone.negative), '${counts.negative} ${counts.negative == 1 ? 'issue' : 'issues'}'),
            ],
          ),
        ],
      ),
    );
  }
}

class _Meta extends StatelessWidget {
  const _Meta(this.icon, this.text);

  final IconData icon;
  final String text;

  @override
  Widget build(BuildContext context) {
    return Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        Icon(icon, size: 16, color: AppColors.muted),
        const SizedBox(width: 5),
        Text(text, style: TextStyle(fontSize: 13.5, color: AppColors.muted)),
      ],
    );
  }
}

class _DetailHeading extends StatelessWidget {
  const _DetailHeading(this.text);

  final String text;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(top: 28, bottom: 10),
      child: Text(text, style: const TextStyle(fontSize: 18, fontWeight: FontWeight.w700)),
    );
  }
}

class _Card extends StatelessWidget {
  const _Card({required this.child, this.padding = const EdgeInsets.all(16)});

  final Widget child;
  final EdgeInsetsGeometry padding;

  @override
  Widget build(BuildContext context) {
    return Container(
      width: double.infinity,
      padding: padding,
      decoration: BoxDecoration(
        color: AppColors.surface,
        borderRadius: BorderRadius.circular(18),
        border: Border.all(color: AppColors.line),
      ),
      child: child,
    );
  }
}

class _AspectRow extends StatelessWidget {
  const _AspectRow({required this.item});

  final AspectRating item;

  @override
  Widget build(BuildContext context) {
    final r = item.rating;
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 12),
      child: Row(
        children: [
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(item.name, style: const TextStyle(fontSize: 15, fontWeight: FontWeight.w600)),
                Text(item.category, style: TextStyle(fontSize: 13, color: AppColors.muted)),
              ],
            ),
          ),
          if (r != null) ...[
            RatingStars(rating: r, size: 16),
            const SizedBox(width: 8),
            SizedBox(
              width: 30,
              child: Text(
                r.toStringAsFixed(1),
                textAlign: TextAlign.right,
                style: TextStyle(fontSize: 14, fontWeight: FontWeight.w700, color: toneColor(toneForRating(r))),
              ),
            ),
          ],
        ],
      ),
    );
  }
}

class _PartTile extends StatelessWidget {
  const _PartTile({required this.part, required this.videoLength});

  final FeedbackPart part;
  final double videoLength;

  @override
  Widget build(BuildContext context) {
    final end = videoLength > 0 && part.end > videoLength ? videoLength : part.end;
    final tone = toneFromLabel(part.sentiment);

    return Padding(
      padding: const EdgeInsets.only(bottom: 10),
      child: _Card(
        padding: EdgeInsets.zero,
        child: Theme(
          data: Theme.of(context).copyWith(dividerColor: Colors.transparent),
          child: ExpansionTile(
            tilePadding: const EdgeInsets.symmetric(horizontal: 16, vertical: 4),
            childrenPadding: const EdgeInsets.fromLTRB(16, 0, 16, 16),
            expandedCrossAxisAlignment: CrossAxisAlignment.start,
            leading: Container(
              width: 34,
              height: 34,
              alignment: Alignment.center,
              decoration: BoxDecoration(color: toneSoft(tone.tone), borderRadius: BorderRadius.circular(10)),
              child: Text(
                '${part.part}',
                style: TextStyle(fontWeight: FontWeight.w800, color: toneColor(tone.tone)),
              ),
            ),
            title: Text('Part ${part.part}', style: const TextStyle(fontSize: 15, fontWeight: FontWeight.w700)),
            subtitle: Text(
              '${formatClock(part.start)} – ${formatClock(end)}',
              style: TextStyle(fontSize: 13, color: AppColors.muted),
            ),
            children: [
              if (part.summary.isNotEmpty)
                Text(part.summary, style: TextStyle(fontSize: 15, height: 1.5, color: AppColors.ink2)),
              if (part.keyPoint.isNotEmpty) ...[
                const SizedBox(height: 12),
                _AccentBox(
                  accent: AppColors.brand,
                  background: AppColors.brandSoft,
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        'KEY TAKEAWAY',
                        style: TextStyle(fontSize: 11, fontWeight: FontWeight.w800, letterSpacing: 0.6, color: AppColors.brand),
                      ),
                      const SizedBox(height: 4),
                      Text(part.keyPoint, style: const TextStyle(fontSize: 14, height: 1.45)),
                    ],
                  ),
                ),
              ],
              if (part.feedback.isNotEmpty) ...[
                const SizedBox(height: 14),
                const Text('Customer feedback', style: TextStyle(fontSize: 14, fontWeight: FontWeight.w700)),
                const SizedBox(height: 8),
                for (final f in part.feedback) _FeedbackItem(item: f),
              ],
            ],
          ),
        ),
      ),
    );
  }
}

class _FeedbackItem extends StatelessWidget {
  const _FeedbackItem({required this.item});

  final AspectFeedback item;

  @override
  Widget build(BuildContext context) {
    final tone = toneFromLabel(item.sentiment);
    return Padding(
      padding: const EdgeInsets.only(bottom: 8),
      child: _AccentBox(
        accent: toneColor(tone.tone),
        outline: AppColors.line,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Expanded(
                child: Text(item.name, style: const TextStyle(fontSize: 14.5, fontWeight: FontWeight.w700)),
              ),
              if (item.rating != null) RatingStars(rating: item.rating!, size: 16),
            ],
          ),
          const SizedBox(height: 4),
          Text(
            [
              if (item.category.isNotEmpty) item.category,
              if (item.sentiment.isNotEmpty) item.sentiment,
              if (item.severity.isNotEmpty) item.severity,
              if (item.mentions > 1) '${item.mentions} mentions',
            ].join('  ·  '),
            style: TextStyle(fontSize: 12.5, color: AppColors.muted),
          ),
          if (item.evidence.isNotEmpty) ...[
            const SizedBox(height: 6),
            Text(
              '“${item.evidence}”',
              style: TextStyle(fontSize: 14, height: 1.45, fontStyle: FontStyle.italic, color: AppColors.ink2),
            ),
          ],
        ],
      ),
      ),
    );
  }
}