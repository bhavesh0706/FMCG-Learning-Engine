import 'package:flutter/material.dart';

import '../main.dart';
import '../widgets/design.dart';
import '../services/org_api.dart';
import 'org_screens.dart';
import 'report_screens.dart' show ToneBar;
import 'next_update_screens.dart' show RecipeExtrasView, ReportDownloads, ActionPlansView;
import '../widgets/search_dropdown.dart';
import 'survey_snapshot.dart';
import 'project_screens.dart' show ProductSetupBar, openProjectFor;

/* ============================================================
   One product (organization): Report · Recipe · Questions · Rounds · Compare
   ============================================================ */

// Survey questions are edited in the project (one shared set per product): no tab here.
const _tabs = ['standards', 'recipe', 'report', 'plans', 'rounds', 'compare'];

class OrgProductPage extends StatefulWidget {
  const OrgProductPage({super.key, required this.productId, required this.name, this.initialTab = 'report'});

  final int productId;
  final String name;
  final String initialTab;

  @override
  State<OrgProductPage> createState() => _OrgProductPageState();
}

class _OrgProductPageState extends State<OrgProductPage> {
  Json? _detail;
  String? _error;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final d = await OrgApi.product(widget.productId);
      if (mounted) setState(() {
        _detail = d;
        _error = null;
      });
    } catch (e) {
      if (mounted) setState(() => _error = '$e');
    }
  }

  @override
  Widget build(BuildContext context) {
    final product = asJson(_detail?['product']);
    final name = str(product['name'], widget.name.isEmpty ? 'Product' : widget.name);
    final latest = intOf(_detail?['latest_version']);
    final start = _tabs.indexOf(widget.initialTab).clamp(0, _tabs.length - 1);
    return DefaultTabController(
      length: _tabs.length,
      initialIndex: start,
      child: Scaffold(
        backgroundColor: AppColors.bg,
        appBar: AppBar(
          title: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Text(name, overflow: TextOverflow.ellipsis),
            if (_detail != null)
              Text(
                [str(product['category_name']), if (latest > 0) 'Recipe v$latest is the latest'].where((s) => s.isNotEmpty).join(' · '),
                style: TextStyle(fontSize: 12, color: AppColors.muted, fontWeight: FontWeight.w500),
              ),
          ]),
          bottom: const TabBar(
            isScrollable: true,
            tabAlignment: TabAlignment.start,
            tabs: [Tab(text: 'Standards'), Tab(text: 'Recipe'), Tab(text: 'Report'), Tab(text: 'Plans'), Tab(text: 'Survey runs'), Tab(text: 'Compare')],
          ),
        ),
        body: _error != null && _detail == null
            ? ErrorRetry(message: _error!, onRetry: _load)
            : _detail == null
                ? const Center(child: CircularProgressIndicator())
                : Column(children: [
                    // the product's setup steps, and Done -> project creation (rebuilt after every reload)
                    ProductSetupBar(key: ValueKey('${intOf(_detail?['latest_version'])}-${asJsonList(_detail?['rounds']).length}'), productId: widget.productId),
                    Expanded(
                      child: TabBarView(children: [
                    // This product's recipe standards (results, add / edit / remove)
                    // Standards, Recipe, Questions: the current setup, or (chosen above) what a survey used.
                    SurveyScoped(
                      productId: widget.productId,
                      detail: _detail!,
                      kind: 'standards',
                      child: RefreshIndicator(
                        onRefresh: _load,
                        child: ListView(
                          padding: const EdgeInsets.fromLTRB(16, 12, 16, 32),
                          children: [RecipeExtrasView(productId: widget.productId, canEdit: true, standardsOnly: true)],
                        ),
                      ),
                    ),
                    SurveyScoped(productId: widget.productId, detail: _detail!, kind: 'recipe',
                        snapshotView: (d) => _RecipeTab(productId: widget.productId, onSaved: _load, snapshot: {...asJson(d['recipe']), '_costing': d['costing']}),
                        child: _RecipeTab(productId: widget.productId, onSaved: _load)),
                    _ReportTab(productId: widget.productId),
                    ActionPlansView(productId: widget.productId),
                    _RoundsTab(productId: widget.productId, detail: _detail!, onChanged: _load),
                    RoundCompareView(productId: widget.productId),
                  ])),
                  ]),
      ),
    );
  }
}

// ---------------------------------------------------------------- small pieces

Widget _section(String title) => Padding(
      padding: const EdgeInsets.fromLTRB(2, 18, 2, 8),
      child: Text(title, style: TextStyle(fontWeight: FontWeight.w800, fontSize: 15.5, color: AppColors.ink)),
    );

/// Horizontal bar with a label and a count ("5 ★ ▇▇▇ 3").
class _CountBar extends StatelessWidget {
  const _CountBar({required this.label, required this.count, required this.total});

  final String label;
  final int count;
  final int total;

  @override
  Widget build(BuildContext context) {
    final f = total <= 0 ? 0.0 : count / total;
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 3),
      child: Row(children: [
        SizedBox(width: 92, child: Text(label, overflow: TextOverflow.ellipsis, style: TextStyle(fontSize: 12.5, color: AppColors.ink2))),
        Expanded(
          child: ClipRRect(
            borderRadius: BorderRadius.circular(4),
            child: LinearProgressIndicator(value: f, minHeight: 8, backgroundColor: AppColors.line, color: AppColors.brand),
          ),
        ),
        const SizedBox(width: 8),
        SizedBox(width: 64, child: Text('$count · ${(f * 100).round()}%', textAlign: TextAlign.right, style: TextStyle(fontSize: 12, color: AppColors.muted))),
      ]),
    );
  }
}

Widget _bullet(String text, {IconData icon = Icons.circle, Color? color, String? trailing}) => Padding(
      padding: const EdgeInsets.symmetric(vertical: 4),
      child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Padding(padding: const EdgeInsets.only(top: 3), child: Icon(icon, size: icon == Icons.circle ? 7 : 16, color: color ?? AppColors.brand)),
        const SizedBox(width: 8),
        Expanded(child: Text(text, style: TextStyle(height: 1.4, color: AppColors.ink))),
        if (trailing != null) Text(trailing, style: TextStyle(fontSize: 12, fontWeight: FontWeight.w800, color: AppColors.ink2)),
      ]),
    );

// ---------------------------------------------------------------- report

class _ReportTab extends StatefulWidget {
  const _ReportTab({required this.productId});

  final int productId;

  @override
  State<_ReportTab> createState() => _ReportTabState();
}

class _ReportTabState extends State<_ReportTab> {
  int? _round;

  @override
  Widget build(BuildContext context) {
    return Loaded<Json>(
      key: ValueKey('report-$_round'),
      load: () => OrgApi.report(widget.productId, round: _round),
      builder: (context, data, reload) {
        final current = asJson(data['round']);
        final rounds = <int, Json>{};
        for (final r in [...asJsonList(data['rounds']), if (current.isNotEmpty) current]) {
          rounds[intOf(r['number'])] = r;
        }
        final numbers = rounds.keys.toList()..sort((a, b) => b.compareTo(a));
        final report = asJson(data['report']);
        final stats = asJson(report['stats']);
        final nv = asJson(report['narrative']);
        final n = intOf(stats['response_count']);
        final selected = _round ?? intOf(current['number']);
        return ListView(
          padding: const EdgeInsets.fromLTRB(16, 12, 16, 28),
          children: [
            if (n > 0) Padding(padding: const EdgeInsets.only(bottom: 10), child: ReportDownloads(productId: widget.productId, round: selected)),
            if (numbers.isNotEmpty)
              SearchDropdownField<int>(
                isExpanded: true,
                value: numbers.contains(selected) ? selected : numbers.first,
                decoration: const InputDecoration(labelText: 'Survey'),
                items: [
                  for (final k in numbers)
                    DropdownMenuItem(
                      value: k,
                      child: Text('Survey $k · ${str(rounds[k]!['status']) == 'live' ? 'collecting' : 'completed'} · recipe v${intOf(rounds[k]!['recipe_version'])}'),
                    ),
                ],
                onChanged: (v) => setState(() => _round = v),
              ),
            if (data['updating'] == true) ...[
              const SizedBox(height: 10),
              const Align(alignment: Alignment.centerLeft, child: Pill('Updating with the latest responses…')),
            ],
            if (report.isEmpty)
              const EmptyNote(
                icon: Icons.assessment_outlined,
                title: 'No analyzed responses yet',
                text: 'The report appears after the first analyzed response of this survey.',
              )
            else ...[
              const SizedBox(height: 12),
              KpiGrid(cards: [
                KpiCard(label: 'Consumers', icon: Icons.groups_outlined, tone: KpiTone.teal, value: '$n'),
                KpiCard(
                  label: 'Average rating',
                  icon: Icons.star_outline_rounded,
                  tone: KpiTone.act,
                  valueWidget: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                    Text(ratingText(stats['overall_rating']),
                        style: TextStyle(fontFamily: kDisplayFont, fontSize: 26, fontWeight: FontWeight.w800, height: 1.05, color: AppColors.ink)),
                    const SizedBox(height: 4),
                    RatingStars(rating: (numOrNull(stats['overall_rating']) ?? 0).toDouble(), size: 13),
                  ]),
                ),
              ]),
              const SizedBox(height: 8),
              Panel(
                child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                  Text('Consumer sentiment', style: TextStyle(fontSize: 12, color: AppColors.muted)),
                  const SizedBox(height: 8),
                  ToneBar(counts: asJson(stats['sentiment_distribution'])),
                  const SizedBox(height: 8),
                  Wrap(spacing: 12, children: [
                    for (final t in ['positive', 'mixed', 'negative'])
                      Text('${intOf(asJson(stats['sentiment_distribution'])[t])} $t', style: TextStyle(fontSize: 12.5, color: AppColors.ink2)),
                  ]),
                ]),
              ),
              if (str(nv['conclusion']).isNotEmpty) ...[
                const SizedBox(height: 10),
                Panel(
                  color: AppColors.hero,
                  child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                    Text('Client summary', style: TextStyle(fontSize: 12, color: Colors.white.withValues(alpha: 0.75))),
                    const SizedBox(height: 4),
                    Text(str(nv['conclusion']), style: const TextStyle(color: Colors.white, fontWeight: FontWeight.w700, height: 1.4)),
                  ]),
                ),
              ],
              _section('Rating distribution'),
              Panel(
                child: Column(children: [
                  for (final s in ['5', '4', '3', '2', '1'])
                    _CountBar(label: '$s ★', count: intOf(asJson(stats['rating_distribution'])[s]), total: n),
                ]),
              ),
              ..._narrative(nv, n),
              ..._questions(asJsonList(stats['questions'])),
            ],
          ],
        );
      },
    );
  }

  List<Widget> _narrative(Json nv, int n) {
    final out = <Widget>[];
    void points(String title, String key, IconData icon, Color color) {
      final items = asJsonList(nv[key]).where((m) => str(m['point']).isNotEmpty).toList();
      if (items.isEmpty) return;
      out.add(_section(title));
      out.add(Panel(
        child: Column(children: [
          for (final m in items) _bullet(str(m['point']), icon: icon, color: color, trailing: '${intOf(m['customers'])} of $n'),
        ]),
      ));
    }

    void strings(String title, String key) {
      final items = (nv[key] is List ? (nv[key] as List) : const []).map((e) => '$e').where((e) => e.trim().isNotEmpty).toList();
      if (items.isEmpty) return;
      out.add(_section(title));
      out.add(Panel(child: Column(children: [for (final s in items) _bullet(s)])));
    }

    points('Common positive points', 'common_positives', Icons.check_rounded, AppColors.pos);
    points('Recurring issues', 'recurring_issues', Icons.priority_high_rounded, AppColors.neg);
    strings('Recommended actions', 'recommended_actions');
    strings('Suggestions from customers', 'suggestions');
    return out;
  }

  List<Widget> _questions(List<Json> qs) {
    if (qs.isEmpty) return const [];
    return [
      _section('Survey answers'),
      for (final q in qs) ...[
        Panel(
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Text(str(q['question']), style: TextStyle(fontWeight: FontWeight.w700, color: AppColors.ink)),
            const SizedBox(height: 6),
            if (numOrNull(q['average']) != null)
              Text('Average ${ratingText(q['average'])} / 5', style: TextStyle(fontSize: 12.5, color: AppColors.ink2)),
            if (q['distribution'] is Map) ...[
              const SizedBox(height: 4),
              for (final e in asJson(q['distribution']).entries)
                _CountBar(
                  label: str(q['type']) == 'rating' ? '${e.key} ★' : e.key,
                  count: intOf(e.value),
                  total: intOf(q['answered']),
                ),
            ],
            for (final t in (q['text_answers'] is List ? (q['text_answers'] as List) : const []).take(5))
              _bullet('“$t”', icon: Icons.format_quote_rounded, color: AppColors.muted),
            const SizedBox(height: 4),
            Text('${intOf(q['answered'])} answered', style: TextStyle(fontSize: 11.5, color: AppColors.muted)),
          ]),
        ),
        const SizedBox(height: 8),
      ],
    ];
  }
}

// ---------------------------------------------------------------- recipe

class _RecipeTab extends StatelessWidget {
  const _RecipeTab({required this.productId, required this.onSaved, this.snapshot});

  final int productId;
  final VoidCallback onSaved;
  /// An earlier survey's recipe: the same view, read-only (no edit buttons).
  final Json? snapshot;

  @override
  Widget build(BuildContext context) {
    final readOnly = snapshot != null;
    return Loaded<Json>(
      load: () => readOnly ? Future.value(snapshot!) : OrgApi.recipe(productId),
      builder: (context, recipe, reload) {
        final ingredients = asJsonList(recipe['ingredients']);
        Future<void> edit() async {
          final saved = await Navigator.of(context).push<bool>(
            MaterialPageRoute(builder: (_) => RecipeEditorPage(productId: productId, recipe: recipe)),
          );
          if (saved == true) {
            await reload();
            onSaved();
          }
        }

        return ListView(
          padding: const EdgeInsets.fromLTRB(16, 12, 16, 28),
          children: [
            Text(
              readOnly
                  ? 'The recipe this survey tested (read-only).'
                  : 'The report\'s recommendations refer to these ingredients. Saving a change creates a new version and tells the research team.',
              style: TextStyle(color: AppColors.muted, height: 1.4),
            ),
            const SizedBox(height: 12),
            if (ingredients.isEmpty)
              Panel(
                child: Column(children: [
                  const EmptyNote(icon: Icons.menu_book_outlined, title: 'No recipe yet', text: 'Add how the product is made.'),
                  if (!readOnly) FilledButton.icon(onPressed: edit, icon: const Icon(Icons.add), label: const Text('Add recipe')),
                ]),
              )
            else ...[
              Panel(
                child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                  Row(children: [
                    Expanded(child: Text('Batch size', style: TextStyle(fontWeight: FontWeight.w800, color: AppColors.ink))),
                    if (!readOnly) TextButton(onPressed: edit, child: const Text('Edit')),
                  ]),
                  Text(str(recipe['batch_size'], 'Not set'), style: TextStyle(color: AppColors.ink2)),
                ]),
              ),
              const SizedBox(height: 8),
              Panel(
                child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                  Text('Ingredients · ${ingredients.length}', style: TextStyle(fontWeight: FontWeight.w800, color: AppColors.ink)),
                  const SizedBox(height: 6),
                  for (final i in ingredients)
                    Padding(
                      padding: const EdgeInsets.symmetric(vertical: 5),
                      child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
                        Expanded(
                          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                            Text(str(i['name']), style: TextStyle(color: AppColors.ink, fontWeight: FontWeight.w600)),
                            if (str(i['notes']).isNotEmpty) Text(str(i['notes']), style: TextStyle(fontSize: 12, color: AppColors.muted)),
                          ]),
                        ),
                        Text('${str(i['quantity'])} ${str(i['unit'])}'.trim(), style: TextStyle(fontWeight: FontWeight.w800, color: AppColors.ink)),
                      ]),
                    ),
                ]),
              ),
              const SizedBox(height: 8),
              // Costing (Cost per KG x Usage %), standards, attachments, earlier steps (read-only)
              if (!readOnly) RecipeExtrasView(productId: productId, canEdit: true)
              // An earlier version: its own Cost per KG and Cost in Use.
              else RecipeExtrasView(productId: productId, data: {'costing': snapshot!['_costing']}, costingOnly: true),
              const SizedBox(height: 8),
              if (str(recipe['process']).isNotEmpty) ...[
                const SizedBox(height: 8),
                Panel(
                  child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                    Text('Production process', style: TextStyle(fontWeight: FontWeight.w800, color: AppColors.ink)),
                    const SizedBox(height: 4),
                    Text(str(recipe['process']), style: TextStyle(color: AppColors.ink2, height: 1.45)),
                  ]),
                ),
              ],
              if (str(recipe['notes']).isNotEmpty) ...[
                const SizedBox(height: 8),
                Panel(
                  child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                    Text('Notes', style: TextStyle(fontWeight: FontWeight.w800, color: AppColors.ink)),
                    const SizedBox(height: 4),
                    Text(str(recipe['notes']), style: TextStyle(color: AppColors.ink2)),
                  ]),
                ),
              ],
              const SizedBox(height: 14),
              if (!readOnly) FilledButton.icon(onPressed: edit, icon: const Icon(Icons.edit_outlined), label: const Text('Edit recipe')),
            ],
          ],
        );
      },
    );
  }
}

/// Full-screen recipe editor; saving creates a new recipe version on the server.
// ---------------------------------------------------------------- batch tolerance
// The ingredients weigh at least the batch size and at most a little more: each 100 kg band has
// its own % (the server checks the same: services/recipe_tools.batch_check).
const _toleranceBands = [(100, 2.0), (200, 1.8), (300, 1.6), (400, 1.5), (500, 1.4), (600, 1.3), (700, 1.2), (800, 1.1), (900, 1.0), (1000, 0.9)];
const _toleranceAbove = 0.8;
const _weightToKg = {'kg': 1.0, 'kgs': 1.0, 'g': 0.001, 'gm': 0.001, 'gms': 0.001, 'gram': 0.001, 'grams': 0.001, 'mg': 0.000001,
  'l': 1.0, 'ltr': 1.0, 'litre': 1.0, 'liter': 1.0, 'ml': 0.001};
const _batchUnits = {..._weightToKg, 'kilo': 1.0, 'kilos': 1.0, 'kilogram': 1.0, 'kilograms': 1.0, 't': 1000.0, 'ton': 1000.0,
  'tons': 1000.0, 'tonne': 1000.0, 'tonnes': 1000.0, 'quintal': 100.0, 'quintals': 100.0};

/// 102 -> "102", 304.8 -> "304.8" (at most 3 decimals).
String _kg(double v) => (v * 1000).round() % 1000 == 0 ? '${v.round()}' : '${(v * 1000).round() / 1000}';

/// (checked, ok, message) for a batch size text and (name, quantity, unit) rows.
({bool checked, bool ok, String message}) batchCheck(String batchText, Iterable<(String, String, String)> rows) {
  double? batch;
  for (final m in RegExp(r'(\d+(?:\.\d+)?)\s*([a-z]+)').allMatches(batchText.toLowerCase().replaceAll(',', ''))) {
    final f = _batchUnits[m.group(2)];
    if (f != null) {
      batch = double.parse(m.group(1)!) * f;
      break;
    }
  }
  var total = 0.0;
  var weighed = false;
  for (final (name, qty, unit) in rows) {
    final q = double.tryParse(RegExp(r'-?\d+(?:\.\d+)?').firstMatch(qty.replaceAll(',', ''))?.group(0) ?? '');
    final f = _weightToKg[unit.trim().toLowerCase()];
    if (name.trim().isNotEmpty && q != null && f != null) {
      total += q * f;
      weighed = true;
    }
  }
  final size = batch;
  if (size == null || size <= 0 || !weighed) return (checked: false, ok: true, message: '');
  final grams = (total * 1000).round(); // whole grams: no floating-point dust (0.1 + 0.2)
  final kg = grams / 1000;
  (int, double)? band;
  for (final b in _toleranceBands) {
    if (size <= b.$1) {
      band = b;
      break;
    }
  }
  final pct = band?.$2 ?? _toleranceAbove;
  final allowed = (size * (1 + pct / 100) * 1000).round() / 1000;
  final short = ((size - kg) * 1000).round() / 1000;
  final over = ((kg - allowed) * 1000).round() / 1000;
  final bandText = band != null ? 'batches up to ${band.$1} kg' : 'batches over ${_toleranceBands.last.$1} kg';
  if (short <= 0 && over <= 0) {
    return (checked: true, ok: true, message: 'Ingredients total ${_kg(kg)} kg: within the ${_kg(size)} kg batch (up to ${_kg(allowed)} kg allowed, +$pct%).');
  }
  return (
    checked: true,
    ok: false,
    message: short > 0
        ? 'Ingredients total ${_kg(kg)} kg, ${_kg(short)} kg less than the ${_kg(size)} kg batch. '
            'Add ${_kg(short)} kg (up to ${_kg(allowed)} kg is allowed), or change the batch size.'
        : 'Ingredients total ${_kg(kg)} kg, more than the ${_kg(size)} kg batch allows: up to ${_kg(allowed)} kg '
            '(+$pct% tolerance for $bandText). Reduce the quantities or change the batch size.',
  );
}

class RecipeEditorPage extends StatefulWidget {
  const RecipeEditorPage({super.key, required this.productId, required this.recipe});

  final int productId;
  final Json recipe;

  @override
  State<RecipeEditorPage> createState() => _RecipeEditorPageState();
}

class _RecipeEditorPageState extends State<RecipeEditorPage> {
  late final TextEditingController _batch = TextEditingController(text: str(widget.recipe['batch_size']));
  late final TextEditingController _notes = TextEditingController(text: str(widget.recipe['notes']));
  late final TextEditingController _process = TextEditingController(text: str(widget.recipe['process']));
  final List<List<TextEditingController>> _ingredients = []; // name, quantity, unit, notes, cost per kg
  // Rows removed while editing: disposed when the editor closes (their fields may
  // still be on screen during the frame that removes them).
  final List<TextEditingController> _removed = [];
  bool _saving = false;
  String? _error;

  @override
  void initState() {
    super.initState();
    for (final i in asJsonList(widget.recipe['ingredients'])) {
      _ingredients.add([for (final k in ['name', 'quantity', 'unit', 'notes', 'cost_per_kg']) TextEditingController(text: str(i[k]))]);
    }
    if (_ingredients.isEmpty) _addIngredient();
  }

  @override
  void dispose() {
    for (final c in [_batch, _notes, _process, ..._ingredients.expand((r) => r), ..._removed]) {
      c.dispose();
    }
    super.dispose();
  }

  void _addIngredient() => setState(() => _ingredients.add(List.generate(5, (_) => TextEditingController())));

  ({bool checked, bool ok, String message}) _check() =>
      batchCheck(_batch.text, [for (final r in _ingredients) (r[0].text, r[1].text, r[2].text)]);

  Future<void> _save() async {
    FocusManager.instance.primaryFocus?.unfocus(); // Save works with the keyboard open
    final check = _check();
    if (!check.ok) {
      setState(() => _error = check.message);
      return;
    }
    String? t(TextEditingController c) => c.text.trim().isEmpty ? null : c.text.trim();
    final body = <String, dynamic>{
      'batch_size': t(_batch),
      'notes': t(_notes),
      'process': t(_process),
      'ingredients': [
        for (final r in _ingredients)
          if (r[0].text.trim().isNotEmpty)
            {
              'name': r[0].text.trim(),
              'quantity': t(r[1]),
              'unit': t(r[2]),
              'notes': t(r[3]),
              'cost_per_kg': double.tryParse(r[4].text.trim().replaceAll(',', '')),
            },
      ],
      'steps': [], // no Steps in new versions (earlier versions keep theirs, read-only)
    };
    setState(() {
      _saving = true;
      _error = null;
    });
    try {
      await OrgApi.saveRecipe(widget.productId, body);
      if (!mounted) return;
      showMessage(context, 'Recipe saved as a new version. The research team has been told.');
      Navigator.of(context).pop(true);
    } catch (e) {
      if (mounted) setState(() {
        _saving = false;
        _error = '$e';
      });
    }
  }

  Widget _field(TextEditingController c, String label, {int lines = 1, String? hint, TextInputType? keyboard}) => Padding(
        padding: const EdgeInsets.only(bottom: 8),
        child: TextField(
            controller: c,
            minLines: lines,
            maxLines: lines == 1 ? 1 : 4,
            keyboardType: keyboard,
            decoration: InputDecoration(labelText: label, hintText: hint)),
      );

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: AppColors.bg,
      appBar: AppBar(title: const Text('Edit recipe')),
      // The Save bar is part of the body (not bottomNavigationBar), so it stays above
      // the keyboard: the body shrinks to the space the keyboard leaves.
      body: Column(children: [
        Expanded(
          child: ListView(
        keyboardDismissBehavior: ScrollViewKeyboardDismissBehavior.onDrag,
        padding: const EdgeInsets.fromLTRB(16, 8, 16, 24),
        children: [
          _field(_batch, 'Batch size', hint: '100 kg batch = about 1000 servings of 100 g'),
          _section('Ingredients'),
          for (var k = 0; k < _ingredients.length; k++)
            Panel(
              padding: const EdgeInsets.fromLTRB(12, 12, 4, 4),
              child: Column(children: [
                Row(children: [
                  Expanded(child: _field(_ingredients[k][0], 'Ingredient ${k + 1}')),
                  IconButton(
                    tooltip: 'Remove ingredient',
                    onPressed: () => setState(() => _removed.addAll(_ingredients.removeAt(k))),
                    icon: Icon(Icons.close, color: AppColors.muted),
                  ),
                ]),
                Padding(
                  padding: const EdgeInsets.only(right: 8),
                  child: Row(children: [
                    Expanded(child: _field(_ingredients[k][1], 'Quantity')),
                    const SizedBox(width: 8),
                    Expanded(child: _field(_ingredients[k][2], 'Unit', hint: 'kg, g, ml, %')),
                    const SizedBox(width: 8),
                    Expanded(child: _field(_ingredients[k][4], 'Cost per KG (₹)', hint: '45', keyboard: const TextInputType.numberWithOptions(decimal: true))),
                  ]),
                ),
                Padding(padding: const EdgeInsets.only(right: 8), child: _field(_ingredients[k][3], 'Why it\'s there (optional)')),
              ]),
            ),
          TextButton.icon(onPressed: _addIngredient, icon: const Icon(Icons.add), label: const Text('Add ingredient')),
          _section('Production process'),
          _field(_process, 'Write the process step by step (optional; files can be attached too)', lines: 6),
          _section('Notes'),
          _field(_notes, 'Storage, shelf life, packaging (optional)', lines: 3),
          if (_error != null) Text(_error!, style: TextStyle(color: AppColors.neg)),
        ],
      ),
        ),
        // Ingredients total against the batch size, updated while typing
        AnimatedBuilder(
          animation: Listenable.merge([_batch, for (final r in _ingredients) ...r.take(3)]),
          builder: (context, _) {
            final check = _check();
            if (!check.checked) return const SizedBox.shrink();
            return Container(
              width: double.infinity,
              margin: const EdgeInsets.fromLTRB(16, 6, 16, 0),
              padding: const EdgeInsets.all(10),
              decoration: BoxDecoration(
                color: check.ok ? AppColors.posSoft : AppColors.negSoft,
                borderRadius: BorderRadius.circular(10),
              ),
              child: Text(check.message,
                  style: TextStyle(fontSize: 13, height: 1.35, color: check.ok ? AppColors.pos : AppColors.neg,
                      fontWeight: check.ok ? FontWeight.w500 : FontWeight.w700)),
            );
          },
        ),
        SafeArea(
          top: false,
          bottom: MediaQuery.viewInsetsOf(context).bottom == 0, // keyboard open: sit right on it
          minimum: const EdgeInsets.fromLTRB(16, 8, 16, 12),
          child: SizedBox(
            width: double.infinity,
            child: FilledButton(
              onPressed: _saving ? null : _save,
              child: Text(_saving ? 'Saving…' : 'Save as a new version'),
            ),
          ),
        ),
      ]),
    );
  }
}

// ---------------------------------------------------------------- survey questions

const _typeLabels = {
  'rating': 'Rating 1–5',
  'yes_no': 'Yes / No',
  'single_choice': 'Single choice',
  'multiple_choice': 'Multiple choice',
  'preference': 'Preference (Sample A vs B)',
  'text': 'Text answer',
};
const _optionTypes = {'single_choice', 'multiple_choice', 'preference'};

class ProductQuestionsTab extends StatelessWidget {
  const ProductQuestionsTab({required this.productId, this.snapshot});

  final int productId;
  /// The questions an earlier survey asked: the same view, read-only.
  final List<Json>? snapshot;

  @override
  Widget build(BuildContext context) {
    final readOnly = snapshot != null;
    return Loaded<List<Json>>(
      load: () => readOnly ? Future.value(snapshot!) : OrgApi.questions(productId),
      builder: (context, all, reload) {
        final qs = readOnly
            ? all
            : (all.where((q) => q['is_active'] != false).toList()..sort((a, b) => intOf(a['position']).compareTo(intOf(b['position']))));
        final n = qs.length;
        final (tone, status) = n < 5
            ? ('warn', '$n of 5–10 questions · add ${5 - n} more before it can be surveyed')
            : n > 10
                ? ('warn', '$n questions · at most 10 can be asked')
                : ('ok', '$n of 5–10 questions · ready for surveys');

        Future<void> run(Future<void> Function() action, String done) async {
          try {
            await action();
            await reload();
            if (context.mounted) showMessage(context, done);
          } catch (e) {
            if (context.mounted) showMessage(context, '$e', error: true);
          }
        }

        Future<void> move(int index, int by) => run(() async {
              final ids = qs.map((q) => intOf(q['id'])).toList();
              final id = ids.removeAt(index);
              ids.insert(index + by, id);
              await OrgApi.reorderQuestions(productId, ids);
            }, 'Order saved.');

        Future<void> edit([Json? q]) async {
          final saved = await showModalBottomSheet<bool>(
            context: context,
            isScrollControlled: true,
            useSafeArea: true,
            builder: (_) => QuestionEditorSheet(productId: productId, question: q),
          );
          if (saved == true) {
            await reload();
            if (context.mounted) showMessage(context, q == null ? 'Question added. Survey persons see it straight away.' : 'Question saved.');
          }
        }

        return ListView(
          padding: const EdgeInsets.fromLTRB(16, 12, 16, 28),
          children: [
            Text(
              readOnly
                  ? 'The questions this survey asked, in this order (read-only).'
                  : 'Survey persons ask these questions, in this order. Keep them the same between surveys you want to compare.',
              style: TextStyle(color: AppColors.muted, height: 1.4),
            ),
            const SizedBox(height: 10),
            if (!readOnly) Align(alignment: Alignment.centerLeft, child: Pill(status, tone: tone)),
            const SizedBox(height: 10),
            for (var k = 0; k < qs.length; k++) ...[
              Panel(
                padding: const EdgeInsets.fromLTRB(12, 10, 4, 10),
                child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
                  CircleAvatar(
                    radius: 12,
                    backgroundColor: AppColors.brandSoft,
                    child: Text('${k + 1}', style: TextStyle(fontSize: 11.5, fontWeight: FontWeight.w800, color: AppColors.brand)),
                  ),
                  const SizedBox(width: 10),
                  Expanded(
                    child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                      Text(str(qs[k]['text']), style: TextStyle(fontWeight: FontWeight.w700, color: AppColors.ink)),
                      const SizedBox(height: 3),
                      Text(
                        [
                          _typeLabels[str(qs[k]['question_type'])] ?? str(qs[k]['question_type']),
                          if (qs[k]['required'] == true) 'required',
                          if (qs[k]['options'] is List) (qs[k]['options'] as List).join(' / '),
                          if (readOnly && qs[k]['still_asked'] == false) 'no longer asked',
                        ].join(' · '),
                        style: TextStyle(fontSize: 12, color: AppColors.muted),
                      ),
                    ]),
                  ),
                  if (!readOnly) PopupMenuButton<String>(
                    tooltip: 'Question actions',
                    onSelected: (a) async {
                      if (a == 'edit') await edit(qs[k]);
                      if (a == 'up') await move(k, -1);
                      if (a == 'down') await move(k, 1);
                      if (a == 'remove') {
                        final ok = await confirmAction(
                          context,
                          title: 'Remove this question?',
                          message: 'New surveys won\'t ask it. Answers already collected are kept.',
                          confirmLabel: 'Remove',
                          cancelLabel: 'Keep',
                        );
                        if (ok) await run(() => OrgApi.removeQuestion(intOf(qs[k]['id'])), 'Question removed.');
                      }
                    },
                    itemBuilder: (_) => [
                      const PopupMenuItem(value: 'edit', child: Text('Edit')),
                      if (k > 0) const PopupMenuItem(value: 'up', child: Text('Move up')),
                      if (k < qs.length - 1) const PopupMenuItem(value: 'down', child: Text('Move down')),
                      const PopupMenuItem(value: 'remove', child: Text('Remove')),
                    ],
                  ),
                ]),
              ),
              const SizedBox(height: 8),
            ],
            const SizedBox(height: 6),
            FilledButton.icon(onPressed: () => edit(), icon: const Icon(Icons.add), label: const Text('Add question')),
          ],
        );
      },
    );
  }
}

class QuestionEditorSheet extends StatefulWidget {
  const QuestionEditorSheet({super.key, required this.productId, this.question});

  final int productId;
  final Json? question;

  @override
  State<QuestionEditorSheet> createState() => _QuestionEditorSheetState();
}

class _QuestionEditorSheetState extends State<QuestionEditorSheet> {
  late final TextEditingController _text = TextEditingController(text: str(widget.question?['text']));
  late final TextEditingController _options = TextEditingController(
    text: widget.question?['options'] is List ? (widget.question!['options'] as List).join('\n') : '',
  );
  late String _type = str(widget.question?['question_type'], 'rating');
  late bool _required = widget.question?['required'] != false;
  bool _saving = false;
  String? _error;

  @override
  void dispose() {
    _text.dispose();
    _options.dispose();
    super.dispose();
  }

  Future<void> _save() async {
    final options = _options.text.split('\n').map((s) => s.trim()).where((s) => s.isNotEmpty).toList();
    if (_text.text.trim().length < 5) {
      setState(() => _error = 'Write the question (at least 5 characters).');
      return;
    }
    if ((_type == 'single_choice' || _type == 'multiple_choice') && options.length < 2) {
      setState(() => _error = 'Add at least two answer options, one per line.');
      return;
    }
    final body = <String, dynamic>{
      'text': _text.text.trim(),
      'question_type': _type,
      'required': _required,
      'options': _optionTypes.contains(_type) ? (_type == 'preference' && options.isEmpty ? null : options) : null,
    };
    setState(() {
      _saving = true;
      _error = null;
    });
    try {
      if (widget.question == null) {
        await OrgApi.addQuestion(widget.productId, body);
      } else {
        await OrgApi.updateQuestion(intOf(widget.question!['id']), body);
      }
      if (mounted) Navigator.of(context).pop(true);
    } catch (e) {
      if (mounted) setState(() {
        _saving = false;
        _error = '$e';
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: EdgeInsets.fromLTRB(20, 8, 20, 16 + MediaQuery.of(context).viewInsets.bottom),
      child: SingleChildScrollView(
        child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, mainAxisSize: MainAxisSize.min, children: [
          Text(widget.question == null ? 'Add a question' : 'Edit question',
              style: TextStyle(fontFamily: kDisplayFont, fontSize: 18, fontWeight: FontWeight.w800, color: AppColors.ink)),
          const SizedBox(height: 14),
          TextField(controller: _text, minLines: 2, maxLines: 4, decoration: const InputDecoration(labelText: 'Question')),
          const SizedBox(height: 12),
          SearchDropdownField<String>(
            isExpanded: true,
            value: _type,
            decoration: const InputDecoration(labelText: 'Answer type'),
            items: [for (final e in _typeLabels.entries) DropdownMenuItem(value: e.key, child: Text(e.value))],
            onChanged: (v) => setState(() => _type = v ?? 'rating'),
          ),
          if (_optionTypes.contains(_type)) ...[
            const SizedBox(height: 12),
            TextField(
              controller: _options,
              minLines: _type == 'preference' ? 2 : 3,
              maxLines: 6,
              decoration: InputDecoration(
                labelText: _type == 'preference' ? 'The two samples (empty = Sample A, Sample B)' : 'Answer options',
                hintText: _type == 'preference' ? 'Sample A\nSample B' : 'One per line',
              ),
            ),
          ],
          SwitchListTile(
            contentPadding: EdgeInsets.zero,
            value: _required,
            onChanged: (v) => setState(() => _required = v),
            title: const Text('Required'),
          ),
          if (_error != null) Padding(padding: const EdgeInsets.only(bottom: 8), child: Text(_error!, style: TextStyle(color: AppColors.neg))),
          FilledButton(onPressed: _saving ? null : _save, child: Text(_saving ? 'Saving…' : (widget.question == null ? 'Add question' : 'Save'))),
          TextButton(onPressed: () => Navigator.of(context).pop(false), child: const Text('Cancel')),
        ]),
      ),
    );
  }
}

// ---------------------------------------------------------------- rounds

class _RoundsTab extends StatelessWidget {
  const _RoundsTab({required this.productId, required this.detail, required this.onChanged});

  final int productId;
  final Json detail;
  final Future<void> Function() onChanged;

  @override
  Widget build(BuildContext context) {
    final live = asJson(detail['live_round']);
    final rounds = asJsonList(detail['rounds'])..sort((a, b) => intOf(b['number']).compareTo(intOf(a['number'])));
    final past = rounds.where((r) => str(r['status']) != 'live').toList();
    final canStart = detail['can_start_round'] == true;

    // Surveys start from the project of the recipe version, where its readiness is checked
    Future<void> start() async {
      await openProjectFor(context, productId, intOf(detail['latest_version']));
      await onChanged();
    }

    Future<void> end() async {
      final n = intOf(live['number']);
      final ok = await confirmAction(
        context,
        title: 'End Survey $n?',
        message: 'No new responses will join it. Its report keeps the ${intOf(live['done'])} responses collected so far.',
        confirmLabel: 'End survey',
        cancelLabel: 'Keep collecting',
      );
      if (!ok) return;
      try {
        await OrgApi.endRound(productId, n);
        await onChanged();
        if (context.mounted) showMessage(context, 'Survey $n ended.');
      } catch (e) {
        if (context.mounted) showMessage(context, '$e', error: true);
      }
    }

    // A survey started by mistake: the latest one, while nobody has answered it.
    final latestNumber = rounds.isEmpty ? null : intOf(rounds.first['number']);
    Future<void> remove(Json r) async {
      final n = intOf(r['number']);
      final ok = await confirmAction(
        context,
        title: 'Delete Survey $n?',
        message: 'Survey $n has no responses yet. It is removed with its action plans and team assignments; '
            'the recipe and its versions stay.',
        confirmLabel: 'Delete',
        cancelLabel: 'Keep it',
      );
      if (!ok) return;
      try {
        await OrgApi.deleteRound(productId, n);
        await onChanged();
        if (context.mounted) showMessage(context, 'Survey $n was deleted.');
      } catch (e) {
        if (context.mounted) showMessage(context, '$e', error: true);
      }
    }

    Widget card(Json r, {bool isLive = false}) {
      final done = intOf(r['done']), target = intOf(r['target']);
      return Panel(
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Row(children: [
            Expanded(child: Text('Survey ${intOf(r['number'])}', style: TextStyle(fontWeight: FontWeight.w800, fontSize: 15.5, color: AppColors.ink))),
            Pill(isLive ? 'Collecting' : (str(r['closed_reason']) == 'ended' ? 'Ended' : 'Complete'), tone: isLive ? 'ok' : 'muted'),
          ]),
          const SizedBox(height: 4),
          Text(
            [
              'Recipe v${intOf(r['recipe_version'])}',
              if (asJson(r['project'])['code'] != null) '${asJson(r['project'])['code']}',
              '$done submitted of $target · ${(target - done).clamp(0, target)} remaining',
              if (str(r['started_at']).isNotEmpty) 'from ${str(r['started_at']).substring(0, 10)}',
              if (numOrNull(r['rating']) != null) '${ratingText(r['rating'])} ★',
              if (str(r['note']).isNotEmpty) '“${str(r['note'])}”',
            ].join(' · '),
            style: TextStyle(fontSize: 12.5, color: AppColors.muted),
          ),
          const SizedBox(height: 8),
          ClipRRect(
            borderRadius: BorderRadius.circular(4),
            child: LinearProgressIndicator(
              value: target <= 0 ? 0 : (done / target).clamp(0, 1).toDouble(),
              minHeight: 7,
              backgroundColor: AppColors.line,
              color: AppColors.brand,
            ),
          ),
          if (isLive || (intOf(r['number']) == latestNumber && done == 0)) ...[
            const SizedBox(height: 10),
            Wrap(spacing: 8, runSpacing: 8, children: [
              if (isLive) OutlinedButton(onPressed: end, child: const Text('End survey')),
              if (intOf(r['number']) == latestNumber && done == 0)
                OutlinedButton(
                  onPressed: () => remove(r),
                  style: OutlinedButton.styleFrom(foregroundColor: AppColors.neg),
                  child: const Text('Delete'),
                ),
            ]),
          ],
        ]),
      );
    }

    return RefreshIndicator(
      onRefresh: onChanged,
      child: ListView(
        padding: const EdgeInsets.fromLTRB(16, 12, 16, 28),
        children: [
          Text(
            'A survey is one batch of consumer responses for one recipe version. Start a new survey after changing the recipe, then compare.',
            style: TextStyle(color: AppColors.muted, height: 1.4),
          ),
          const SizedBox(height: 12),
          if (live.isNotEmpty) ...[card(live, isLive: true), const SizedBox(height: 8)],
          for (final r in past) ...[card(r), const SizedBox(height: 8)],
          if (rounds.isEmpty) const EmptyNote(icon: Icons.flag_outlined, title: 'No surveys yet', text: 'Start the first survey to collect consumer feedback.'),
          const SizedBox(height: 6),
          if (live.isEmpty && asJsonList(detail['standards_failed']).isNotEmpty) ...[
            StandardsBlocked(failures: asJsonList(detail['standards_failed']), version: intOf(detail['latest_version'])),
            const SizedBox(height: 8),
          ],
          FilledButton.icon(onPressed: canStart ? start : null, icon: const Icon(Icons.play_arrow_rounded), label: const Text('Open project to start')),
          if (!canStart && live.isNotEmpty)
            Padding(
              padding: const EdgeInsets.only(top: 6),
              child: Text('Survey ${intOf(live['number'])} is collecting. End it to start a new one.',
                  textAlign: TextAlign.center, style: TextStyle(fontSize: 12.5, color: AppColors.muted)),
            ),
        ],
      ),
    );
  }
}

// ---------------------------------------------------------------- compare

/// Two rounds side by side with the recipe changes (organization's Compare tab and
/// the admin's "Compare rounds"; /org/products/{id}/compare is open to admins too).
class RoundCompareView extends StatefulWidget {
  const RoundCompareView({super.key, required this.productId});

  final int productId;

  @override
  State<RoundCompareView> createState() => _RoundCompareViewState();
}

class _RoundCompareViewState extends State<RoundCompareView> {
  // Separate surveys (Survey 1 and Survey 2 may test the same recipe); never a survey with itself.
  List<int>? _picked; // null: the two latest
  int? get _a => _picked != null && _picked!.length >= 2 ? _picked![0] : null;
  int? get _b => _picked != null && _picked!.length >= 2 ? _picked![1] : null;

  @override
  Widget build(BuildContext context) {
    return Loaded<Json>(
      key: ValueKey('compare-${_picked?.join(',')}'),
      load: () => _picked != null && _picked!.length < 2
          ? OrgApi.compare(widget.productId).then((d) => {...d, 'need_two': true})
          : OrgApi.compare(widget.productId, a: _a, b: _b, surveys: _picked),
      builder: (context, data, reload) {
        if (data['enough_rounds'] != true) {
          return ListView(children: const [
            EmptyNote(
              icon: Icons.compare_arrows,
              title: 'Compare needs two surveys',
              text: 'Start a second survey (for example after changing the recipe) to compare them side by side.',
            ),
          ]);
        }
        final rounds = asJsonList(data['rounds']);
        final a = asJson(data['a']), b = asJson(data['b']);
        final picked = _picked ?? [intOf(a['number']), intOf(b['number'])];
        final chips = Wrap(spacing: 6, runSpacing: 6, children: [
          for (final r in rounds)
            FilterChip(
              label: Text('Survey ${intOf(r['number'])} · v${intOf(r['recipe_version'])}'
                  '${asJson(r['project'])['code'] != null ? ' · ${asJson(r['project'])['code']}' : ''}'),
              selected: picked.contains(intOf(r['number'])),
              onSelected: (on) => setState(() {
                final next = [...picked];
                on ? next.add(intOf(r['number'])) : next.remove(intOf(r['number']));
                next.sort();
                _picked = next;
              }),
            ),
        ]);
        if (data['need_two'] == true) {
          return ListView(padding: const EdgeInsets.all(16), children: [
            chips,
            const SizedBox(height: 12),
            Text('Choose at least two different surveys.', style: TextStyle(color: AppColors.muted)),
          ]);
        }
        final many = asJsonList(data['surveys']);
        if (picked.length > 2 && many.isNotEmpty) {
          String v(dynamic x, [String suffix = '']) => x == null ? '—' : '${numOrNull(x) is double ? (numOrNull(x) as double).toStringAsFixed(1) : x}$suffix';
          return ListView(padding: const EdgeInsets.fromLTRB(16, 12, 16, 28), children: [
            chips,
            const SizedBox(height: 12),
            for (final s in many)
              Padding(
                padding: const EdgeInsets.only(bottom: 8),
                child: Panel(
                  child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                    Text('Survey ${intOf(s['number'])} · Recipe v${intOf(s['recipe_version'])}',
                        style: TextStyle(fontWeight: FontWeight.w800, color: AppColors.ink)),
                    const SizedBox(height: 4),
                    Text('Submitted responses ${v(asJson(s['numbers'])['consumers'])} · Rating ${v(asJson(s['numbers'])['rating'])} · '
                        'Positive ${v(asJson(s['numbers'])['positive_pct'], '%')} · Negative ${v(asJson(s['numbers'])['negative_pct'], '%')}',
                        style: TextStyle(color: AppColors.ink2)),
                  ]),
                ),
              ),
            Text("Each survey keeps its own responses; surveys of the same recipe aren't merged.", style: TextStyle(fontSize: 12.5, color: AppColors.muted)),
          ]);
        }
        final na = asJson(a['numbers']), nb = asJson(b['numbers']);
        final changes = (data['recipe_changes'] is List ? data['recipe_changes'] as List : const []).map((e) => '$e').toList();

        TableRow row(String label, dynamic x, dynamic y, {String suffix = '', bool higherIsBetter = true}) {
          final dx = numOrNull(x), dy = numOrNull(y);
          final better = dx != null && dy != null && dy != dx && ((dy > dx) == higherIsBetter);
          final worse = dx != null && dy != null && dy != dx && !better;
          String f(num? v) => v == null ? '—' : (v is int || v == v.roundToDouble() ? '${v.round()}$suffix' : '${v.toStringAsFixed(1)}$suffix');
          return TableRow(children: [
            Padding(padding: const EdgeInsets.symmetric(vertical: 7), child: Text(label, style: TextStyle(color: AppColors.ink2))),
            Padding(padding: const EdgeInsets.symmetric(vertical: 7), child: Text(f(dx), textAlign: TextAlign.right, style: TextStyle(color: AppColors.ink))),
            Padding(
              padding: const EdgeInsets.symmetric(vertical: 7),
              child: Text(f(dy),
                  textAlign: TextAlign.right,
                  style: TextStyle(
                    fontWeight: FontWeight.w800,
                    color: better ? AppColors.pos : (worse ? AppColors.neg : AppColors.ink),
                  )),
            ),
          ]);
        }

        final optional = <TableRow>[
          if (na['would_buy_pct'] != null || nb['would_buy_pct'] != null) row('Would buy', na['would_buy_pct'], nb['would_buy_pct'], suffix: '%'),
          if (na['preferred_pct'] != null || nb['preferred_pct'] != null) row('Preferred', na['preferred_pct'], nb['preferred_pct'], suffix: '%'),
          if (na['top_score_pct'] != null || nb['top_score_pct'] != null) row('Top score', na['top_score_pct'], nb['top_score_pct'], suffix: '%'),
          if (na['ideal_price'] != null || nb['ideal_price'] != null) row('Ideal price', na['ideal_price'], nb['ideal_price']),
        ];

        return ListView(
          padding: const EdgeInsets.fromLTRB(16, 12, 16, 28),
          children: [
            chips,
            const SizedBox(height: 12),
            Panel(
              child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                Text(str(data['headline'], 'Survey ${intOf(a['number'])} and Survey ${intOf(b['number'])}'),
                    style: TextStyle(fontWeight: FontWeight.w800, fontSize: 15.5, color: AppColors.ink)),
                const SizedBox(height: 8),
                Table(
                  columnWidths: const {0: FlexColumnWidth(2), 1: FlexColumnWidth(1), 2: FlexColumnWidth(1)},
                  children: [
                    TableRow(children: [
                      const SizedBox(),
                      Text('Survey ${intOf(a['number'])}', textAlign: TextAlign.right, style: TextStyle(fontSize: 12, color: AppColors.muted)),
                      Text('Survey ${intOf(b['number'])}', textAlign: TextAlign.right, style: TextStyle(fontSize: 12, color: AppColors.muted)),
                    ]),
                    row('Consumers', na['consumers'], nb['consumers']),
                    row('Average rating', na['rating'], nb['rating']),
                    row('Positive consumers', na['positive_pct'], nb['positive_pct'], suffix: '%'),
                    row('Negative consumers', na['negative_pct'], nb['negative_pct'], suffix: '%', higherIsBetter: false),
                    ...optional,
                  ],
                ),
              ]),
            ),
            const SizedBox(height: 8),
            Panel(
              child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                Text('Recipe used: v${intOf(a['recipe_version'])} → v${intOf(b['recipe_version'])}',
                    style: TextStyle(fontWeight: FontWeight.w800, color: AppColors.ink)),
                const SizedBox(height: 6),
                if (data['same_recipe'] == true)
                  Text('Both surveys used the same recipe.', style: TextStyle(color: AppColors.muted))
                else
                  for (final c in changes) _bullet(c),
              ]),
            ),
          ],
        );
      },
    );
  }
}


/// Recipe standards that stop a survey round from starting (web: OrgPortal.jsx
/// StandardsBlocked). Every applicable standard must be met; the server checks again
/// on every start and there is no override.
class StandardsBlocked extends StatelessWidget {
  const StandardsBlocked({super.key, required this.failures, this.version = 0});

  final List<Map<String, dynamic>> failures;
  final int version;

  @override
  Widget build(BuildContext context) {
    final n = failures.length;
    return Container(
      padding: const EdgeInsets.all(14),
      decoration: BoxDecoration(
        color: AppColors.negSoft,
        borderRadius: BorderRadius.circular(12),
        border: Border.all(color: AppColors.neg),
      ),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Text("A survey can't start: $n recipe standard${n == 1 ? ' is' : 's are'} not met${version > 0 ? ' by recipe v$version' : ''}.",
            style: TextStyle(fontWeight: FontWeight.w800, color: AppColors.neg)),
        const SizedBox(height: 4),
        Text('Change the recipe (or the standard) until every standard is met.', style: TextStyle(color: AppColors.ink, fontSize: 13)),
        for (final f in failures) ...[
          const Divider(height: 18),
          Text(str(f['name']), style: TextStyle(fontWeight: FontWeight.w700, color: AppColors.ink)),
          Text('Ingredient: ${str(f['ingredient'], 'Whole recipe')}', style: TextStyle(fontSize: 13, color: AppColors.muted)),
          Text('Expected: ${str(f['expected'])}', style: TextStyle(fontSize: 13, color: AppColors.muted)),
          Text('Actual: ${str(f['actual'])}', style: TextStyle(fontSize: 13, color: AppColors.muted)),
          Text(str(f['reason']), style: TextStyle(fontSize: 13, color: AppColors.neg)),
        ],
      ]),
    );
  }
}