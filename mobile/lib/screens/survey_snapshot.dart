import 'package:flutter/material.dart';

import '../main.dart';
import '../services/org_api.dart';
import '../widgets/search_dropdown.dart';
import 'org_screens.dart' show Loaded, Panel, Pill;

/// Standards, Recipe and Questions tabs of a product: the current setup (editable, [child]) or,
/// once a survey is chosen, what that survey used (read-only, from
/// GET /org/products/{id}/rounds/{n}/snapshot). Same as the web's survey box and selector.
class SurveyScoped extends StatefulWidget {
  const SurveyScoped({super.key, required this.productId, required this.detail, required this.kind, required this.child, this.snapshotView});

  final int productId;
  final Json detail;
  final String kind; // recipe | standards | questions
  final Widget child;
  /// The tab's own view fed with the survey's data (same layout, read-only); else a simple summary.
  final Widget Function(Json data)? snapshotView;

  @override
  State<SurveyScoped> createState() => _SurveyScopedState();
}

class _SurveyScopedState extends State<SurveyScoped> {
  int? _round; // null = the current setup

  @override
  Widget build(BuildContext context) {
    final rounds = asJsonList(widget.detail['rounds']).reversed.toList();
    final latest = intOf(widget.detail['latest_version'], 1);
    final chosen = rounds.where((r) => intOf(r['number']) == _round).firstOrNull;
    return Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
      if (rounds.isNotEmpty)
        Padding(
          padding: const EdgeInsets.fromLTRB(16, 12, 16, 0),
          child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
            SearchDropdownField<int>(
              isExpanded: true,
              value: _round ?? 0,
              decoration: const InputDecoration(labelText: 'Survey', isDense: true),
              items: [
                DropdownMenuItem(value: 0, child: Text('Current (Recipe v$latest)')),
                for (final r in rounds)
                  DropdownMenuItem(
                    value: intOf(r['number']),
                    child: Text('Survey ${intOf(r['number'])} · v${intOf(r['recipe_version'])} · '
                        '${str(r['status']) == 'live' ? 'collecting' : 'completed'}'),
                  ),
              ],
              onChanged: (v) => setState(() => _round = v == null || v == 0 ? null : v),
            ),
            const SizedBox(height: 8),
            _box(chosen, rounds, latest),
          ]),
        ),
      Expanded(
        child: chosen == null
            ? widget.child
            : Loaded<Json>(
                key: ValueKey('snap-${widget.kind}-$_round'),
                load: () => OrgApi.request('GET', '/org/products/${widget.productId}/rounds/$_round/snapshot'),
                builder: (context, data, reload) => widget.snapshotView != null
                    ? widget.snapshotView!(data)
                    : ListView(
                        padding: const EdgeInsets.fromLTRB(16, 12, 16, 32),
                        children: [_snapshot(data, intOf(chosen['number']))],
                      ),
              ),
      ),
    ]);
  }

  Widget _box(Json? survey, List<Json> rounds, int latest) {
    if (survey == null) {
      final users = rounds.where((r) => intOf(r['recipe_version']) == latest).map((r) => 'Survey ${intOf(r['number'])}').toList();
      return Panel(
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text('Current setup · Recipe v$latest', style: TextStyle(fontWeight: FontWeight.w800, color: AppColors.ink)),
          const SizedBox(height: 2),
          Text('${users.isEmpty ? 'Not surveyed yet.' : 'Used by ${users.join(', ')}.'} Changes apply to the next survey.',
              style: TextStyle(fontSize: 13, color: AppColors.muted)),
        ]),
      );
    }
    final live = str(survey['status']) == 'live';
    return Panel(
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Row(children: [
          Expanded(child: Text('Survey ${intOf(survey['number'])}', style: TextStyle(fontWeight: FontWeight.w800, color: AppColors.ink))),
          Pill(live ? 'Collecting' : 'Completed', tone: live ? 'ok' : 'muted'),
        ]),
        const SizedBox(height: 2),
        Text(
          'Recipe v${intOf(survey['recipe_version'])} · ${intOf(survey['done'])} of ${intOf(survey['target'])} consumers'
          '${_day(survey['started_at']).isEmpty ? '' : ' · started ${_day(survey['started_at'])}'}'
          '${_day(survey['closed_at']).isEmpty ? '' : ' · ended ${_day(survey['closed_at'])}'}. Read-only.',
          style: TextStyle(fontSize: 13, color: AppColors.muted),
        ),
      ]),
    );
  }

  static String _day(Object? iso) {
    final d = DateTime.tryParse(str(iso))?.toLocal();
    if (d == null) return '';
    const m = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
    return '${d.day} ${m[d.month - 1]} ${d.year}';
  }

  Widget _snapshot(Json data, int number) {
    final recipe = asJson(data['recipe']);
    final title = TextStyle(fontWeight: FontWeight.w800, fontSize: 16, color: AppColors.ink);
    final muted = TextStyle(fontSize: 13, color: AppColors.muted);
    if (widget.kind == 'recipe') {
      final ingredients = asJsonList(recipe['ingredients']);
      final steps = (recipe['steps'] as List?) ?? const [];
      return Panel(
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text('Recipe v${intOf(recipe['version'])}', style: title),
          if (str(recipe['batch_size']).isNotEmpty) Text('Batch ${str(recipe['batch_size'])}', style: muted),
          const SizedBox(height: 8),
          if (recipe['has_recipe'] != true) Text('No recipe was saved for this version.', style: muted),
          for (var i = 0; i < ingredients.length; i++)
            Padding(
              padding: const EdgeInsets.symmetric(vertical: 4),
              child: Row(children: [
                SizedBox(width: 26, child: Text('${i + 1}', style: muted)),
                Expanded(child: Text(str(ingredients[i]['name']), style: TextStyle(color: AppColors.ink))),
                Text([str(ingredients[i]['quantity']), str(ingredients[i]['unit'])].where((x) => x.isNotEmpty).join(' '),
                    style: TextStyle(fontWeight: FontWeight.w700, color: AppColors.ink2)),
              ]),
            ),
          if (steps.isNotEmpty) ...[
            const SizedBox(height: 10),
            Text('Steps', style: title),
            for (var i = 0; i < steps.length; i++)
              Padding(
                padding: const EdgeInsets.only(top: 4),
                child: Text('${i + 1}. ${steps[i] is Map ? str((steps[i] as Map)['text'] ?? (steps[i] as Map)['description']) : '${steps[i]}'}'),
              ),
          ],
          if (str(recipe['process']).isNotEmpty) ...[
            const SizedBox(height: 10),
            Text('Production process', style: title),
            const SizedBox(height: 4),
            Text(str(recipe['process']), style: TextStyle(color: AppColors.ink2, height: 1.4)),
          ],
        ]),
      );
    }
    if (widget.kind == 'standards') {
      final st = asJson(data['standards']);
      final results = asJsonList(st['results']);
      final failed = intOf(st['failed_count']);
      return Panel(
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text('Recipe standards · Recipe v${intOf(recipe['version'])}', style: title),
          Text(results.isEmpty ? 'No standards apply' : failed > 0 ? '$failed of ${results.length} not met' : '${results.length} standards, all met',
              style: TextStyle(fontWeight: FontWeight.w700, color: failed > 0 ? AppColors.warn : AppColors.pos)),
          Text('Today\'s standards, checked against the recipe this survey used.', style: muted),
          const SizedBox(height: 8),
          for (final r in results)
            Padding(
              padding: const EdgeInsets.symmetric(vertical: 5),
              child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
                Expanded(
                  child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                    Text(str(r['name']), style: TextStyle(fontWeight: FontWeight.w700, color: AppColors.ink)),
                    Text('Expected ${str(r['expected'])}${r['actual'] == null ? '' : ' · actual ${str(r['actual'])}'}', style: muted),
                  ]),
                ),
                Pill(r['passed'] == true ? 'Met' : 'Not met', tone: r['passed'] == true ? 'ok' : 'warn'),
              ]),
            ),
        ]),
      );
    }
    final questions = asJsonList(data['questions']);
    const kinds = {'rating': 'Rating 1–5', 'yes_no': 'Yes / No', 'single_choice': 'Single choice', 'text': 'Free text'};
    return Panel(
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Text('${questions.length} question${questions.length == 1 ? '' : 's'}', style: title),
        Text(str(data['questions_source']) == 'answers' ? 'As Survey $number\'s consumers were asked them.' : 'No answers yet: the questions it asks now.',
            style: muted),
        const SizedBox(height: 8),
        for (var i = 0; i < questions.length; i++)
          Padding(
            padding: const EdgeInsets.symmetric(vertical: 5),
            child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Text('${i + 1}. ${str(questions[i]['text'])}', style: TextStyle(fontWeight: FontWeight.w700, color: AppColors.ink)),
              Text(
                [
                  kinds[str(questions[i]['question_type'])] ?? str(questions[i]['question_type']),
                  if ((questions[i]['options'] as List?)?.isNotEmpty ?? false) (questions[i]['options'] as List).join(' / '),
                  if (questions[i]['still_asked'] == false) 'No longer asked',
                ].join(' · '),
                style: muted,
              ),
            ]),
          ),
      ]),
    );
  }
}
