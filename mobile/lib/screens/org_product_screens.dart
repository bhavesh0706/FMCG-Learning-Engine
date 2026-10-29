import 'package:flutter/material.dart';

import '../main.dart';
import '../services/org_api.dart';
import 'org_screens.dart';
import 'report_screens.dart' show ToneBar;

/* ============================================================
   One product (organization): Report · Recipe · Questions · Rounds · Compare
   ============================================================ */

const _tabs = ['report', 'recipe', 'questions', 'rounds', 'compare'];

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
            tabs: [Tab(text: 'Report'), Tab(text: 'Recipe'), Tab(text: 'Questions'), Tab(text: 'Rounds'), Tab(text: 'Compare')],
          ),
        ),
        body: _error != null && _detail == null
            ? ErrorRetry(message: _error!, onRetry: _load)
            : _detail == null
                ? const Center(child: CircularProgressIndicator())
                : TabBarView(children: [
                    _ReportTab(productId: widget.productId),
                    _RecipeTab(productId: widget.productId, onSaved: _load),
                    _QuestionsTab(productId: widget.productId),
                    _RoundsTab(productId: widget.productId, detail: _detail!, onChanged: _load),
                    RoundCompareView(productId: widget.productId),
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
            if (numbers.isNotEmpty)
              DropdownButtonFormField<int>(
                isExpanded: true,
                value: numbers.contains(selected) ? selected : numbers.first,
                decoration: const InputDecoration(labelText: 'Round'),
                items: [
                  for (final k in numbers)
                    DropdownMenuItem(
                      value: k,
                      child: Text('Round $k · ${str(rounds[k]!['status']) == 'live' ? 'collecting' : 'completed'} · recipe v${intOf(rounds[k]!['recipe_version'])}'),
                    ),
                ],
                onChanged: (v) => setState(() => _round = v),
              ),
            if (data['updating'] == true) ...[
              const SizedBox(height: 10),
              const Align(alignment: Alignment.centerLeft, child: Pill('Updating with the latest surveys…')),
            ],
            if (report.isEmpty)
              const EmptyNote(
                icon: Icons.assessment_outlined,
                title: 'No analyzed surveys yet',
                text: 'The report appears after the first analyzed survey of this round.',
              )
            else ...[
              const SizedBox(height: 12),
              Row(children: [
                Expanded(child: StatBox(value: '$n', label: 'Consumers')),
                const SizedBox(width: 8),
                Expanded(
                  child: StatBox(
                    value: '',
                    label: 'Average rating',
                    child: Row(children: [
                      Text(ratingText(stats['overall_rating']), style: TextStyle(fontSize: 22, fontWeight: FontWeight.w800, color: AppColors.ink)),
                      const SizedBox(width: 6),
                      RatingStars(rating: (numOrNull(stats['overall_rating']) ?? 0).toDouble(), size: 13),
                    ]),
                  ),
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
  const _RecipeTab({required this.productId, required this.onSaved});

  final int productId;
  final VoidCallback onSaved;

  @override
  Widget build(BuildContext context) {
    return Loaded<Json>(
      load: () => OrgApi.recipe(productId),
      builder: (context, recipe, reload) {
        final ingredients = asJsonList(recipe['ingredients']);
        final steps = asJsonList(recipe['steps']);
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
              'The report\'s recommendations refer to these ingredients and steps. Saving a change creates a new version and tells the research team.',
              style: TextStyle(color: AppColors.muted, height: 1.4),
            ),
            const SizedBox(height: 12),
            if (ingredients.isEmpty && steps.isEmpty)
              Panel(
                child: Column(children: [
                  const EmptyNote(icon: Icons.menu_book_outlined, title: 'No recipe yet', text: 'Add how the product is made.'),
                  FilledButton.icon(onPressed: edit, icon: const Icon(Icons.add), label: const Text('Add recipe')),
                ]),
              )
            else ...[
              Panel(
                child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                  Row(children: [
                    Expanded(child: Text('Batch size', style: TextStyle(fontWeight: FontWeight.w800, color: AppColors.ink))),
                    TextButton(onPressed: edit, child: const Text('Edit')),
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
              Panel(
                child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                  Text('Production steps · ${steps.length}', style: TextStyle(fontWeight: FontWeight.w800, color: AppColors.ink)),
                  const SizedBox(height: 6),
                  for (var k = 0; k < steps.length; k++)
                    Padding(
                      padding: const EdgeInsets.symmetric(vertical: 5),
                      child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
                        CircleAvatar(
                          radius: 11,
                          backgroundColor: AppColors.brandSoft,
                          child: Text('${k + 1}', style: TextStyle(fontSize: 11, fontWeight: FontWeight.w800, color: AppColors.brand)),
                        ),
                        const SizedBox(width: 10),
                        Expanded(
                          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                            Text(str(steps[k]['title']), style: TextStyle(color: AppColors.ink, fontWeight: FontWeight.w600)),
                            if ([steps[k]['temperature'], steps[k]['duration']].any((v) => str(v).isNotEmpty))
                              Text([str(steps[k]['temperature']), str(steps[k]['duration'])].where((s) => s.isNotEmpty).join(' · '),
                                  style: TextStyle(fontSize: 12, color: AppColors.muted)),
                            if (str(steps[k]['details']).isNotEmpty) Text(str(steps[k]['details']), style: TextStyle(fontSize: 12.5, color: AppColors.ink2)),
                          ]),
                        ),
                      ]),
                    ),
                ]),
              ),
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
              FilledButton.icon(onPressed: edit, icon: const Icon(Icons.edit_outlined), label: const Text('Edit recipe')),
            ],
          ],
        );
      },
    );
  }
}

/// Full-screen recipe editor; saving creates a new recipe version on the server.
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
  final List<List<TextEditingController>> _ingredients = []; // name, quantity, unit, notes
  final List<List<TextEditingController>> _steps = []; // title, temperature, duration, details
  // Rows removed while editing: disposed when the editor closes (their fields may
  // still be on screen during the frame that removes them).
  final List<TextEditingController> _removed = [];
  bool _saving = false;
  String? _error;

  @override
  void initState() {
    super.initState();
    for (final i in asJsonList(widget.recipe['ingredients'])) {
      _ingredients.add([for (final k in ['name', 'quantity', 'unit', 'notes']) TextEditingController(text: str(i[k]))]);
    }
    for (final s in asJsonList(widget.recipe['steps'])) {
      _steps.add([for (final k in ['title', 'temperature', 'duration', 'details']) TextEditingController(text: str(s[k]))]);
    }
    if (_ingredients.isEmpty) _addIngredient();
    if (_steps.isEmpty) _addStep();
  }

  @override
  void dispose() {
    for (final c in [_batch, _notes, ..._ingredients.expand((r) => r), ..._steps.expand((r) => r), ..._removed]) {
      c.dispose();
    }
    super.dispose();
  }

  void _addIngredient() => setState(() => _ingredients.add(List.generate(4, (_) => TextEditingController())));
  void _addStep() => setState(() => _steps.add(List.generate(4, (_) => TextEditingController())));

  Future<void> _save() async {
    String? t(TextEditingController c) => c.text.trim().isEmpty ? null : c.text.trim();
    final body = <String, dynamic>{
      'batch_size': t(_batch),
      'notes': t(_notes),
      'ingredients': [
        for (final r in _ingredients)
          if (r[0].text.trim().isNotEmpty) {'name': r[0].text.trim(), 'quantity': t(r[1]), 'unit': t(r[2]), 'notes': t(r[3])},
      ],
      'steps': [
        for (final r in _steps)
          if (r[0].text.trim().isNotEmpty) {'title': r[0].text.trim(), 'temperature': t(r[1]), 'duration': t(r[2]), 'details': t(r[3])},
      ],
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

  Widget _field(TextEditingController c, String label, {int lines = 1, String? hint}) => Padding(
        padding: const EdgeInsets.only(bottom: 8),
        child: TextField(controller: c, minLines: lines, maxLines: lines == 1 ? 1 : 4, decoration: InputDecoration(labelText: label, hintText: hint)),
      );

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: AppColors.bg,
      appBar: AppBar(title: const Text('Edit recipe')),
      body: ListView(
        padding: const EdgeInsets.fromLTRB(16, 8, 16, 120),
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
                  ]),
                ),
                Padding(padding: const EdgeInsets.only(right: 8), child: _field(_ingredients[k][3], 'Why it\'s there (optional)')),
              ]),
            ),
          TextButton.icon(onPressed: _addIngredient, icon: const Icon(Icons.add), label: const Text('Add ingredient')),
          _section('Production steps'),
          for (var k = 0; k < _steps.length; k++)
            Panel(
              padding: const EdgeInsets.fromLTRB(12, 12, 4, 4),
              child: Column(children: [
                Row(children: [
                  Expanded(child: _field(_steps[k][0], 'Step ${k + 1}')),
                  IconButton(
                    tooltip: 'Remove step',
                    onPressed: () => setState(() => _removed.addAll(_steps.removeAt(k))),
                    icon: Icon(Icons.close, color: AppColors.muted),
                  ),
                ]),
                Padding(
                  padding: const EdgeInsets.only(right: 8),
                  child: Row(children: [
                    Expanded(child: _field(_steps[k][1], 'Temperature', hint: '85 °C')),
                    const SizedBox(width: 8),
                    Expanded(child: _field(_steps[k][2], 'Duration', hint: '30 min')),
                  ]),
                ),
                Padding(padding: const EdgeInsets.only(right: 8), child: _field(_steps[k][3], 'Details (optional)', lines: 2)),
              ]),
            ),
          TextButton.icon(onPressed: _addStep, icon: const Icon(Icons.add), label: const Text('Add step')),
          _section('Notes'),
          _field(_notes, 'Storage, shelf life, packaging (optional)', lines: 3),
          if (_error != null) Text(_error!, style: TextStyle(color: AppColors.neg)),
        ],
      ),
      bottomNavigationBar: SafeArea(
        minimum: const EdgeInsets.fromLTRB(16, 8, 16, 12),
        child: FilledButton(
          onPressed: _saving ? null : _save,
          child: Text(_saving ? 'Saving…' : 'Save as a new version'),
        ),
      ),
    );
  }
}

// ---------------------------------------------------------------- survey questions

const _typeLabels = {'rating': 'Rating 1–5', 'yes_no': 'Yes / No', 'single_choice': 'Single choice', 'text': 'Text answer'};

class _QuestionsTab extends StatelessWidget {
  const _QuestionsTab({required this.productId});

  final int productId;

  @override
  Widget build(BuildContext context) {
    return Loaded<List<Json>>(
      load: () => OrgApi.questions(productId),
      builder: (context, all, reload) {
        final qs = all.where((q) => q['is_active'] != false).toList()..sort((a, b) => intOf(a['position']).compareTo(intOf(b['position'])));
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
              'Survey persons ask these questions, in this order. Keep them the same between rounds you want to compare.',
              style: TextStyle(color: AppColors.muted, height: 1.4),
            ),
            const SizedBox(height: 10),
            Align(alignment: Alignment.centerLeft, child: Pill(status, tone: tone)),
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
                        ].join(' · '),
                        style: TextStyle(fontSize: 12, color: AppColors.muted),
                      ),
                    ]),
                  ),
                  PopupMenuButton<String>(
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
    if (_type == 'single_choice' && options.length < 2) {
      setState(() => _error = 'Add at least two answer options, one per line.');
      return;
    }
    final body = <String, dynamic>{
      'text': _text.text.trim(),
      'question_type': _type,
      'required': _required,
      'options': _type == 'single_choice' ? options : null,
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
              style: TextStyle(fontSize: 18, fontWeight: FontWeight.w800, color: AppColors.ink)),
          const SizedBox(height: 14),
          TextField(controller: _text, minLines: 2, maxLines: 4, decoration: const InputDecoration(labelText: 'Question')),
          const SizedBox(height: 12),
          DropdownButtonFormField<String>(
            isExpanded: true,
            value: _type,
            decoration: const InputDecoration(labelText: 'Answer type'),
            items: [for (final e in _typeLabels.entries) DropdownMenuItem(value: e.key, child: Text(e.value))],
            onChanged: (v) => setState(() => _type = v ?? 'rating'),
          ),
          if (_type == 'single_choice') ...[
            const SizedBox(height: 12),
            TextField(
              controller: _options,
              minLines: 3,
              maxLines: 6,
              decoration: const InputDecoration(labelText: 'Answer options', hintText: 'One per line'),
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

    Future<void> start() async {
      final started = await showModalBottomSheet<bool>(
        context: context,
        isScrollControlled: true,
        useSafeArea: true,
        builder: (_) => _StartRoundSheet(
          productId: productId,
          number: (rounds.isEmpty ? 0 : intOf(rounds.first['number'])) + 1,
          recipeVersion: intOf(detail['latest_version']),
          defaultTarget: intOf(detail['default_target'], 30),
        ),
      );
      if (started == true) {
        await onChanged();
        if (context.mounted) showMessage(context, 'Round started. Survey persons can now survey it.');
      }
    }

    Future<void> end() async {
      final n = intOf(live['number']);
      final ok = await confirmAction(
        context,
        title: 'End Round $n?',
        message: 'No new surveys will join it. Its report keeps the ${intOf(live['done'])} surveys collected so far.',
        confirmLabel: 'End round',
        cancelLabel: 'Keep collecting',
      );
      if (!ok) return;
      try {
        await OrgApi.endRound(productId, n);
        await onChanged();
        if (context.mounted) showMessage(context, 'Round $n ended.');
      } catch (e) {
        if (context.mounted) showMessage(context, '$e', error: true);
      }
    }

    Widget card(Json r, {bool isLive = false}) {
      final done = intOf(r['done']), target = intOf(r['target']);
      return Panel(
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Row(children: [
            Expanded(child: Text('Round ${intOf(r['number'])}', style: TextStyle(fontWeight: FontWeight.w800, fontSize: 15.5, color: AppColors.ink))),
            Pill(isLive ? 'Collecting' : (str(r['closed_reason']) == 'ended' ? 'Ended' : 'Complete'), tone: isLive ? 'ok' : 'muted'),
          ]),
          const SizedBox(height: 4),
          Text(
            [
              'Recipe v${intOf(r['recipe_version'])}',
              '$done of $target consumers',
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
          if (isLive) ...[
            const SizedBox(height: 10),
            OutlinedButton(onPressed: end, child: const Text('End round')),
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
            'A round is one batch of consumer surveys for one recipe version. Start a new round after changing the recipe, then compare.',
            style: TextStyle(color: AppColors.muted, height: 1.4),
          ),
          const SizedBox(height: 12),
          if (live.isNotEmpty) ...[card(live, isLive: true), const SizedBox(height: 8)],
          for (final r in past) ...[card(r), const SizedBox(height: 8)],
          if (rounds.isEmpty) const EmptyNote(icon: Icons.flag_outlined, title: 'No rounds yet', text: 'Start the first round to collect consumer feedback.'),
          const SizedBox(height: 6),
          FilledButton.icon(onPressed: canStart ? start : null, icon: const Icon(Icons.play_arrow_rounded), label: const Text('Start new round')),
          if (!canStart && live.isNotEmpty)
            Padding(
              padding: const EdgeInsets.only(top: 6),
              child: Text('Round ${intOf(live['number'])} is collecting. End it to start a new one.',
                  textAlign: TextAlign.center, style: TextStyle(fontSize: 12.5, color: AppColors.muted)),
            ),
        ],
      ),
    );
  }
}

class _StartRoundSheet extends StatefulWidget {
  const _StartRoundSheet({required this.productId, required this.number, required this.recipeVersion, required this.defaultTarget});

  final int productId;
  final int number;
  final int recipeVersion;
  final int defaultTarget;

  @override
  State<_StartRoundSheet> createState() => _StartRoundSheetState();
}

class _StartRoundSheetState extends State<_StartRoundSheet> {
  late final TextEditingController _target = TextEditingController(text: '${widget.defaultTarget}');
  final TextEditingController _note = TextEditingController();
  bool _saving = false;
  String? _error;

  @override
  void dispose() {
    _target.dispose();
    _note.dispose();
    super.dispose();
  }

  Future<void> _start() async {
    final target = int.tryParse(_target.text.trim());
    if (target == null || target < 1) {
      setState(() => _error = 'Enter how many consumers to survey (1 or more).');
      return;
    }
    setState(() {
      _saving = true;
      _error = null;
    });
    try {
      await OrgApi.startRound(widget.productId, target: target, note: _note.text);
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
      child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, mainAxisSize: MainAxisSize.min, children: [
        Text('Start Round ${widget.number}', style: TextStyle(fontSize: 18, fontWeight: FontWeight.w800, color: AppColors.ink)),
        const SizedBox(height: 4),
        Text(widget.recipeVersion > 0 ? 'Consumers taste recipe v${widget.recipeVersion}.' : 'Add a recipe first to compare rounds by recipe.',
            style: TextStyle(color: AppColors.muted)),
        const SizedBox(height: 14),
        TextField(
          controller: _target,
          keyboardType: TextInputType.number,
          decoration: const InputDecoration(labelText: 'Consumers to survey'),
        ),
        const SizedBox(height: 12),
        TextField(controller: _note, decoration: const InputDecoration(labelText: 'Note for the research team (optional)', hintText: 'Less sugar')),
        if (_error != null) Padding(padding: const EdgeInsets.only(top: 8), child: Text(_error!, style: TextStyle(color: AppColors.neg))),
        const SizedBox(height: 14),
        FilledButton(onPressed: _saving ? null : _start, child: Text(_saving ? 'Starting…' : 'Start Round ${widget.number}')),
        TextButton(onPressed: () => Navigator.of(context).pop(false), child: const Text('Cancel')),
      ]),
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
  int? _a;
  int? _b;

  @override
  Widget build(BuildContext context) {
    return Loaded<Json>(
      key: ValueKey('compare-$_a-$_b'),
      load: () => OrgApi.compare(widget.productId, a: _a, b: _b),
      builder: (context, data, reload) {
        if (data['enough_rounds'] != true) {
          return ListView(children: const [
            EmptyNote(
              icon: Icons.compare_arrows,
              title: 'Compare needs two rounds',
              text: 'Start a second round (for example after changing the recipe) to compare them side by side.',
            ),
          ]);
        }
        final rounds = asJsonList(data['rounds']);
        final a = asJson(data['a']), b = asJson(data['b']);
        final na = asJson(a['numbers']), nb = asJson(b['numbers']);
        final changes = (data['recipe_changes'] is List ? data['recipe_changes'] as List : const []).map((e) => '$e').toList();

        Widget picker(String label, int value, void Function(int) onPick) => Expanded(
              child: DropdownButtonFormField<int>(
                isExpanded: true,
                value: value,
                decoration: InputDecoration(labelText: label),
                items: [
                  for (final r in rounds)
                    DropdownMenuItem(value: intOf(r['number']), child: Text('Round ${intOf(r['number'])} · v${intOf(r['recipe_version'])}')),
                ],
                onChanged: (v) {
                  if (v != null) setState(() => onPick(v));
                },
              ),
            );

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
            Row(children: [
              picker('From', intOf(a['number']), (v) => _a = v),
              const SizedBox(width: 10),
              picker('To', intOf(b['number']), (v) => _b = v),
            ]),
            const SizedBox(height: 12),
            Panel(
              child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                Text(str(data['headline'], 'Round ${intOf(a['number'])} and Round ${intOf(b['number'])}'),
                    style: TextStyle(fontWeight: FontWeight.w800, fontSize: 15.5, color: AppColors.ink)),
                const SizedBox(height: 8),
                Table(
                  columnWidths: const {0: FlexColumnWidth(2), 1: FlexColumnWidth(1), 2: FlexColumnWidth(1)},
                  children: [
                    TableRow(children: [
                      const SizedBox(),
                      Text('Round ${intOf(a['number'])}', textAlign: TextAlign.right, style: TextStyle(fontSize: 12, color: AppColors.muted)),
                      Text('Round ${intOf(b['number'])}', textAlign: TextAlign.right, style: TextStyle(fontSize: 12, color: AppColors.muted)),
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
                  Text('Both rounds used the same recipe.', style: TextStyle(color: AppColors.muted))
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