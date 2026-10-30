import 'package:flutter/material.dart';

import '../main.dart';
import '../services/org_api.dart';
import '../services/session.dart';
import 'home_screens.dart' show MobileSettingsPage;
import 'org_catalog_screen.dart';
import 'org_product_screens.dart';

/* ============================================================
   Organization (client role) in the app
   ============================================================
   The same pages as the organization's web portal:
     Overview   numbers and every product's current round
     Products   each product: report, recipe, questions, rounds, compare
     Catalog    add and change products and categories
     Updates    what happened (rounds, reports, recipe versions)
   Survey persons and admins keep HomeShell; nextScreen() picks the shell.
*/

// ---------------------------------------------------------------- shared helpers

/// "5 min ago", "yesterday", "Oct 28" from an ISO date (UTC when it has no zone).
String agoText(dynamic iso) {
  final raw = str(iso);
  if (raw.isEmpty) return '';
  final t = DateTime.tryParse(raw.endsWith('Z') || raw.contains('+') ? raw : '${raw}Z');
  if (t == null) return '';
  final d = DateTime.now().difference(t.toLocal());
  if (d.inMinutes < 1) return 'just now';
  if (d.inMinutes < 60) return '${d.inMinutes} min ago';
  if (d.inHours < 24) return '${d.inHours} h ago';
  if (d.inDays == 1) return 'yesterday';
  if (d.inDays < 7) return '${d.inDays} days ago';
  const months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  final l = t.toLocal();
  return '${months[l.month - 1]} ${l.day}';
}

String ratingText(dynamic v) {
  final n = numOrNull(v);
  return n == null ? '—' : n.toStringAsFixed(1);
}

/// "Round 2 · collecting · 5 of 10" for a round map.
String roundLine(Json? r) {
  if (r == null || r.isEmpty) return 'No round yet';
  final status = str(r['status']);
  final state = status == 'live'
      ? 'collecting'
      : (str(r['closed_reason']) == 'ended' ? 'ended' : 'complete');
  return 'Round ${intOf(r['number'])} · $state · ${intOf(r['done'])} of ${intOf(r['target'])}';
}

void showMessage(BuildContext context, String text, {bool error = false}) {
  ScaffoldMessenger.of(context)
    ..hideCurrentSnackBar()
    ..showSnackBar(SnackBar(
      content: Text(text),
      backgroundColor: error ? AppColors.neg : null,
      behavior: SnackBarBehavior.floating,
    ));
}

/// Loads data, shows a spinner, an error with Retry, or the content; pull to refresh.
class Loaded<T> extends StatefulWidget {
  const Loaded({super.key, required this.load, required this.builder, this.empty});

  final Future<T> Function() load;
  final Widget Function(BuildContext context, T data, Future<void> Function() reload) builder;
  final bool Function(T data)? empty;

  @override
  State<Loaded<T>> createState() => LoadedState<T>();
}

class LoadedState<T> extends State<Loaded<T>> {
  T? _data;
  String? _error;
  bool _loading = true;

  @override
  void initState() {
    super.initState();
    reload();
  }

  Future<void> reload() async {
    setState(() {
      _loading = _data == null;
      _error = null;
    });
    try {
      final d = await widget.load();
      if (!mounted) return;
      setState(() {
        _data = d;
        _loading = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _error = '$e';
        _loading = false;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    if (_loading) return const Center(child: CircularProgressIndicator());
    if (_error != null && _data == null) {
      return ErrorRetry(message: _error!, onRetry: reload);
    }
    return RefreshIndicator(
      onRefresh: reload,
      child: widget.builder(context, _data as T, reload),
    );
  }
}

class ErrorRetry extends StatelessWidget {
  const ErrorRetry({super.key, required this.message, required this.onRetry});

  final String message;
  final VoidCallback onRetry;

  @override
  Widget build(BuildContext context) {
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(28),
        child: Column(mainAxisSize: MainAxisSize.min, children: [
          Icon(Icons.cloud_off_outlined, size: 40, color: AppColors.muted),
          const SizedBox(height: 10),
          Text(message, textAlign: TextAlign.center, style: TextStyle(color: AppColors.ink2)),
          const SizedBox(height: 14),
          FilledButton.tonal(onPressed: onRetry, child: const Text('Try again')),
        ]),
      ),
    );
  }
}

class EmptyNote extends StatelessWidget {
  const EmptyNote({super.key, required this.icon, required this.title, required this.text});

  final IconData icon;
  final String title;
  final String text;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 40, horizontal: 24),
      child: Column(children: [
        Icon(icon, size: 40, color: AppColors.muted),
        const SizedBox(height: 10),
        Text(title, style: TextStyle(fontWeight: FontWeight.w700, fontSize: 16, color: AppColors.ink)),
        const SizedBox(height: 4),
        Text(text, textAlign: TextAlign.center, style: TextStyle(color: AppColors.muted)),
      ]),
    );
  }
}

class Panel extends StatelessWidget {
  const Panel({super.key, required this.child, this.padding = const EdgeInsets.all(14), this.color, this.onTap});

  final Widget child;
  final EdgeInsets padding;
  final Color? color;
  final VoidCallback? onTap;

  @override
  Widget build(BuildContext context) {
    final box = Container(
      padding: padding,
      decoration: BoxDecoration(
        color: color ?? AppColors.surface,
        borderRadius: BorderRadius.circular(16),
        border: Border.all(color: AppColors.line),
      ),
      child: child,
    );
    if (onTap == null) return box;
    return Material(
      color: Colors.transparent,
      child: InkWell(borderRadius: BorderRadius.circular(16), onTap: onTap, child: box),
    );
  }
}

class Pill extends StatelessWidget {
  const Pill(this.text, {super.key, this.tone = 'info'});

  final String text;
  final String tone; // info | ok | warn | neg | muted

  @override
  Widget build(BuildContext context) {
    final (bg, fg) = switch (tone) {
      'ok' => (AppColors.posSoft, AppColors.pos),
      'warn' => (AppColors.warnSoft, AppColors.warn),
      'neg' => (AppColors.negSoft, AppColors.neg),
      'muted' => (AppColors.line, AppColors.ink2),
      _ => (AppColors.brandSoft, AppColors.brand),
    };
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 9, vertical: 3),
      decoration: BoxDecoration(color: bg, borderRadius: BorderRadius.circular(99)),
      child: Text(text, style: TextStyle(color: fg, fontSize: 11.5, fontWeight: FontWeight.w700)),
    );
  }
}

class StatBox extends StatelessWidget {
  const StatBox({super.key, required this.value, required this.label, this.child});

  final String value;
  final String label;
  final Widget? child;

  @override
  Widget build(BuildContext context) {
    return Panel(
      padding: const EdgeInsets.fromLTRB(12, 10, 12, 10),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        if (child == null)
          Text(value, style: TextStyle(fontSize: 22, fontWeight: FontWeight.w800, color: AppColors.ink))
        else
          child!,
        const SizedBox(height: 2),
        Text(label, style: TextStyle(fontSize: 12, color: AppColors.muted)),
      ]),
    );
  }
}

void openOrgProduct(BuildContext context, int id, String name, {String tab = 'report'}) {
  Navigator.of(context).push(MaterialPageRoute(builder: (_) => OrgProductPage(productId: id, name: name, initialTab: tab)));
}

// ---------------------------------------------------------------- shell

class OrgShell extends StatefulWidget {
  const OrgShell({super.key});

  @override
  State<OrgShell> createState() => _OrgShellState();
}

class _OrgShellState extends State<OrgShell> {
  int _tab = 0;
  int _unread = 0;
  final _keys = List.generate(4, (_) => GlobalKey());

  static const _titles = ['Overview', 'Products', 'Catalog', 'Updates'];

  @override
  void initState() {
    super.initState();
    _refreshUnread();
  }

  Future<void> _refreshUnread() async {
    try {
      final n = await OrgApi.unreadUpdates();
      if (mounted) setState(() => _unread = n);
    } catch (_) {
      // The badge is a convenience; the Updates page shows any error.
    }
  }

  void _select(int i) {
    setState(() => _tab = i);
    if (i == 3) {
      OrgApi.markUpdatesSeen().then((_) {
        if (mounted) setState(() => _unread = 0);
      }).catchError((_) {});
    } else {
      _refreshUnread();
    }
  }

  @override
  Widget build(BuildContext context) {
    final name = Session.user.value?.name ?? 'Super Admin';
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
          Expanded(child: Text(_tab == 0 ? name : _titles[_tab], overflow: TextOverflow.ellipsis)),
        ]),
        actions: [
          IconButton(
            tooltip: 'Settings',
            onPressed: () => Navigator.of(context).push(MaterialPageRoute(builder: (_) => const MobileSettingsPage())),
            icon: CircleAvatar(
              radius: 15,
              backgroundColor: AppColors.brandSoft,
              child: Text(name.isEmpty ? 'O' : name[0].toUpperCase(),
                  style: TextStyle(color: AppColors.brand, fontWeight: FontWeight.w800, fontSize: 13)),
            ),
          ),
          const SizedBox(width: 6),
        ],
      ),
      body: IndexedStack(index: _tab, children: [
        OrgOverviewPage(key: _keys[0]),
        OrgProductsPage(key: _keys[1]),
        OrgCatalogPage(key: _keys[2]),
        OrgUpdatesPage(key: _keys[3]),
      ]),
      bottomNavigationBar: NavigationBar(
        selectedIndex: _tab,
        onDestinationSelected: _select,
        destinations: [
          const NavigationDestination(icon: Icon(Icons.dashboard_outlined), selectedIcon: Icon(Icons.dashboard), label: 'Overview'),
          const NavigationDestination(icon: Icon(Icons.inventory_2_outlined), selectedIcon: Icon(Icons.inventory_2), label: 'Products'),
          const NavigationDestination(icon: Icon(Icons.category_outlined), selectedIcon: Icon(Icons.category), label: 'Catalog'),
          NavigationDestination(
            icon: Badge(isLabelVisible: _unread > 0, label: Text('$_unread'), child: const Icon(Icons.notifications_outlined)),
            selectedIcon: const Icon(Icons.notifications),
            label: 'Updates',
          ),
        ],
      ),
    );
  }
}

// ---------------------------------------------------------------- overview

class OrgOverviewPage extends StatelessWidget {
  const OrgOverviewPage({super.key});

  String _greeting() {
    final h = DateTime.now().hour;
    return h < 12 ? 'Good morning' : (h < 17 ? 'Good afternoon' : 'Good evening');
  }

  @override
  Widget build(BuildContext context) {
    return Loaded<Json>(
      load: OrgApi.overview,
      builder: (context, data, reload) {
        final n = asJson(data['numbers']);
        final products = asJsonList(data['products']);
        return ListView(
          padding: const EdgeInsets.fromLTRB(16, 8, 16, 24),
          children: [
            Text(_greeting(), style: TextStyle(fontSize: 24, fontWeight: FontWeight.w800, color: AppColors.ink)),
            const SizedBox(height: 2),
            Text('Where your products stand with consumers.', style: TextStyle(color: AppColors.muted)),
            const SizedBox(height: 14),
            Row(children: [
              Expanded(child: StatBox(value: '${intOf(n['products'])}', label: 'Products')),
              const SizedBox(width: 8),
              Expanded(child: StatBox(value: '${intOf(n['responses'])}', label: 'Consumer responses')),
              const SizedBox(width: 8),
              Expanded(child: StatBox(value: '${intOf(n['open_rounds'])}', label: 'Open rounds')),
            ]),
            const SizedBox(height: 18),
            Text('Your products', style: TextStyle(fontWeight: FontWeight.w800, fontSize: 16, color: AppColors.ink)),
            const SizedBox(height: 8),
            if (products.isEmpty)
              const EmptyNote(icon: Icons.inventory_2_outlined, title: 'No products yet', text: 'Add your first product in Catalog.'),
            for (final p in products) ...[
              _ProductRow(p: p),
              const SizedBox(height: 8),
            ],
          ],
        );
      },
    );
  }
}

class _ProductRow extends StatelessWidget {
  const _ProductRow({required this.p});

  final Json p;

  @override
  Widget build(BuildContext context) {
    final round = asJson(p['current_round']);
    final rating = numOrNull(round['rating']);
    final prev = numOrNull(p['previous_rating']);
    return Panel(
      onTap: () => openOrgProduct(context, intOf(p['id']), str(p['name'])),
      child: Row(children: [
        Expanded(
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Text(str(p['name']), style: TextStyle(fontWeight: FontWeight.w700, color: AppColors.ink)),
            const SizedBox(height: 3),
            Text(roundLine(round.isEmpty ? null : round), style: TextStyle(fontSize: 12.5, color: AppColors.muted)),
          ]),
        ),
        if (rating != null)
          Text(prev != null ? '${ratingText(prev)} → ${ratingText(rating)}' : ratingText(rating),
              style: TextStyle(fontWeight: FontWeight.w800, color: AppColors.ink)),
        const SizedBox(width: 4),
        Icon(Icons.chevron_right, color: AppColors.muted),
      ]),
    );
  }
}

// ---------------------------------------------------------------- products

class OrgProductsPage extends StatefulWidget {
  const OrgProductsPage({super.key});

  @override
  State<OrgProductsPage> createState() => _OrgProductsPageState();
}

class _OrgProductsPageState extends State<OrgProductsPage> {
  String _query = '';

  @override
  Widget build(BuildContext context) {
    return Loaded<List<Json>>(
      load: OrgApi.products,
      builder: (context, products, reload) {
        final q = _query.toLowerCase();
        final shown = products
            .where((p) => q.isEmpty || '${p['name']} ${p['sku']} ${p['category_name']}'.toLowerCase().contains(q))
            .toList();
        return ListView(
          padding: const EdgeInsets.fromLTRB(16, 8, 16, 24),
          children: [
            TextField(
              decoration: const InputDecoration(prefixIcon: Icon(Icons.search), hintText: 'Search products'),
              onChanged: (v) => setState(() => _query = v),
            ),
            const SizedBox(height: 12),
            if (shown.isEmpty)
              EmptyNote(
                icon: Icons.search_off,
                title: products.isEmpty ? 'No products yet' : 'No matches',
                text: products.isEmpty ? 'Add your first product in Catalog.' : 'Try a different name or SKU.',
              ),
            for (final p in shown) ...[
              Panel(
                onTap: () => openOrgProduct(context, intOf(p['id']), str(p['name'])),
                child: Row(children: [
                  Container(
                    width: 38,
                    height: 38,
                    decoration: BoxDecoration(color: AppColors.brandSoft, borderRadius: BorderRadius.circular(11)),
                    child: Icon(Icons.inventory_2_outlined, color: AppColors.brand, size: 20),
                  ),
                  const SizedBox(width: 12),
                  Expanded(
                    child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                      Text(str(p['name']), style: TextStyle(fontWeight: FontWeight.w700, color: AppColors.ink)),
                      const SizedBox(height: 3),
                      Text(
                        [
                          str(p['category_name']),
                          if (asJson(p['recipe']).isNotEmpty) 'Recipe v${intOf(asJson(p['recipe'])['version'])}' else 'No recipe',
                        ].join(' · '),
                        style: TextStyle(fontSize: 12.5, color: AppColors.muted),
                      ),
                    ]),
                  ),
                  Icon(Icons.chevron_right, color: AppColors.muted),
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

// ---------------------------------------------------------------- updates

class OrgUpdatesPage extends StatelessWidget {
  const OrgUpdatesPage({super.key});

  IconData _icon(String kind) {
    if (kind.contains('round_started')) return Icons.play_circle_outline;
    if (kind.contains('round')) return Icons.flag_outlined;
    if (kind.contains('report')) return Icons.assessment_outlined;
    if (kind.contains('recipe')) return Icons.edit_note;
    return Icons.notifications_none;
  }

  @override
  Widget build(BuildContext context) {
    return Loaded<Json>(
      load: OrgApi.updates,
      builder: (context, data, reload) {
        final updates = asJsonList(data['updates']);
        return ListView(
          padding: const EdgeInsets.fromLTRB(16, 8, 16, 24),
          children: [
            if (updates.isEmpty)
              const EmptyNote(
                icon: Icons.notifications_none,
                title: 'No updates yet',
                text: 'Rounds, reports and recipe versions appear here.',
              ),
            for (final u in updates) ...[
              Panel(
                color: u['unread'] == true ? AppColors.brandSoft : null,
                onTap: intOf(u['product_id']) > 0
                    ? () => openOrgProduct(context, intOf(u['product_id']), '', tab: str(u['tab'], 'report'))
                    : null,
                child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
                  Icon(_icon(str(u['kind'])), color: AppColors.brand),
                  const SizedBox(width: 12),
                  Expanded(
                    child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                      Text(str(u['title']), style: TextStyle(fontWeight: FontWeight.w700, color: AppColors.ink)),
                      if (str(u['text']).isNotEmpty) ...[
                        const SizedBox(height: 3),
                        Text(str(u['text']), style: TextStyle(fontSize: 12.5, color: AppColors.ink2)),
                      ],
                      const SizedBox(height: 4),
                      Text(agoText(u['at']), style: TextStyle(fontSize: 11.5, color: AppColors.muted)),
                    ]),
                  ),
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