import 'dart:math';

import 'package:flutter/material.dart';

import '../main.dart';
import '../services/admin_api.dart';
import '../services/notification_service.dart';
import '../services/org_api.dart' show Json, asJson, asJsonList, str, intOf, numOrNull;
import '../services/session.dart';
import 'home_screens.dart' show MobileHistoryPage, MobileSettingsPage;
import 'notifications_screen.dart' show NotificationBellButton;
import 'org_screens.dart' show Loaded, Panel, Pill, StatBox, EmptyNote, agoText, ratingText, showMessage;
import 'report_screens.dart' show ProductReportPage;

/* ============================================================
   Admin (role "admin") in the app
   ============================================================
   The web admin dashboard on the phone: Home (overview), History (every survey
   and product reports), Catalog (read-only: the organization manages it) and
   Team (survey executives). Admins don't take surveys: no Survey tab, no
   recording, no microphone/camera permissions.
*/

class AdminShell extends StatefulWidget {
  const AdminShell({super.key});

  @override
  State<AdminShell> createState() => _AdminShellState();
}

class _AdminShellState extends State<AdminShell> with WidgetsBindingObserver {
  int _tab = 0;
  static const _titles = ['Overview', 'Survey history', 'Catalog', 'Team'];

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    Notifications.start(); // bell count, checked every 30 s while the app is open
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    Notifications.stop();
    super.dispose();
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state == AppLifecycleState.resumed) {
      Notifications.start();
    } else if (state == AppLifecycleState.paused) {
      Notifications.stop();
    }
  }

  @override
  Widget build(BuildContext context) {
    final name = Session.user.value?.name ?? 'Admin';
    return Scaffold(
      backgroundColor: AppColors.bg,
      appBar: AppBar(
        titleSpacing: 16,
        title: Row(children: [
          Container(
            width: 30,
            height: 30,
            decoration: BoxDecoration(color: AppColors.brand, borderRadius: BorderRadius.circular(9)),
            child: Icon(Icons.play_arrow_rounded, color: AppColors.onBrand, size: 20),
          ),
          const SizedBox(width: 10),
          Expanded(child: Text(_titles[_tab], overflow: TextOverflow.ellipsis)),
        ]),
        actions: [
          const NotificationBellButton(),
          IconButton(
            tooltip: 'Settings',
            onPressed: () => Navigator.of(context).push(MaterialPageRoute(builder: (_) => const MobileSettingsPage())),
            icon: CircleAvatar(
              radius: 15,
              backgroundColor: AppColors.brandSoft,
              child: Text(name.isEmpty ? 'A' : name[0].toUpperCase(),
                  style: TextStyle(color: AppColors.brand, fontWeight: FontWeight.w800, fontSize: 13)),
            ),
          ),
          const SizedBox(width: 6),
        ],
      ),
      body: IndexedStack(index: _tab, children: [
        AdminHomePage(onViewAll: () => setState(() => _tab = 1)),
        const MobileHistoryPage(), // admins: Surveys and Products tabs, reports
        const AdminCatalogPage(),
        const AdminTeamPage(),
      ]),
      bottomNavigationBar: NavigationBar(
        selectedIndex: _tab,
        onDestinationSelected: (i) => setState(() => _tab = i),
        destinations: const [
          NavigationDestination(icon: Icon(Icons.home_outlined), selectedIcon: Icon(Icons.home), label: 'Home'),
          NavigationDestination(icon: Icon(Icons.history), selectedIcon: Icon(Icons.history), label: 'History'),
          NavigationDestination(icon: Icon(Icons.category_outlined), selectedIcon: Icon(Icons.category), label: 'Catalog'),
          NavigationDestination(icon: Icon(Icons.groups_outlined), selectedIcon: Icon(Icons.groups), label: 'Team'),
        ],
      ),
    );
  }
}

Widget _sectionTitle(String text, {Widget? trailing}) => Padding(
      padding: const EdgeInsets.fromLTRB(2, 18, 2, 8),
      child: Row(children: [
        Expanded(child: Text(text, style: TextStyle(fontWeight: FontWeight.w800, fontSize: 16, color: AppColors.ink))),
        if (trailing != null) trailing,
      ]),
    );

Widget _stars(dynamic rating) {
  final r = (numOrNull(rating) ?? 0).toDouble();
  return Row(mainAxisSize: MainAxisSize.min, children: [
    RatingStars(rating: r, size: 13),
    const SizedBox(width: 4),
    Text(ratingText(rating), style: TextStyle(fontWeight: FontWeight.w800, color: AppColors.ink)),
    Text(' / 5', style: TextStyle(color: AppColors.muted, fontSize: 12)),
  ]);
}

void _openReport(BuildContext context, int id, String name) {
  Navigator.of(context).push(MaterialPageRoute(builder: (_) => ProductReportPage(productId: id, productName: name)));
}

// ---------------------------------------------------------------- Home (web: Overview)

class _HomeData {
  _HomeData(this.feedback, this.surveys, this.categories, this.products);
  final Json feedback;
  final Json surveys;
  final List<Json> categories;
  final List<Json> products;
}

class AdminHomePage extends StatelessWidget {
  const AdminHomePage({super.key, required this.onViewAll});

  final VoidCallback onViewAll;

  Future<_HomeData> _load() async {
    final r = await Future.wait([AdminApi.productFeedback(), AdminApi.recentSurveys(), AdminApi.categories(), AdminApi.products()]);
    return _HomeData(r[0] as Json, r[1] as Json, r[2] as List<Json>, r[3] as List<Json>);
  }

  @override
  Widget build(BuildContext context) {
    return Loaded<_HomeData>(
      load: _load,
      builder: (context, d, reload) {
        final feedback = asJsonList(d.feedback['products']);
        final surveyed = feedback.where((p) => intOf(p['responses']) > 0).length;
        final analyzed = intOf(d.surveys['analyzed_total']);
        final recent = asJsonList(d.surveys['surveys']);
        final rows = [...feedback]..sort((a, b) => intOf(b['responses']).compareTo(intOf(a['responses'])));
        return ListView(
          padding: const EdgeInsets.fromLTRB(16, 8, 16, 24),
          children: [
            Text("Every product's field feedback and its consolidated report.", style: TextStyle(color: AppColors.muted)),
            const SizedBox(height: 12),
            Row(children: [
              Expanded(child: StatBox(value: '${d.products.length}', label: 'Products · in ${d.categories.length} categories')),
              const SizedBox(width: 8),
              Expanded(child: StatBox(value: '$analyzed', label: 'Feedback analyzed · surveys')),
            ]),
            const SizedBox(height: 8),
            StatBox(
              value: '$surveyed of ${d.products.length}',
              label: surveyed >= d.products.length && d.products.isNotEmpty ? 'Products surveyed · every product has surveys' : 'Products surveyed',
            ),
            _sectionTitle('Products'),
            if (rows.isEmpty) const EmptyNote(icon: Icons.inventory_2_outlined, title: 'No products yet', text: 'The Super Admin adds products in their portal.'),
            for (final p in rows) ...[
              _HomeProductRow(
                feedback: p,
                catalog: d.products.where((c) => intOf(c['id']) == intOf(p['id'])).firstOrNull ?? const <String, dynamic>{},
              ),
              const SizedBox(height: 8),
            ],
            _sectionTitle('Recent feedback', trailing: TextButton(onPressed: onViewAll, child: Text('View all $analyzed'))),
            if (recent.isEmpty) const EmptyNote(icon: Icons.mic_none, title: 'No feedback yet', text: 'Analyzed surveys appear here.'),
            for (final s in recent) ...[
              Panel(
                onTap: asJson(s['feedback']).isEmpty
                    ? null
                    : () => Navigator.of(context).push(
                          MaterialPageRoute(builder: (_) => FeedbackDetailPage(id: intOf(asJson(s['feedback'])['id']))),
                        ),
                child: Row(children: [
                  Expanded(
                    child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                      Text('${str(s['respondent_label'], 'Customer')} · ${str(s['product_name'])}',
                          style: TextStyle(fontWeight: FontWeight.w700, color: AppColors.ink)),
                      const SizedBox(height: 2),
                      Text(agoText(s['created_at']), style: TextStyle(fontSize: 12, color: AppColors.muted)),
                    ]),
                  ),
                  if (numOrNull(asJson(s['feedback'])['overall_rating']) != null)
                    Text('${ratingText(asJson(s['feedback'])['overall_rating'])} ★', style: TextStyle(fontWeight: FontWeight.w800, color: AppColors.ink))
                  else
                    Pill(str(s['status']), tone: 'muted'),
                ]),
              ),
              const SizedBox(height: 8),
            ],
          ],
        );
      },
    );
  }
}

/// One product on Home, like the web: responses, rating and status; a version picker
/// ("All 2 versions", "Version 1", "Version 2") when the product has several recipe
/// versions in fieldwork; and "Recipe v2 by Acme Foods · 7 days ago".
class _HomeProductRow extends StatefulWidget {
  const _HomeProductRow({required this.feedback, required this.catalog});

  final Json feedback;
  final Json catalog;

  @override
  State<_HomeProductRow> createState() => _HomeProductRowState();
}

class _HomeProductRowState extends State<_HomeProductRow> {
  int? _version; // null = all versions

  @override
  Widget build(BuildContext context) {
    final p = widget.feedback;
    final versions = asJsonList(widget.catalog['survey_versions']);
    final picked = versions.where((v) => intOf(v['version']) == _version).firstOrNull;
    final responses = picked != null ? intOf(picked['responses']) : intOf(p['responses']);
    final rating = picked != null ? picked['rating'] : p['avg_rating'];
    final change = asJson(widget.catalog['recipe_change']);
    return Panel(
      onTap: () => _openReport(context, intOf(p['id']), str(p['name'])),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Expanded(
            child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Text(str(p['name']), style: TextStyle(fontWeight: FontWeight.w700, color: AppColors.ink)),
              const SizedBox(height: 2),
              Text([str(p['sku']), str(p['category_name'])].where((s) => s.isNotEmpty).join(' · '),
                  style: TextStyle(fontSize: 12, color: AppColors.muted)),
            ]),
          ),
          Pill(intOf(p['responses']) > 0 ? 'Has feedback' : 'No feedback yet', tone: intOf(p['responses']) > 0 ? 'ok' : 'muted'),
        ]),
        if (versions.length > 1) ...[
          const SizedBox(height: 8),
          DropdownButtonFormField<int?>(
            isExpanded: true,
            value: _version,
            decoration: const InputDecoration(isDense: true, contentPadding: EdgeInsets.symmetric(horizontal: 12, vertical: 8)),
            items: [
              DropdownMenuItem<int?>(value: null, child: Text('All ${versions.length} versions')),
              for (final v in versions)
                DropdownMenuItem<int?>(
                  value: intOf(v['version']),
                  child: Text('Version ${intOf(v['version'])}${v['live'] == true ? ' · collecting' : ''}'),
                ),
            ],
            onChanged: (v) => setState(() => _version = v),
          ),
        ],
        if (change.isNotEmpty && str(change['by_role']) == 'client') ...[
          const SizedBox(height: 6),
          Pill('Recipe v${intOf(change['version'])} by ${str(change['by_name'], 'the Super Admin')} · ${agoText(change['at'])}'),
        ],
        const SizedBox(height: 8),
        Row(children: [
          Text('$responses responses', style: TextStyle(fontWeight: FontWeight.w700, color: AppColors.ink2)),
          const SizedBox(width: 10),
          if (numOrNull(rating) != null) _stars(rating),
        ]),
      ]),
    );
  }
}

// ---------------------------------------------------------------- Catalog (read-only)

class _CatalogData {
  _CatalogData(this.categories, this.products);
  final List<Json> categories;
  final List<Json> products;
}

class AdminCatalogPage extends StatefulWidget {
  const AdminCatalogPage({super.key});

  @override
  State<AdminCatalogPage> createState() => _AdminCatalogPageState();
}

class _AdminCatalogPageState extends State<AdminCatalogPage> {
  int? _category;
  String _query = '';

  Future<_CatalogData> _load() async {
    final r = await Future.wait([AdminApi.categories(), AdminApi.products()]);
    return _CatalogData(r[0], r[1]);
  }

  @override
  Widget build(BuildContext context) {
    return Loaded<_CatalogData>(
      load: _load,
      builder: (context, d, reload) {
        final q = _query.toLowerCase();
        final shown = d.products
            .where((p) => _category == null || intOf(p['category_id']) == _category)
            .where((p) => q.isEmpty || '${p['name']} ${p['sku']}'.toLowerCase().contains(q))
            .toList();
        return ListView(
          padding: const EdgeInsets.fromLTRB(16, 8, 16, 24),
          children: [
            Text('The products executives survey. Products and categories are added and changed by the Super Admin.',
                style: TextStyle(color: AppColors.muted, height: 1.4)),
            const SizedBox(height: 12),
            SingleChildScrollView(
              scrollDirection: Axis.horizontal,
              child: Row(children: [
                Padding(
                  padding: const EdgeInsets.only(right: 6),
                  child: ChoiceChip(label: Text('All products · ${d.products.length}'), selected: _category == null,
                      onSelected: (_) => setState(() => _category = null)),
                ),
                for (final c in d.categories)
                  Padding(
                    padding: const EdgeInsets.only(right: 6),
                    child: ChoiceChip(
                      label: Text('${str(c['name'])} · ${intOf(c['product_count'])}'),
                      selected: _category == intOf(c['id']),
                      onSelected: (_) => setState(() => _category = intOf(c['id'])),
                    ),
                  ),
              ]),
            ),
            const SizedBox(height: 10),
            TextField(
              decoration: const InputDecoration(prefixIcon: Icon(Icons.search), hintText: 'Search products or SKUs'),
              onChanged: (v) => setState(() => _query = v),
            ),
            const SizedBox(height: 10),
            if (shown.isEmpty) const EmptyNote(icon: Icons.search_off, title: 'No products', text: 'Try a different name or category.'),
            for (final p in shown) ...[
              Panel(
                onTap: () async {
                  await Navigator.of(context).push(MaterialPageRoute(builder: (_) => AdminProductPage(product: p)));
                  await reload();
                },
                child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                  Row(children: [
                    Expanded(child: Text(str(p['name']), style: TextStyle(fontWeight: FontWeight.w700, color: AppColors.ink))),
                    Icon(Icons.chevron_right, color: AppColors.muted),
                  ]),
                  Text([str(p['sku']), str(p['category_name'])].where((s) => s.isNotEmpty).join(' · '),
                      style: TextStyle(fontSize: 12, color: AppColors.muted)),
                  const SizedBox(height: 8),
                  Wrap(spacing: 6, runSpacing: 6, children: [
                    Pill(p['survey_ready'] == true ? 'Ready for surveys' : 'Not ready', tone: p['survey_ready'] == true ? 'ok' : 'warn'),
                    Pill('${intOf(p['question_count'])} questions', tone: 'muted'),
                    Pill(p['has_recipe'] == true ? '✓ ${intOf(p['recipe_steps'])} steps' : 'No recipe', tone: p['has_recipe'] == true ? 'ok' : 'warn'),
                    Pill(_versions(p), tone: 'muted'),
                  ]),
                  if (asJson(p['recipe_change']).isNotEmpty && str(asJson(p['recipe_change'])['by_role']) == 'client') ...[
                    const SizedBox(height: 6),
                    Pill('Updated by ${str(asJson(p['recipe_change'])['by_name'], 'the Super Admin')} · ${agoText(asJson(p['recipe_change'])['at'])}',
                        tone: 'warn'),
                  ],
                ]),
              ),
              const SizedBox(height: 8),
            ],
          ],
        );
      },
    );
  }

  String _versions(Json p) {
    final v = p['recipe_versions'] is List ? (p['recipe_versions'] as List) : const [];
    if (v.isEmpty) return 'No versions';
    return '${v.length} ${v.length == 1 ? 'version' : 'versions'}: ${v.map((x) => 'v$x').join(' ')}';
  }
}

// ---------------------------------------------------------------- Catalog → one product

/// Like the web's product page for admins: Product details (read-only), Who can
/// survey and Rounds; the report opens from here.
class AdminProductPage extends StatelessWidget {
  const AdminProductPage({super.key, required this.product});

  final Json product;

  @override
  Widget build(BuildContext context) {
    final p = product;
    final live = asJson(p['live_round']);
    return DefaultTabController(
      length: 3,
      child: Scaffold(
        backgroundColor: AppColors.bg,
        appBar: AppBar(
          title: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Text(str(p['name']), overflow: TextOverflow.ellipsis),
            Text(str(p['category_name']), style: TextStyle(fontSize: 12, color: AppColors.muted, fontWeight: FontWeight.w500)),
          ]),
          bottom: TabBar(tabs: [
            const Tab(text: 'Product details'),
            const Tab(text: 'Who can survey'),
            Tab(text: live.isEmpty ? 'Rounds' : 'Rounds · ${intOf(live['number'])} live'),
          ]),
        ),
        body: TabBarView(children: [
          _ProductDetailsTab(product: p),
          _WhoCanSurveyTab(productId: intOf(p['id']), name: str(p['name'])),
          _ProductRoundsTab(productId: intOf(p['id'])),
        ]),
        bottomNavigationBar: SafeArea(
          minimum: const EdgeInsets.fromLTRB(16, 8, 16, 12),
          child: FilledButton.icon(
            onPressed: () => _openReport(context, intOf(p['id']), str(p['name'])),
            icon: const Icon(Icons.assessment_outlined),
            label: const Text('Open consumer report'),
          ),
        ),
      ),
    );
  }
}

class _ProductDetailsTab extends StatelessWidget {
  const _ProductDetailsTab({required this.product});

  final Json product;

  @override
  Widget build(BuildContext context) {
    final p = product;
    final versions = p['recipe_versions'] is List ? (p['recipe_versions'] as List) : const [];
    Widget row(String label, String value) => Padding(
          padding: const EdgeInsets.symmetric(vertical: 6),
          child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
            SizedBox(width: 130, child: Text(label, style: TextStyle(color: AppColors.muted))),
            Expanded(child: Text(value.isEmpty ? '—' : value, style: TextStyle(color: AppColors.ink, fontWeight: FontWeight.w600))),
          ]),
        );
    return ListView(padding: const EdgeInsets.all(16), children: [
      Panel(
        color: AppColors.brandSoft,
        child: Text('Products are added and changed by the Super Admin in their portal. You get a notification for every change.',
            style: TextStyle(color: AppColors.ink2, height: 1.4)),
      ),
      const SizedBox(height: 10),
      Panel(
        child: Column(children: [
          row('Product name', str(p['name'])),
          row('Category', str(p['category_name'])),
          row('SKU', str(p['sku'])),
          row('Description', str(p['description'])),
          row('Survey questions', '${intOf(p['question_count'])}'),
          row('Recipe', p['has_recipe'] == true ? '${intOf(p['recipe_steps'])} steps' : 'Not added'),
          row('Recipe versions', versions.isEmpty ? '' : versions.map((v) => 'v$v').join(', ')),
          row('Status', p['survey_ready'] == true ? 'Ready for surveys' : 'Not ready for surveys'),
        ]),
      ),
    ]);
  }
}

class _WhoCanSurveyTab extends StatefulWidget {
  const _WhoCanSurveyTab({required this.productId, required this.name});

  final int productId;
  final String name;

  @override
  State<_WhoCanSurveyTab> createState() => _WhoCanSurveyTabState();
}

/// Who surveys the product's LIVE round: the Super Admin starts each round, an admin
/// assigns it. Nothing is open to everyone; each new round is assigned again.
class _WhoCanSurveyTabState extends State<_WhoCanSurveyTab> {
  List<Json>? _people;
  Json? _round;
  bool _open = false; // existing product: open to everyone while nobody is chosen
  Set<int> _chosen = {};
  Set<int> _saved = {};
  bool _saving = false;
  String? _error;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final r = await Future.wait([AdminApi.team(), AdminApi.productExecutives(widget.productId)]);
      if (!mounted) return;
      final info = r[1] as Json;
      final ids = (info['user_ids'] is List ? (info['user_ids'] as List) : const []).map((e) => (e as num).toInt()).toSet();
      setState(() {
        _people = (r[0] as List<Json>).where((u) => str(u['role']) == 'executive').toList();
        _round = info['round'] is Map ? asJson(info['round']) : null;
        _open = info['needs_round'] == false;
        _chosen = {...ids};
        _saved = {...ids};
        _error = null;
      });
    } catch (e) {
      if (mounted) setState(() => _error = '$e');
    }
  }

  Future<void> _save() async {
    setState(() {
      _saving = true;
      _error = null;
    });
    try {
      await AdminApi.saveProductExecutives(widget.productId, _chosen.toList());
      if (!mounted) return;
      setState(() {
        _saving = false;
        _saved = {..._chosen};
      });
      final n = intOf(_round?['number']);
      showMessage(context, _chosen.isEmpty
          ? (_open ? '${widget.name} is open to every survey person again.' : 'Nobody is assigned to Round $n of ${widget.name}.')
          : '${widget.name} is assigned to ${_chosen.length} survey ${_chosen.length == 1 ? 'person' : 'persons'}.');
    } catch (e) {
      if (mounted) {
        setState(() {
          _saving = false;
          _error = '$e';
        });
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final people = _people;
    if (people == null && _error == null) return const Center(child: CircularProgressIndicator());
    final round = _round;
    if (people != null && round == null) {
      return ListView(padding: const EdgeInsets.all(16), children: [
        EmptyNote(
          icon: Icons.flag_outlined,
          title: 'No live round',
          text: 'The Super Admin starts the next round of ${widget.name}; you can assign it here once it has started. '
              'Earlier rounds keep their survey persons as history.',
        ),
      ]);
    }
    final n = intOf(round?['number']);
    final active = (people ?? const <Json>[]).where((u) => u['is_active'] != false).toList();
    final dirty = _chosen.length != _saved.length || !_chosen.containsAll(_saved);
    return ListView(padding: const EdgeInsets.all(16), children: [
      Text(_open ? 'Who can survey ${widget.name}' : 'Who surveys Round $n',
          style: TextStyle(fontWeight: FontWeight.w800, fontSize: 16, color: AppColors.ink)),
      const SizedBox(height: 4),
      Text(
          _open
              ? 'Surveyed before rounds were needed, so it stays open: every survey person sees it until you choose people. A new recipe version needs the Super Admin to start its round.'
              : 'Only the survey persons you assign see ${widget.name}, while Round $n is live. Each new round is assigned again.',
          style: TextStyle(color: AppColors.muted, height: 1.4)),
      const SizedBox(height: 8),
      Row(children: [
        Expanded(child: Text('${_chosen.length} of ${active.length} selected', style: TextStyle(color: AppColors.muted))),
        TextButton(onPressed: () => setState(() => _chosen = active.map((u) => intOf(u['id'])).toSet()), child: const Text('Select all')),
        TextButton(onPressed: () => setState(() => _chosen = {}), child: const Text('Clear')),
      ]),
      if (people != null && people.isEmpty) const EmptyNote(icon: Icons.groups_outlined, title: 'No survey persons yet', text: 'Add them on the Team tab.'),
      if (people != null)
        for (final u in people)
          CheckboxListTile(
            contentPadding: EdgeInsets.zero,
            value: _chosen.contains(intOf(u['id'])),
            onChanged: u['is_active'] == false
                ? null
                : (v) => setState(() => v == true ? _chosen.add(intOf(u['id'])) : _chosen.remove(intOf(u['id']))),
            title: Text(str(u['name'])),
            subtitle: Text(u['is_active'] == false
                ? 'Disabled: reactivate on the Team tab to assign'
                : str(u['mobile'], str(u['email']))),
          ),
      if (_error != null) Padding(padding: const EdgeInsets.only(top: 8), child: Text(_error!, style: TextStyle(color: AppColors.neg))),
      const SizedBox(height: 12),
      FilledButton(onPressed: _saving || !dirty ? null : _save, child: Text(_saving ? 'Saving…' : 'Save')),
    ]);
  }
}

class _ProductRoundsTab extends StatelessWidget {
  const _ProductRoundsTab({required this.productId});

  final int productId;

  @override
  Widget build(BuildContext context) {
    return Loaded<List<Json>>(
      load: () => AdminApi.productRounds(productId),
      builder: (context, rounds, reload) {
        final list = [...rounds]..sort((a, b) => intOf(b['number']).compareTo(intOf(a['number'])));
        return ListView(padding: const EdgeInsets.all(16), children: [
          Text('A round is one batch of consumer surveys for one recipe version. The Super Admin starts and ends rounds.',
              style: TextStyle(color: AppColors.muted, height: 1.4)),
          const SizedBox(height: 10),
          if (list.isEmpty) const EmptyNote(icon: Icons.flag_outlined, title: 'No rounds yet', text: 'The Super Admin starts the first round in their portal.'),
          for (final r in list) ...[
            Panel(
              child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                Row(children: [
                  Expanded(child: Text('Round ${intOf(r['number'])}', style: TextStyle(fontWeight: FontWeight.w800, fontSize: 15.5, color: AppColors.ink))),
                  Pill(str(r['status']) == 'live' ? 'Collecting' : (str(r['closed_reason']) == 'ended' ? 'Ended' : 'Complete'),
                      tone: str(r['status']) == 'live' ? 'ok' : 'muted'),
                ]),
                const SizedBox(height: 4),
                Text(
                  [
                    'Recipe v${intOf(r['recipe_version'])}',
                    '${intOf(r['done'])} of ${intOf(r['target'])} consumers',
                    if (numOrNull(r['rating']) != null) '${ratingText(r['rating'])} ★',
                    if (str(r['started_by_name']).isNotEmpty) 'started by ${str(r['started_by_name'])} ${agoText(r['started_at'])}',
                    if (str(r['note']).isNotEmpty) '“${str(r['note'])}”',
                  ].join(' · '),
                  style: TextStyle(fontSize: 12.5, color: AppColors.muted, height: 1.4),
                ),
                const SizedBox(height: 8),
                ClipRRect(
                  borderRadius: BorderRadius.circular(4),
                  child: LinearProgressIndicator(
                    value: intOf(r['target']) <= 0 ? 0 : (intOf(r['done']) / intOf(r['target'])).clamp(0, 1).toDouble(),
                    minHeight: 7,
                    backgroundColor: AppColors.line,
                    color: AppColors.brand,
                  ),
                ),
              ]),
            ),
            const SizedBox(height: 8),
          ],
        ]);
      },
    );
  }
}

// ---------------------------------------------------------------- Team

class AdminTeamPage extends StatefulWidget {
  const AdminTeamPage({super.key});

  @override
  State<AdminTeamPage> createState() => _AdminTeamPageState();
}

class _AdminTeamPageState extends State<AdminTeamPage> {
  String _filter = 'all';
  String _query = '';

  bool _activeToday(Json u) => intOf(asJson(u['stats'])['today']) > 0;

  @override
  Widget build(BuildContext context) {
    return Loaded<List<Json>>(
      load: AdminApi.team,
      builder: (context, users, reload) {
        final members = users.where((u) => str(u['role']) == 'executive').toList();
        final counts = {
          'all': members.length,
          'history': members.where((u) => u['can_view_history'] == true).length,
          'today': members.where(_activeToday).length,
          'disabled': members.where((u) => u['is_active'] == false).length,
        };
        final q = _query.toLowerCase();
        final shown = members.where((u) {
          final ok = switch (_filter) {
            'history' => u['can_view_history'] == true,
            'today' => _activeToday(u),
            'disabled' => u['is_active'] == false,
            _ => true,
          };
          return ok && (q.isEmpty || '${u['name']} ${u['mobile']} ${u['email']}'.toLowerCase().contains(q));
        }).toList();

        Future<void> add() async {
          final ok = await showModalBottomSheet<bool>(context: context, isScrollControlled: true, useSafeArea: true, builder: (_) => const _AddMemberSheet());
          if (ok == true) {
            await reload();
            if (context.mounted) showMessage(context, 'Team member added. Give them their mobile number and password.');
          }
        }

        return ListView(
          padding: const EdgeInsets.fromLTRB(16, 8, 16, 24),
          children: [
            Text('Survey executives using the mobile app. Add executives and choose who may see their survey history.',
                style: TextStyle(color: AppColors.muted, height: 1.4)),
            const SizedBox(height: 12),
            FilledButton.icon(onPressed: add, icon: const Icon(Icons.person_add_alt_1), label: const Text('Add team member')),
            const SizedBox(height: 12),
            TextField(
              decoration: const InputDecoration(prefixIcon: Icon(Icons.search), hintText: 'Search by name, mobile or email'),
              onChanged: (v) => setState(() => _query = v),
            ),
            const SizedBox(height: 10),
            Wrap(spacing: 6, runSpacing: 6, children: [
              for (final f in const [('all', 'All'), ('history', 'History on'), ('today', 'Active today'), ('disabled', 'Disabled')])
                ChoiceChip(label: Text('${f.$2} ${counts[f.$1]}'), selected: _filter == f.$1, onSelected: (_) => setState(() => _filter = f.$1)),
            ]),
            const SizedBox(height: 10),
            if (shown.isEmpty) const EmptyNote(icon: Icons.groups_outlined, title: 'No team members here', text: 'Add a survey executive to get started.'),
            for (final u in shown) ...[_MemberCard(user: u, onChanged: reload), const SizedBox(height: 8)],
            const SizedBox(height: 6),
            Text(
              'Survey history on: the executive\'s app shows the History tab with their own surveys. '
              'Active off: their login is refused; their account and surveys are kept. '
              'Sign out everywhere ends every sign-in of that person (lost or shared phone).',
              style: TextStyle(fontSize: 12, color: AppColors.muted, height: 1.45),
            ),
          ],
        );
      },
    );
  }
}

class _MemberCard extends StatelessWidget {
  const _MemberCard({required this.user, required this.onChanged});

  final Json user;
  final Future<void> Function() onChanged;

  @override
  Widget build(BuildContext context) {
    final u = user;
    final s = asJson(u['stats']);
    final id = intOf(u['id']);
    final assigned = u['assigned_products'] is List ? (u['assigned_products'] as List).length : 0;

    Future<void> run(Future<void> Function() action, String done) async {
      try {
        await action();
        await onChanged();
        if (context.mounted) showMessage(context, done);
      } catch (e) {
        if (context.mounted) showMessage(context, '$e', error: true);
      }
    }

    Future<void> resetPassword() async {
      final pw = await showDialog<String>(context: context, builder: (_) => _ResetPasswordDialog(name: str(u['name'])));
      if (pw != null) await run(() => AdminApi.resetPassword(id, pw), 'Password changed. ${str(u['name'])} is signed out everywhere.');
    }

    Future<void> signOut() async {
      final ok = await confirmAction(
        context,
        title: 'Sign ${str(u['name'])} out of every device?',
        message: 'They must sign in again with their password. Use this when a phone is lost or was shared. Surveys saved on the phone are not deleted.',
        confirmLabel: 'Sign out everywhere',
        cancelLabel: 'Cancel',
      );
      if (ok) await run(() => AdminApi.signOutMember(id), '${str(u['name'])} is signed out everywhere.');
    }

    Future<void> toggleActive() async {
      final disabling = u['is_active'] != false;
      final name = str(u['name']);
      final ok = await confirmAction(
        context,
        title: disabling ? 'Disable $name?' : 'Reactivate $name?',
        message: disabling
            ? "$name is signed out everywhere and can't log in or be assigned. Their surveys and history are kept; you can reactivate them any time."
            : '$name can log in again with their current password. Assign them to live rounds to give them products.',
        confirmLabel: disabling ? 'Disable' : 'Reactivate',
        cancelLabel: 'Cancel',
      );
      if (ok) {
        await run(() => AdminApi.updateMember(id, {'is_active': !disabling}),
            disabling ? '$name is disabled and signed out everywhere.' : '$name is active again.');
      }
    }

    Future<void> manageProducts() async {
      final saved = await showModalBottomSheet<bool>(
        context: context,
        isScrollControlled: true,
        useSafeArea: true,
        builder: (_) => _ProductsSheet(userId: id, name: str(u['name']), active: u['is_active'] != false),
      );
      if (saved == true) await onChanged();
    }

    return Panel(
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Row(children: [
          CircleAvatar(
            radius: 18,
            backgroundColor: AppColors.brandSoft,
            child: Text(str(u['name'], '?')[0].toUpperCase(), style: TextStyle(color: AppColors.brand, fontWeight: FontWeight.w800)),
          ),
          const SizedBox(width: 10),
          Expanded(
            child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Text(str(u['name']), style: TextStyle(fontWeight: FontWeight.w800, color: AppColors.ink)),
              Text(str(u['mobile'], str(u['email'])), style: TextStyle(fontSize: 12, color: AppColors.muted)),
            ]),
          ),
          if (u['is_active'] == false) const Pill('Disabled', tone: 'neg'),
        ]),
        const SizedBox(height: 10),
        Row(children: [
          Expanded(child: _num('Today', '${intOf(s['today'])}')),
          Expanded(child: _num('This week', '${intOf(s['this_week'])}')),
          Expanded(child: _num('Total', '${intOf(s['total'])}', sub: '${intOf(s['analyzed'])} analyzed')),
        ]),
        const SizedBox(height: 8),
        Text('Last survey ${agoText(s['last_survey_at']).isEmpty ? '—' : agoText(s['last_survey_at'])} · '
            'last login ${agoText(u['last_login_at']).isEmpty ? 'never' : agoText(u['last_login_at'])}',
            style: TextStyle(fontSize: 12, color: AppColors.muted)),
        const SizedBox(height: 8),
        OutlinedButton.icon(
          onPressed: manageProducts,
          icon: const Icon(Icons.inventory_2_outlined, size: 18),
          label: Text(assigned == 0 ? 'Nothing to survey · Manage' : 'Can survey $assigned ${assigned == 1 ? 'product' : 'products'} · Manage'),
        ),
        SwitchListTile(
          contentPadding: EdgeInsets.zero,
          dense: true,
          title: const Text('Survey history'),
          value: u['can_view_history'] == true,
          onChanged: (v) => run(() => AdminApi.updateMember(id, {'can_view_history': v}),
              v ? 'Survey history turned on.' : 'Survey history turned off.'),
        ),
        Row(children: [
          Text('Status', style: TextStyle(color: AppColors.ink2)),
          const SizedBox(width: 8),
          Pill(u['is_active'] == false ? 'Disabled' : 'Active', tone: u['is_active'] == false ? 'neg' : 'ok'),
          const Spacer(),
          if (id != Session.user.value?.id)
            TextButton(onPressed: toggleActive, child: Text(u['is_active'] == false ? 'Reactivate' : 'Disable')),
        ]),
        Wrap(spacing: 4, children: [
          TextButton(onPressed: resetPassword, child: const Text('Reset password')),
          TextButton(onPressed: signOut, child: const Text('Sign out everywhere')),
        ]),
      ]),
    );
  }

  Widget _num(String label, String value, {String? sub}) => Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Text(label, style: TextStyle(fontSize: 11.5, color: AppColors.muted)),
        Text(value, style: TextStyle(fontWeight: FontWeight.w800, fontSize: 16, color: AppColors.ink)),
        if (sub != null) Text(sub, style: TextStyle(fontSize: 11, color: AppColors.muted)),
      ]);
}

String _generatedPassword() {
  const words = ['Tiger', 'Mango', 'River', 'Cloud', 'Maple', 'Falcon', 'Lotus', 'Cedar'];
  final r = Random.secure();
  return '${words[r.nextInt(words.length)]}${1000 + r.nextInt(9000)}';
}

class _AddMemberSheet extends StatefulWidget {
  const _AddMemberSheet();

  @override
  State<_AddMemberSheet> createState() => _AddMemberSheetState();
}

class _AddMemberSheetState extends State<_AddMemberSheet> {
  final _name = TextEditingController();
  final _mobile = TextEditingController();
  final _email = TextEditingController();
  late final TextEditingController _password = TextEditingController(text: _generatedPassword());
  bool _history = false;
  bool _saving = false;
  String? _error;

  @override
  void dispose() {
    for (final c in [_name, _mobile, _email, _password]) {
      c.dispose();
    }
    super.dispose();
  }

  Future<void> _save() async {
    final digits = _mobile.text.replaceAll(RegExp(r'\D'), '');
    final pw = _password.text;
    String? problem;
    if (_name.text.trim().length < 2) {
      problem = 'Enter the full name.';
    } else if (digits.length < 10) {
      problem = 'Enter a 10-digit mobile number.';
    } else if (pw.length < 8 || !RegExp(r'[A-Za-z]').hasMatch(pw) || !RegExp(r'\d').hasMatch(pw)) {
      problem = 'Password: at least 8 characters with letters and numbers.';
    }
    if (problem != null) {
      setState(() => _error = problem);
      return;
    }
    setState(() {
      _saving = true;
      _error = null;
    });
    try {
      await AdminApi.addMember({
        'name': _name.text.trim(),
        'mobile': _mobile.text.trim(),
        if (_email.text.trim().isNotEmpty) 'email': _email.text.trim(),
        'password': pw,
        'can_view_history': _history,
        'role': 'executive',
      });
      if (mounted) Navigator.of(context).pop(true);
    } catch (e) {
      if (mounted) {
        setState(() {
          _saving = false;
          _error = '$e';
        });
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: EdgeInsets.fromLTRB(20, 8, 20, 16 + MediaQuery.of(context).viewInsets.bottom),
      child: SingleChildScrollView(
        child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, mainAxisSize: MainAxisSize.min, children: [
          Text('Add team member', style: TextStyle(fontSize: 18, fontWeight: FontWeight.w800, color: AppColors.ink)),
          const SizedBox(height: 4),
          Text('Create their login. They sign in to the mobile app with this mobile number and password.', style: TextStyle(color: AppColors.muted)),
          const SizedBox(height: 14),
          TextField(controller: _name, decoration: const InputDecoration(labelText: 'Full name', hintText: 'e.g. Riya Kapoor')),
          const SizedBox(height: 10),
          TextField(controller: _mobile, keyboardType: TextInputType.phone, decoration: const InputDecoration(labelText: 'Mobile number')),
          const SizedBox(height: 10),
          TextField(controller: _email, keyboardType: TextInputType.emailAddress, decoration: const InputDecoration(labelText: 'Email (optional)')),
          const SizedBox(height: 10),
          TextField(
            controller: _password,
            decoration: InputDecoration(
              labelText: 'Password',
              suffixIcon: IconButton(
                tooltip: 'Generate',
                icon: const Icon(Icons.autorenew),
                onPressed: () => setState(() => _password.text = _generatedPassword()),
              ),
            ),
          ),
          CheckboxListTile(
            contentPadding: EdgeInsets.zero,
            value: _history,
            onChanged: (v) => setState(() => _history = v ?? false),
            title: const Text('Can see their survey history'),
            subtitle: const Text('Ratings and summaries of their own surveys in the app.'),
          ),
          if (_error != null) Padding(padding: const EdgeInsets.only(bottom: 8), child: Text(_error!, style: TextStyle(color: AppColors.neg))),
          FilledButton(onPressed: _saving ? null : _save, child: Text(_saving ? 'Adding…' : 'Add member')),
          TextButton(onPressed: () => Navigator.of(context).pop(false), child: const Text('Cancel')),
        ]),
      ),
    );
  }
}

class _ResetPasswordDialog extends StatefulWidget {
  const _ResetPasswordDialog({required this.name});

  final String name;

  @override
  State<_ResetPasswordDialog> createState() => _ResetPasswordDialogState();
}

class _ResetPasswordDialogState extends State<_ResetPasswordDialog> {
  late final TextEditingController _pw = TextEditingController(text: _generatedPassword());
  String? _error;

  @override
  void dispose() {
    _pw.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return AlertDialog(
      title: Text('Reset ${widget.name}\'s password'),
      content: Column(mainAxisSize: MainAxisSize.min, crossAxisAlignment: CrossAxisAlignment.start, children: [
        const Text('They are signed out everywhere and sign in with this new password.'),
        const SizedBox(height: 12),
        TextField(controller: _pw, decoration: const InputDecoration(labelText: 'New password')),
        if (_error != null) Padding(padding: const EdgeInsets.only(top: 8), child: Text(_error!, style: TextStyle(color: AppColors.neg))),
      ]),
      actions: [
        TextButton(onPressed: () => Navigator.of(context).pop(), child: const Text('Cancel')),
        FilledButton(
          onPressed: () {
            final p = _pw.text;
            if (p.length < 8 || !RegExp(r'[A-Za-z]').hasMatch(p) || !RegExp(r'\d').hasMatch(p)) {
              setState(() => _error = 'At least 8 characters with letters and numbers.');
              return;
            }
            Navigator.of(context).pop(p);
          },
          child: const Text('Reset password'),
        ),
      ],
    );
  }
}

class _ProductsSheet extends StatefulWidget {
  const _ProductsSheet({required this.userId, required this.name, required this.active});

  final int userId;
  final String name;
  final bool active;

  @override
  State<_ProductsSheet> createState() => _ProductsSheetState();
}

/// The live rounds a survey person surveys (by product). Only products whose round
/// the Super Admin has started; nothing is open to everyone.
class _ProductsSheetState extends State<_ProductsSheet> {
  List<Json>? _products;
  Set<int> _selected = {};
  bool _saving = false;
  String? _error;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final r = await Future.wait([AdminApi.products(), AdminApi.memberProducts(widget.userId)]);
      if (!mounted) return;
      setState(() {
        // Products with a live round, and existing products open without one.
        _products = (r[0] as List<Json>).where((p) => asJson(p['live_round']).isNotEmpty || p['needs_round'] == false).toList();
        _selected = (r[1] as List<int>).toSet();
      });
    } catch (e) {
      if (mounted) setState(() => _error = '$e');
    }
  }

  Future<void> _save() async {
    setState(() {
      _saving = true;
      _error = null;
    });
    try {
      await AdminApi.saveMemberProducts(widget.userId, _selected.toList());
      if (mounted) Navigator.of(context).pop(true);
    } catch (e) {
      if (mounted) {
        setState(() {
          _saving = false;
          _error = '$e';
        });
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final products = _products;
    return Padding(
      padding: const EdgeInsets.fromLTRB(20, 8, 20, 16),
      child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, mainAxisSize: MainAxisSize.min, children: [
        Text('What ${widget.name} surveys', style: TextStyle(fontSize: 18, fontWeight: FontWeight.w800, color: AppColors.ink)),
        const SizedBox(height: 4),
        Text('Only the people you assign see a product. Existing products are open to everyone until you assign people; each new round the Super Admin starts is assigned again.',
            style: TextStyle(color: AppColors.muted, height: 1.4)),
        if (!widget.active) ...[
          const SizedBox(height: 8),
          Text('${widget.name} is disabled. Reactivate them to assign products.', style: TextStyle(color: AppColors.neg)),
        ],
        const SizedBox(height: 8),
        if (products == null && _error == null) const Padding(padding: EdgeInsets.all(24), child: Center(child: CircularProgressIndicator())),
        if (products != null && products.isEmpty)
          const Padding(
            padding: EdgeInsets.symmetric(vertical: 12),
            child: Text('No product has a live round. The Super Admin starts rounds; assign them here once started.'),
          ),
        if (products != null && products.isNotEmpty)
          ConstrainedBox(
            constraints: const BoxConstraints(maxHeight: 360),
            child: ListView(shrinkWrap: true, children: [
              for (final p in products)
                CheckboxListTile(
                  contentPadding: EdgeInsets.zero,
                  value: _selected.contains(intOf(p['id'])),
                  onChanged: !widget.active
                      ? null
                      : (v) => setState(() => v == true ? _selected.add(intOf(p['id'])) : _selected.remove(intOf(p['id']))),
                  title: Text(str(p['name'])),
                  subtitle: Text(asJson(p['live_round']).isNotEmpty
                      ? 'Round ${intOf(asJson(p['live_round'])['number'])} · ${str(p['category_name'])}'
                      : 'Existing product · open to everyone unless you assign people'),
                ),
            ]),
          ),
        if (_error != null) Padding(padding: const EdgeInsets.only(top: 8), child: Text(_error!, style: TextStyle(color: AppColors.neg))),
        const SizedBox(height: 12),
        FilledButton(onPressed: _saving || products == null || !widget.active ? null : _save, child: Text(_saving ? 'Saving…' : 'Save')),
        TextButton(onPressed: () => Navigator.of(context).pop(false), child: const Text('Cancel')),
      ]),
    );
  }
}