import 'package:flutter/material.dart';

import '../main.dart';
import '../services/api_service.dart';
import '../services/session.dart';
import '../services/survey_queue.dart';
import 'onboarding_screens.dart';
import 'report_screens.dart';
import 'survey_flow.dart';

/* ================================================================
   Shared bits
   ================================================================ */

Future<void> openSurvey(BuildContext context, {RecentProduct? product}) {
  return Navigator.of(context).push(MaterialPageRoute(builder: (_) => SurveyFlowScreen(startProduct: product)));
}

bool _isToday(DateTime d) {
  final n = DateTime.now();
  return d.year == n.year && d.month == n.month && d.day == n.day;
}

String _dayLabel(DateTime d) {
  final today = DateTime.now();
  final day = DateTime(d.year, d.month, d.day);
  final diff = DateTime(today.year, today.month, today.day).difference(day).inDays;
  if (diff == 0) return 'Today';
  if (diff == 1) return 'Yesterday';
  const months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  return '${d.day} ${months[d.month - 1]} ${d.year}';
}

String _clock(DateTime d) {
  final h = d.hour % 12 == 0 ? 12 : d.hour % 12;
  return '$h:${d.minute.toString().padLeft(2, '0')} ${d.hour < 12 ? 'am' : 'pm'}';
}

class StatusChip extends StatelessWidget {
  const StatusChip({super.key, required this.entry});

  final HistoryEntry entry;

  @override
  Widget build(BuildContext context) {
    String label;
    Color fg;
    Color bg;
    if (entry.noRecording) {
      label = 'No recording';
      fg = AppColors.muted;
      bg = AppColors.neutralSoft;
    } else {
      switch (entry.status) {
        case SurveyStatus.waiting:
          label = 'Waiting to upload';
          fg = AppColors.warn;
          bg = AppColors.warnSoft;
        case SurveyStatus.uploading:
          label = 'Uploading';
          fg = AppColors.info;
          bg = AppColors.infoSoft;
        case SurveyStatus.analyzing:
          label = 'Analyzing';
          fg = AppColors.info;
          bg = AppColors.infoSoft;
        case SurveyStatus.analyzed:
          final tone = toneFromLabel(entry.sentiment ?? '');
          if (!Session.canViewHistory || (entry.sentiment ?? '').isEmpty) {
            // No hint of the feedback without the history permission.
            label = 'Analyzed';
            fg = AppColors.pos;
            bg = AppColors.posSoft;
          } else {
            label = tone.label;
            fg = toneColor(tone.tone);
            bg = toneSoft(tone.tone);
          }
        case SurveyStatus.failed:
          label = 'Failed';
          fg = AppColors.neg;
          bg = AppColors.negSoft;
      }
    }
    final busy = entry.status == SurveyStatus.uploading || entry.status == SurveyStatus.analyzing;
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
      decoration: BoxDecoration(color: bg, borderRadius: BorderRadius.circular(999)),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          if (busy) ...[
            SizedBox(width: 10, height: 10, child: CircularProgressIndicator(strokeWidth: 2, color: fg)),
            const SizedBox(width: 6),
          ],
          Text(label, style: TextStyle(fontSize: 12.5, fontWeight: FontWeight.w700, color: fg)),
        ],
      ),
    );
  }
}

/* ================================================================
   Home
   ================================================================ */

class MobileHomePage extends StatelessWidget {
  const MobileHomePage({super.key, required this.onOpenHistory, required this.onOpenSettings});

  final VoidCallback onOpenHistory;
  final VoidCallback onOpenSettings;

  @override
  Widget build(BuildContext context) {
    return ListenableBuilder(
      listenable: Listenable.merge([SurveyQueue.items, ServerHistory.items, Session.stats, Session.user]),
      builder: (context, _) {
        final items = SurveyQueue.items.value;
        final history = buildHistory(items, ServerHistory.items.value);
        // Everything on the server, plus this phone's surveys not uploaded yet.
        // My own numbers from the server, plus my surveys still waiting on this phone.
        final notOnServer = items.where((s) => s.responseId == null).length;
        final mine = Session.stats.value;
        final total = (mine?.total ?? 0) + notOnServer;
        final todayCount = (mine?.today ?? 0) + items.where((s) => s.responseId == null && _isToday(s.createdAt)).length;
        final allowed = Session.canViewHistory;
        // Without the history permission: this phone's surveys from today, status only.
        final todayHere = items.where((s) => _isToday(s.createdAt)).toList();
        final waiting = items.where((s) => s.needsUpload).toList();
        final active = items.where((s) => s.status == SurveyStatus.uploading || s.status == SurveyStatus.analyzing).toList();
        final failed = items.where((s) => s.status == SurveyStatus.failed).length;

        return RefreshIndicator(
          color: AppColors.brand,
          onRefresh: () async {
            await Future.wait([
              Session.refreshMe(),
              Session.refreshStats(),
              ServerHistory.refresh(),
              SurveyQueue.reconcileWithServer(),
              AppProfile.refreshRecentProducts(),
            ]);
            SurveyQueue.process(includeFailed: true); // runs on; may follow analyses for minutes
          },
          child: ListView(
            physics: const AlwaysScrollableScrollPhysics(),
            padding: const EdgeInsets.fromLTRB(16, 16, 16, 32),
            children: [
              ValueListenableBuilder<String>(
                valueListenable: AppProfile.executiveName,
                builder: (context, name, _) {
                  final first = name.trim().split(' ').first;
                  return Row(
                    children: [
                      Expanded(
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            Text(
                              first.isEmpty ? 'Hello' : 'Hi $first',
                              style: const TextStyle(fontSize: 26, fontWeight: FontWeight.w800, letterSpacing: -0.5),
                            ),
                            Text(_todayText(), style: TextStyle(fontSize: 14, color: AppColors.muted)),
                          ],
                        ),
                      ),
                      IconButton(
                        onPressed: onOpenSettings,
                        tooltip: 'Settings',
                        icon: Icon(Icons.settings_outlined, color: AppColors.ink2),
                      ),
                    ],
                  );
                },
              ),
              const SizedBox(height: 16),

              // Today's numbers
              Container(
                decoration: BoxDecoration(
                  color: AppColors.surface,
                  borderRadius: BorderRadius.circular(18),
                  border: Border.all(color: AppColors.line),
                ),
                child: IntrinsicHeight(
                  child: Row(
                    children: [
                      _Stat(label: 'My surveys', value: mine == null && notOnServer == 0 ? '…' : '$total'),
                      VerticalDivider(width: 1, color: AppColors.line),
                      _Stat(label: 'Today', value: mine == null && notOnServer == 0 ? '…' : '$todayCount'),
                      VerticalDivider(width: 1, color: AppColors.line),
                      _Stat(label: 'To upload', value: '${waiting.length}', warn: waiting.isNotEmpty),
                    ],
                  ),
                ),
              ),
              const SizedBox(height: 14),

              // Main action
              Material(
                color: AppColors.hero,
                borderRadius: BorderRadius.circular(20),
                clipBehavior: Clip.antiAlias,
                child: InkWell(
                  onTap: () => openSurvey(context),
                  child: const Padding(
                    padding: EdgeInsets.all(20),
                    child: Row(
                      children: [
                        CircleAvatar(
                          radius: 28,
                          backgroundColor: Color(0x24FFFFFF),
                          child: Icon(Icons.add_rounded, color: Colors.white, size: 32),
                        ),
                        SizedBox(width: 16),
                        Expanded(
                          child: Column(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                              Text('Start a survey',
                                  style: TextStyle(color: Colors.white, fontSize: 20, fontWeight: FontWeight.w800)),
                              SizedBox(height: 2),
                              Text('Product, customer, questions, recording',
                                  style: TextStyle(color: AppColors.mist, fontSize: 14)),
                            ],
                          ),
                        ),
                        Icon(Icons.chevron_right_rounded, color: Colors.white),
                      ],
                    ),
                  ),
                ),
              ),

              // Uploads waiting
              if (waiting.isNotEmpty) ...[
                const SizedBox(height: 14),
                _Banner(
                  color: AppColors.warnSoft,
                  border: AppColors.warnLine,
                  icon: Icons.cloud_upload_outlined,
                  iconColor: AppColors.warn,
                  title: '${waiting.length} ${waiting.length == 1 ? 'survey' : 'surveys'} waiting to upload',
                  text: failed > 0
                      ? '$failed failed. Check History for the reason.'
                      : 'They upload automatically when the connection is back.',
                  action: TextButton(
                    onPressed: () => SurveyQueue.process(includeFailed: true),
                    child: const Text('Upload now'),
                  ),
                ),
              ],

              // Uploading / analyzing now
              if (active.isNotEmpty) ...[
                const SizedBox(height: 14),
                ValueListenableBuilder<Map<String, double>>(
                  valueListenable: SurveyQueue.progress,
                  builder: (context, progress, _) => Column(
                    children: [
                      for (final s in active)
                        _Banner(
                    color: AppColors.infoSoft,
                    border: AppColors.infoLine,
                    icon: s.status == SurveyStatus.analyzing ? Icons.auto_awesome_outlined : Icons.cloud_upload_outlined,
                    iconColor: AppColors.info,
                    title: '${s.status == SurveyStatus.analyzing ? 'Analyzing' : 'Uploading'} · ${s.productName}',
                    text: s.status == SurveyStatus.analyzing
                        ? '${s.respondentLabel}: this takes a few minutes. You can keep surveying.'
                        : s.respondentLabel,
                    progress: s.status == SurveyStatus.uploading ? progress[s.id] : null,
                  ),
                    ],
                  ),
                ),
              ],

              // Recent products
              ValueListenableBuilder<List<RecentProduct>>(
                valueListenable: AppProfile.recentProducts,
                builder: (context, recent, _) {
                  if (recent.isEmpty) return const SizedBox.shrink();
                  return Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      const SizedBox(height: 24),
                      const Text('Survey again', style: TextStyle(fontSize: 17, fontWeight: FontWeight.w800)),
                      const SizedBox(height: 10),
                      for (final p in recent)
                        Padding(
                          padding: const EdgeInsets.only(bottom: 8),
                          child: Material(
                            color: AppColors.surface,
                            shape: RoundedRectangleBorder(
                              borderRadius: BorderRadius.circular(14),
                              side: BorderSide(color: AppColors.line),
                            ),
                            child: ListTile(
                              shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(14)),
                              leading: Icon(Icons.shopping_bag_outlined, color: AppColors.brand),
                              title: Text(p.name, style: const TextStyle(fontWeight: FontWeight.w700)),
                              subtitle: Text(p.categoryName),
                              trailing: Icon(Icons.add_circle_outline_rounded, color: AppColors.brand),
                              onTap: () => openSurvey(context, product: p),
                            ),
                          ),
                        ),
                    ],
                  );
                },
              ),

              // Latest results
              if (allowed && history.isNotEmpty) ...[
                const SizedBox(height: 18),
                Row(
                  children: [
                    const Expanded(child: Text('Latest surveys', style: TextStyle(fontSize: 17, fontWeight: FontWeight.w800))),
                    TextButton(onPressed: onOpenHistory, child: const Text('View all')),
                  ],
                ),
                for (final e in history.take(3)) SurveyTile(entry: e),
              ] else if (!allowed && todayHere.isNotEmpty) ...[
                const SizedBox(height: 18),
                const Text('Today on this phone', style: TextStyle(fontSize: 17, fontWeight: FontWeight.w800)),
                const SizedBox(height: 10),
                for (final e in buildHistory(todayHere, const [])) SurveyTile(entry: e, showDetails: false),
              ],
            ],
          ),
        );
      },
    );
  }

  String _todayText() {
    final d = DateTime.now();
    const days = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'];
    const months = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'];
    return '${days[d.weekday - 1]}, ${d.day} ${months[d.month - 1]}';
  }
}

class _Stat extends StatelessWidget {
  const _Stat({required this.label, required this.value, this.warn = false});

  final String label;
  final String value;
  final bool warn;

  @override
  Widget build(BuildContext context) {
    return Expanded(
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 14),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(value,
                style: TextStyle(
                  fontSize: 24,
                  fontWeight: FontWeight.w800,
                  color: warn ? AppColors.warn : AppColors.ink,
                )),
            Text(label, style: TextStyle(fontSize: 12.5, color: AppColors.muted)),
          ],
        ),
      ),
    );
  }
}

class _Banner extends StatelessWidget {
  const _Banner({
    required this.color,
    required this.border,
    required this.icon,
    required this.iconColor,
    required this.title,
    required this.text,
    this.action,
    this.progress,
  });

  final Color color;
  final Color border;
  final IconData icon;
  final Color iconColor;
  final String title;
  final String text;
  final Widget? action;
  final double? progress;

  @override
  Widget build(BuildContext context) {
    return Container(
      margin: const EdgeInsets.only(bottom: 8),
      padding: const EdgeInsets.fromLTRB(14, 12, 8, 12),
      decoration: BoxDecoration(color: color, borderRadius: BorderRadius.circular(16), border: Border.all(color: border)),
      child: Row(
        children: [
          Icon(icon, color: iconColor),
          const SizedBox(width: 12),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(title, style: const TextStyle(fontSize: 15, fontWeight: FontWeight.w700)),
                const SizedBox(height: 2),
                Text(text, style: TextStyle(fontSize: 13.5, height: 1.35, color: AppColors.ink2)),
                if (progress != null) ...[
                  const SizedBox(height: 8),
                  LinearProgressIndicator(value: progress, minHeight: 4, color: iconColor, backgroundColor: AppColors.surface),
                ],
              ],
            ),
          ),
          if (action != null) action!,
        ],
      ),
    );
  }
}

/* ================================================================
   One survey in a list
   ================================================================ */

class SurveyTile extends StatelessWidget {
  const SurveyTile({super.key, required this.entry, this.showDetails = true});

  final HistoryEntry entry;

  /// False without the history permission: product, customer and status only.
  final bool showDetails;

  @override
  Widget build(BuildContext context) {
    final e = entry;
    final local = e.local;
    final canOpen = showDetails && e.status == SurveyStatus.analyzed && e.feedbackId != null;
    final showError = local != null && (local.status == SurveyStatus.failed || (local.status == SurveyStatus.waiting && local.error != null));
    return Padding(
      padding: const EdgeInsets.only(bottom: 10),
      child: Material(
        color: AppColors.surface,
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(16), side: BorderSide(color: AppColors.line)),
        clipBehavior: Clip.antiAlias,
        child: InkWell(
          onTap: canOpen
              ? () => Navigator.of(context).push(
                    MaterialPageRoute(builder: (_) => FeedbackDetailPage(id: e.feedbackId!)),
                  )
              : null,
          child: Padding(
            padding: const EdgeInsets.fromLTRB(14, 12, 14, 12),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Expanded(
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Text(e.productName,
                              maxLines: 1,
                              overflow: TextOverflow.ellipsis,
                              style: const TextStyle(fontSize: 15.5, fontWeight: FontWeight.w700)),
                          const SizedBox(height: 2),
                          Text(
                            [e.respondentLabel, if (e.area.isNotEmpty) e.area, _clock(e.createdAt)].join(' · '),
                            maxLines: 1,
                            overflow: TextOverflow.ellipsis,
                            style: TextStyle(fontSize: 13, color: AppColors.muted),
                          ),
                        ],
                      ),
                    ),
                    const SizedBox(width: 8),
                    StatusChip(entry: e),
                  ],
                ),
                if (showDetails && e.status == SurveyStatus.analyzed && e.rating != null && e.rating! > 0) ...[
                  const SizedBox(height: 8),
                  Row(
                    children: [
                      RatingStars(rating: e.rating!, size: 16),
                      const SizedBox(width: 6),
                      Text(e.rating!.toStringAsFixed(1), style: const TextStyle(fontWeight: FontWeight.w700)),
                      const Spacer(),
                      if (canOpen) ...[
                        Text('View', style: TextStyle(color: AppColors.brand, fontWeight: FontWeight.w700)),
                        Icon(Icons.chevron_right_rounded, color: AppColors.brand, size: 20),
                      ],
                    ],
                  ),
                ],
                if (showDetails && e.status == SurveyStatus.analyzed && (e.summary ?? '').isNotEmpty) ...[
                  const SizedBox(height: 6),
                  Text(e.summary!,
                      maxLines: 2,
                      overflow: TextOverflow.ellipsis,
                      style: TextStyle(fontSize: 13.5, height: 1.4, color: AppColors.ink2)),
                ],
                if (e.noRecording) ...[
                  const SizedBox(height: 6),
                  Text('Details and answers were saved, but no recording was analyzed.',
                      style: TextStyle(fontSize: 13, color: AppColors.muted)),
                ],
                if (local != null && showError) ...[
                  const SizedBox(height: 8),
                  Text(local.error ?? 'Upload failed.',
                      style: TextStyle(
                        fontSize: 13,
                        color: local.status == SurveyStatus.failed ? AppColors.neg : AppColors.warnDeep,
                      )),
                  Row(
                    children: [
                      TextButton.icon(
                        onPressed: () => SurveyQueue.retry(local.id),
                        icon: const Icon(Icons.refresh_rounded, size: 18),
                        label: const Text('Try again'),
                      ),
                      if (local.status == SurveyStatus.failed)
                        TextButton(
                          onPressed: () async {
                            final ok = await confirmAction(
                              context,
                              title: 'Delete this survey?',
                              message: "It couldn't be uploaded, so its answers and recording will be lost.",
                            );
                            if (ok) await SurveyQueue.remove(local.id);
                          },
                          style: TextButton.styleFrom(foregroundColor: AppColors.record),
                          child: const Text('Delete'),
                        ),
                    ],
                  ),
                ],
              ],
            ),
          ),
        ),
      ),
    );
  }
}

/* ================================================================
   History: all surveys from the server + this phone's pending ones
   ================================================================ */

/// History. Executives: only their own surveys. Admins: everyone's surveys + product reports.
class MobileHistoryPage extends StatelessWidget {
  const MobileHistoryPage({super.key});

  @override
  Widget build(BuildContext context) {
    if (!Session.isAdmin) return const SurveyHistoryList(ownOnly: true);
    return DefaultTabController(
      length: 2,
      child: Column(
        children: [
          Container(
            color: AppColors.surface,
            child: TabBar(
              labelColor: AppColors.brand,
              unselectedLabelColor: AppColors.muted,
              indicatorColor: AppColors.brand,
              indicatorWeight: 3,
              labelStyle: TextStyle(fontSize: 15, fontWeight: FontWeight.w700),
              tabs: [
                Tab(text: 'Surveys'),
                Tab(text: 'Products'),
              ],
            ),
          ),
          const Expanded(
            child: TabBarView(
              children: [
                SurveyHistoryList(),
                ProductReportsList(),
              ],
            ),
          ),
        ],
      ),
    );
  }
}

class SurveyHistoryList extends StatefulWidget {
  const SurveyHistoryList({super.key, this.ownOnly = false});

  /// Executives: the server already returns only their surveys, so no All/Mine switch.
  final bool ownOnly;

  @override
  State<SurveyHistoryList> createState() => _SurveyHistoryListState();
}

class _SurveyHistoryListState extends State<SurveyHistoryList> {
  String _product = 'All';
  bool _mineOnly = false;

  @override
  void initState() {
    super.initState();
    ServerHistory.refresh();
  }

  Future<void> _refresh() async {
    await Future.wait([ServerHistory.refresh(), SurveyQueue.process(includeFailed: true)]);
  }

  @override
  Widget build(BuildContext context) {
    return ListenableBuilder(
      listenable: Listenable.merge([
        SurveyQueue.items,
        ServerHistory.items,
        ServerHistory.loading,
        ServerHistory.error,
        AppProfile.executiveName,
      ]),
      builder: (context, _) {
        final me = AppProfile.executiveName.value.trim().toLowerCase();
        var all = buildHistory(SurveyQueue.items.value, ServerHistory.items.value);
        if (_mineOnly && !widget.ownOnly) {
          all = all.where((e) => e.local != null || (me.isNotEmpty && e.executiveName.trim().toLowerCase() == me)).toList();
        }
        final products = ['All', ...{for (final e in all) e.productName}];
        if (!products.contains(_product)) _product = 'All';
        final list = all.where((e) => _product == 'All' || e.productName == _product).toList();

        final groups = <String, List<HistoryEntry>>{};
        for (final e in list) {
          groups.putIfAbsent(_dayLabel(e.createdAt), () => []).add(e);
        }
        final loading = ServerHistory.loading.value && ServerHistory.items.value.isEmpty;
        final serverError = ServerHistory.error.value;

        return RefreshIndicator(
          color: AppColors.brand,
          onRefresh: _refresh,
          child: ListView(
            physics: const AlwaysScrollableScrollPhysics(),
            padding: const EdgeInsets.fromLTRB(16, 16, 16, 32),
            children: [
              if (ServerHistory.loading.value)
                Padding(
                  padding: EdgeInsets.only(bottom: 8),
                  child: LinearProgressIndicator(minHeight: 2, color: AppColors.brand, backgroundColor: AppColors.brandSoft),
                ),
              if (widget.ownOnly)
                Padding(
                  padding: const EdgeInsets.only(bottom: 4),
                  child: Text('My surveys',
                      style: TextStyle(fontSize: 22, fontWeight: FontWeight.w800, letterSpacing: -0.3, color: AppColors.ink)),
                )
              else
              SegmentedButton<bool>(
                segments: const [
                  ButtonSegment(value: false, label: Text('All surveys'), icon: Icon(Icons.groups_outlined)),
                  ButtonSegment(value: true, label: Text('Mine'), icon: Icon(Icons.person_outline)),
                ],
                selected: {_mineOnly},
                showSelectedIcon: false,
                onSelectionChanged: (v) => setState(() => _mineOnly = v.first),
              ),
              if (!widget.ownOnly && _mineOnly && me.isEmpty)
                Padding(
                  padding: EdgeInsets.only(top: 8),
                  child: Text('Set your name in Settings to see your surveys from other devices.',
                      style: TextStyle(fontSize: 13, color: AppColors.muted)),
                ),
              if (products.length > 2) ...[
                const SizedBox(height: 10),
                SizedBox(
                  height: 40,
                  child: ListView(
                    scrollDirection: Axis.horizontal,
                    children: [
                      for (final p in products)
                        Padding(
                          padding: const EdgeInsets.only(right: 8),
                          child: ChoiceChip(
                            label: Text(p),
                            selected: _product == p,
                            onSelected: (_) => setState(() => _product = p),
                            selectedColor: AppColors.brandSoft,
                          ),
                        ),
                    ],
                  ),
                ),
              ],
              if (serverError != null) ...[
                const SizedBox(height: 10),
                Container(
                  padding: const EdgeInsets.all(12),
                  decoration: BoxDecoration(color: AppColors.warnSoft, borderRadius: BorderRadius.circular(12)),
                  child: Row(
                    children: [
                      Icon(Icons.cloud_off_rounded, size: 18, color: AppColors.warn),
                      const SizedBox(width: 10),
                      Expanded(
                        child: Text("Can't reach the server. Showing only surveys saved on this phone.",
                            style: TextStyle(fontSize: 13.5, color: AppColors.warnDeep)),
                      ),
                      TextButton(onPressed: ServerHistory.refresh, child: const Text('Retry')),
                    ],
                  ),
                ),
              ],
              const SizedBox(height: 4),
              if (loading)
                Padding(
                  padding: EdgeInsets.only(top: 40),
                  child: Center(child: CircularProgressIndicator(color: AppColors.brand)),
                )
              else if (list.isEmpty)
                Padding(
                  padding: const EdgeInsets.only(top: 40),
                  child: Column(
                    children: [
                      Icon(Icons.history_rounded, size: 44, color: AppColors.muted),
                      const SizedBox(height: 10),
                      Text(_mineOnly || widget.ownOnly ? 'No surveys by you yet' : 'No surveys yet',
                          style: const TextStyle(fontSize: 17, fontWeight: FontWeight.w700)),
                      const SizedBox(height: 4),
                      Text('Submitted surveys appear here with their analysis.',
                          textAlign: TextAlign.center, style: TextStyle(color: AppColors.muted)),
                      const SizedBox(height: 16),
                      FilledButton.icon(
                        onPressed: () => openSurvey(context),
                        icon: const Icon(Icons.add_rounded),
                        label: const Text('Start a survey'),
                      ),
                    ],
                  ),
                ),
              for (final entry in groups.entries) ...[
                Padding(
                  padding: const EdgeInsets.only(top: 14, bottom: 8),
                  child: Text(
                    '${entry.key} · ${entry.value.length}',
                    style: TextStyle(fontSize: 13, fontWeight: FontWeight.w800, color: AppColors.muted, letterSpacing: 0.3),
                  ),
                ),
                for (final e in entry.value) SurveyTile(entry: e),
              ],
            ],
          ),
        );
      },
    );
  }
}

/* ================================================================
   Settings
   ================================================================ */

class MobileSettingsPage extends StatefulWidget {
  const MobileSettingsPage({super.key});

  @override
  State<MobileSettingsPage> createState() => _MobileSettingsPageState();
}

class _MobileSettingsPageState extends State<MobileSettingsPage> {
  int? _bytes;

  @override
  void initState() {
    super.initState();
    SurveyQueue.storageBytes().then((b) {
      if (mounted) setState(() => _bytes = b);
    });
  }

  Future<void> _setTheme(String mode) async {
    AppProfile.themeMode.value = mode; // the app repaints in the new mode
    await AppProfile.save();
  }

  Future<void> _logout() async {
    final waiting = SurveyQueue.pendingCount;
    final ok = await confirmAction(
      context,
      title: 'Log out?',
      message: waiting > 0
          ? '$waiting ${waiting == 1 ? 'survey is' : 'surveys are'} still waiting to upload. They stay on this phone and upload after you log in again.'
          : 'You can log in again any time with your mobile number or email.',
      confirmLabel: 'Log out',
      cancelLabel: 'Stay',
    );
    if (!ok || !mounted) return;
    await Session.logout();
    if (mounted) goReplacingAll(context, const WelcomeScreen());
  }

  Widget _section(String title) => Padding(
        padding: const EdgeInsets.only(top: 18, bottom: 8),
        child: Text(title.toUpperCase(),
            style: TextStyle(fontSize: 12, fontWeight: FontWeight.w800, letterSpacing: 0.8, color: AppColors.brand)),
      );

  @override
  Widget build(BuildContext context) {
    final mb = _bytes == null ? '…' : '${(_bytes! / (1024 * 1024)).toStringAsFixed(1)} MB';
    final user = Session.user.value;
    return Scaffold(
      appBar: AppBar(title: const Text('Settings', style: TextStyle(fontWeight: FontWeight.w700))),
      body: ListView(
        padding: const EdgeInsets.fromLTRB(16, 4, 16, 32),
        children: [
          // Account
          Container(
            padding: const EdgeInsets.all(16),
            decoration: BoxDecoration(
              color: AppColors.surface,
              borderRadius: BorderRadius.circular(18),
              border: Border.all(color: AppColors.line),
            ),
            child: Row(
              children: [
                CircleAvatar(
                  radius: 26,
                  backgroundColor: AppColors.brandSoft,
                  child: Text(
                    (user?.name.trim().isNotEmpty ?? false) ? user!.name.trim()[0].toUpperCase() : '?',
                    style: TextStyle(fontSize: 22, fontWeight: FontWeight.w800, color: AppColors.brand),
                  ),
                ),
                const SizedBox(width: 14),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(user?.name ?? 'Not signed in',
                          style: TextStyle(fontSize: 17, fontWeight: FontWeight.w800, color: AppColors.ink)),
                      Text(user?.contact ?? '', style: TextStyle(fontSize: 13.5, color: AppColors.muted)),
                      Text('Survey executive', style: TextStyle(fontSize: 12.5, color: AppColors.muted)),
                    ],
                  ),
                ),
              ],
            ),
          ),

          _section('Appearance'),
          ValueListenableBuilder<String>(
            valueListenable: AppProfile.themeMode,
            builder: (context, mode, _) => SegmentedButton<String>(
              segments: const [
                ButtonSegment(value: 'system', label: Text('System'), icon: Icon(Icons.brightness_auto_rounded)),
                ButtonSegment(value: 'light', label: Text('Light'), icon: Icon(Icons.light_mode_rounded)),
                ButtonSegment(value: 'dark', label: Text('Dark'), icon: Icon(Icons.dark_mode_rounded)),
              ],
              selected: {mode},
              showSelectedIcon: false,
              onSelectionChanged: (v) => _setTheme(v.first),
            ),
          ),

          _section('Phone'),
          ListTile(
            contentPadding: EdgeInsets.zero,
            leading: Icon(Icons.verified_user_outlined, color: AppColors.muted),
            title: Text('Permissions', style: TextStyle(color: AppColors.ink, fontWeight: FontWeight.w600)),
            subtitle: Text('Microphone, camera, location, notifications', style: TextStyle(color: AppColors.muted)),
            trailing: Icon(Icons.chevron_right_rounded, color: AppColors.muted),
            onTap: () => Navigator.of(context).push(
              MaterialPageRoute(builder: (_) => const PermissionsScreen(fromSettings: true)),
            ),
          ),
          _InfoRow(icon: Icons.dns_outlined, label: 'Server', value: ApiConfig.baseUrl),
          _InfoRow(icon: Icons.sd_storage_outlined, label: 'Recordings waiting on this phone', value: mb),
          const SizedBox(height: 8),
          OutlinedButton.icon(
            onPressed: () async {
              final ok = await confirmAction(
                context,
                title: 'Clear analyzed surveys?',
                message: 'They are removed from this phone only. Everything stays on the server.',
                confirmLabel: 'Clear',
                cancelLabel: 'Keep',
              );
              if (ok) await SurveyQueue.clearAnalyzed();
            },
            icon: const Icon(Icons.cleaning_services_outlined),
            label: const Text('Clear analyzed surveys from this phone'),
          ),

          _section('Account'),
          OutlinedButton.icon(
            onPressed: _logout,
            style: OutlinedButton.styleFrom(foregroundColor: AppColors.neg, side: BorderSide(color: AppColors.negLine)),
            icon: const Icon(Icons.logout_rounded),
            label: const Text('Log out'),
          ),
          const SizedBox(height: 24),
          Text(
            'Personal details and recordings are deleted from this phone as soon as a survey has uploaded.',
            style: TextStyle(fontSize: 13, height: 1.45, color: AppColors.muted),
          ),
          const SizedBox(height: 6),
          Text('$appName · version 1.0', style: TextStyle(fontSize: 12.5, color: AppColors.muted)),
        ],
      ),
    );
  }
}

class _InfoRow extends StatelessWidget {
  const _InfoRow({required this.icon, required this.label, required this.value});

  final IconData icon;
  final String label;
  final String value;

  @override
  Widget build(BuildContext context) {
    return ListTile(
      contentPadding: EdgeInsets.zero,
      leading: Icon(icon, color: AppColors.muted),
      title: Text(label, style: TextStyle(fontSize: 14, color: AppColors.muted)),
      subtitle: Text(value, style: TextStyle(fontSize: 15, fontWeight: FontWeight.w600, color: AppColors.ink)),
    );
  }
}