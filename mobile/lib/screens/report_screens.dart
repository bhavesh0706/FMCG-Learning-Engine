import 'dart:async';

import 'package:flutter/material.dart';

import '../main.dart';
import '../services/api_service.dart';
import '../services/survey_api.dart';

/*
  Product reports on the phone: the same data and consolidated report as the
  web's Feedback history, laid out for a narrow screen.
*/

/* ---------------- Reading loosely-typed JSON safely ---------------- */

double? _d(dynamic v) => v is num ? v.toDouble() : double.tryParse('${v ?? ''}');
int _i(dynamic v) => v is num ? v.toInt() : int.tryParse('${v ?? ''}') ?? 0;
String _s(dynamic v) => v == null ? '' : '$v';
Map<String, dynamic> _m(dynamic v) => v is Map ? Map<String, dynamic>.from(v) : <String, dynamic>{};
List<Map<String, dynamic>> _maps(dynamic v) =>
    v is List ? v.whereType<Map>().map((m) => Map<String, dynamic>.from(m)).toList() : <Map<String, dynamic>>[];
List<String> _strings(dynamic v) =>
    v is List ? v.where((e) => e != null).map((e) => '$e').where((e) => e.trim().isNotEmpty).toList() : <String>[];

/* ---------------- Shared small widgets ---------------- */

Color get _positive => AppColors.pos;
Color get _mixed => AppColors.warn;
Color get _negative => AppColors.neg;
Color get _neutral => AppColors.neutralBar;

class ToneBar extends StatelessWidget {
  const ToneBar({super.key, required this.counts, this.height = 8});

  final Map<String, dynamic> counts;
  final double height;

  @override
  Widget build(BuildContext context) {
    final p = _i(counts['positive']), m = _i(counts['mixed']), n = _i(counts['negative']), u = _i(counts['neutral']);
    if (p + m + n + u == 0) {
      return Container(
        height: height,
        decoration: BoxDecoration(color: AppColors.bg, borderRadius: BorderRadius.circular(999)),
      );
    }
    Widget seg(int flex, Color c) => flex == 0 ? const SizedBox.shrink() : Expanded(flex: flex, child: Container(height: height, color: c));
    return ClipRRect(
      borderRadius: BorderRadius.circular(999),
      child: Row(children: [seg(p, _positive), seg(m, _mixed), seg(n, _negative), seg(u, _neutral)]),
    );
  }
}

class _ToneLegend extends StatelessWidget {
  const _ToneLegend({required this.counts});

  final Map<String, dynamic> counts;

  @override
  Widget build(BuildContext context) {
    Widget item(Color c, String text) => Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            Container(width: 8, height: 8, decoration: BoxDecoration(color: c, shape: BoxShape.circle)),
            const SizedBox(width: 5),
            Text(text, style: TextStyle(fontSize: 12.5, color: AppColors.muted)),
          ],
        );
    return Wrap(
      spacing: 12,
      runSpacing: 4,
      children: [
        item(_positive, '${_i(counts['positive'])} positive'),
        item(_mixed, '${_i(counts['mixed'])} mixed'),
        item(_negative, '${_i(counts['negative'])} negative'),
      ],
    );
  }
}

class _Card extends StatelessWidget {
  const _Card({required this.child, this.title, this.color});

  final Widget child;
  final String? title;
  final Color? color;

  @override
  Widget build(BuildContext context) {
    return Container(
      width: double.infinity,
      margin: const EdgeInsets.only(bottom: 12),
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(color: color ?? AppColors.surface, borderRadius: BorderRadius.circular(16), border: Border.all(color: AppColors.line)),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          if (title != null) ...[
            Text(title!, style: const TextStyle(fontSize: 16, fontWeight: FontWeight.w800)),
            const SizedBox(height: 10),
          ],
          child,
        ],
      ),
    );
  }
}

/// Horizontal bars: label, bar, count (and share).
class _DistBars extends StatelessWidget {
  const _DistBars({required this.rows, required this.total});

  final List<(String, int)> rows;
  final int total;

  @override
  Widget build(BuildContext context) {
    final max = rows.fold<int>(1, (m, r) => r.$2 > m ? r.$2 : m);
    return Column(
      children: [
        for (final r in rows)
          Padding(
            padding: const EdgeInsets.symmetric(vertical: 4),
            child: Row(
              children: [
                SizedBox(
                  width: 110,
                  child: Text(r.$1, maxLines: 1, overflow: TextOverflow.ellipsis, style: TextStyle(fontSize: 13, color: AppColors.ink2)),
                ),
                Expanded(
                  child: ClipRRect(
                    borderRadius: BorderRadius.circular(999),
                    child: LinearProgressIndicator(
                      value: r.$2 / max,
                      minHeight: 8,
                      color: AppColors.brand,
                      backgroundColor: AppColors.bg,
                    ),
                  ),
                ),
                SizedBox(
                  width: 62,
                  child: Text(
                    total > 0 ? '${r.$2} · ${(r.$2 * 100 / total).round()}%' : '${r.$2}',
                    textAlign: TextAlign.right,
                    style: const TextStyle(fontSize: 12.5, fontWeight: FontWeight.w600),
                  ),
                ),
              ],
            ),
          ),
      ],
    );
  }
}

/// List of points with "7 of 10" badges.
class _CountList extends StatelessWidget {
  const _CountList({required this.items, required this.total, required this.color, required this.soft, required this.icon});

  final List<Map<String, dynamic>> items;
  final int total;
  final Color color;
  final Color soft;
  final IconData icon;

  @override
  Widget build(BuildContext context) {
    return Column(
      children: [
        for (final it in items)
          Container(
            width: double.infinity,
            margin: const EdgeInsets.only(bottom: 8),
            padding: const EdgeInsets.all(12),
            decoration: BoxDecoration(color: soft, borderRadius: BorderRadius.circular(12)),
            child: Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Container(
                  width: 22,
                  height: 22,
                  decoration: BoxDecoration(color: color, shape: BoxShape.circle),
                  child: Icon(icon, size: 14, color: Colors.white),
                ),
                const SizedBox(width: 10),
                Expanded(child: Text(_s(it['point']), style: const TextStyle(fontSize: 14.5, height: 1.4))),
                const SizedBox(width: 8),
                Text('${_i(it['customers'])} of $total', style: const TextStyle(fontSize: 12.5, fontWeight: FontWeight.w800)),
              ],
            ),
          ),
      ],
    );
  }
}

class _Bullets extends StatelessWidget {
  const _Bullets({required this.items, required this.icon, required this.color});

  final List<String> items;
  final IconData icon;
  final Color color;

  @override
  Widget build(BuildContext context) {
    return Column(
      children: [
        for (final t in items)
          Padding(
            padding: const EdgeInsets.only(bottom: 8),
            child: Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Padding(padding: const EdgeInsets.only(top: 2), child: Icon(icon, size: 18, color: color)),
                const SizedBox(width: 10),
                Expanded(child: Text(t, style: const TextStyle(fontSize: 14.5, height: 1.4))),
              ],
            ),
          ),
      ],
    );
  }
}

/* ================================================================
   Product list (History -> Products)
   ================================================================ */

({String label, Color fg, Color bg}) _reportStatus(Map<String, dynamic> p) {
  final report = p['report'] is Map ? _m(p['report']) : null;
  // One report per product, kept current automatically after every new analysis.
  if (report != null && report['outdated'] != true) {
    return (label: 'Report up to date', fg: _positive, bg: AppColors.posSoft);
  }
  if (_i(p['responses']) > 0) {
    return (label: report != null ? 'Updating report…' : 'Creating report…', fg: AppColors.brand, bg: AppColors.brandSoft);
  }
  return (label: 'No feedback yet', fg: AppColors.muted, bg: AppColors.neutralSoft);
}

class ProductReportsList extends StatefulWidget {
  const ProductReportsList({super.key});

  @override
  State<ProductReportsList> createState() => _ProductReportsListState();
}

class _ProductReportsListState extends State<ProductReportsList> {
  final _api = SurveyApi();
  List<Map<String, dynamic>> _products = [];
  bool _loading = true;
  String? _error;
  String _query = '';
  bool _showAll = false;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    if (!_loading) setState(() => _loading = true);
    try {
      final list = await _api.productFeedbackList();
      if (!mounted) return;
      setState(() {
        _products = list;
        _error = null;
      });
    } catch (e) {
      if (mounted) setState(() => _error = apiErrorText(e));
    } finally {
      if (mounted) setState(() => _loading = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final q = _query.trim().toLowerCase();
    final list = _products
        .where((p) => _showAll || _i(p['responses']) > 0)
        .where((p) => q.isEmpty || '${p['name']} ${p['category_name']}'.toLowerCase().contains(q))
        .toList()
      ..sort((a, b) => _i(b['responses']).compareTo(_i(a['responses'])));

    return RefreshIndicator(
      color: AppColors.brand,
      onRefresh: _load,
      child: ListView(
        physics: const AlwaysScrollableScrollPhysics(),
        padding: const EdgeInsets.fromLTRB(16, 12, 16, 32),
        children: [
          TextField(
            decoration: InputDecoration(
              hintText: 'Search products',
              prefixIcon: const Icon(Icons.search_rounded),
              filled: true,
              fillColor: AppColors.surface,
              contentPadding: const EdgeInsets.symmetric(vertical: 12),
              border: OutlineInputBorder(borderRadius: BorderRadius.circular(14), borderSide: BorderSide(color: AppColors.line)),
              enabledBorder:
                  OutlineInputBorder(borderRadius: BorderRadius.circular(14), borderSide: BorderSide(color: AppColors.line)),
            ),
            onChanged: (v) => setState(() => _query = v),
          ),
          const SizedBox(height: 8),
          Row(
            children: [
              Expanded(
                child: Text(
                  _showAll ? 'All products' : 'Products with feedback',
                  style: TextStyle(fontSize: 13, color: AppColors.muted, fontWeight: FontWeight.w600),
                ),
              ),
              TextButton(
                onPressed: () => setState(() => _showAll = !_showAll),
                child: Text(_showAll ? 'Only with feedback' : 'Show all'),
              ),
            ],
          ),
          if (_loading && _products.isEmpty)
            Padding(padding: EdgeInsets.only(top: 40), child: Center(child: CircularProgressIndicator(color: AppColors.brand)))
          else if (_error != null && _products.isEmpty)
            _Card(
              color: AppColors.warnSoft,
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  const Text("Couldn't load products", style: TextStyle(fontWeight: FontWeight.w700)),
                  const SizedBox(height: 4),
                  Text(_error!, style: TextStyle(fontSize: 13.5, color: AppColors.ink2)),
                  TextButton(onPressed: _load, child: const Text('Try again')),
                ],
              ),
            )
          else if (list.isEmpty)
            Padding(
              padding: EdgeInsets.only(top: 40),
              child: Text('No products with feedback yet.', textAlign: TextAlign.center, style: TextStyle(color: AppColors.muted)),
            ),
          for (final p in list) _ProductCard(product: p, onChanged: _load),
        ],
      ),
    );
  }
}

class _ProductCard extends StatelessWidget {
  const _ProductCard({required this.product, required this.onChanged});

  final Map<String, dynamic> product;
  final Future<void> Function() onChanged;

  @override
  Widget build(BuildContext context) {
    final p = product;
    final responses = _i(p['responses']);
    final rating = _d(p['avg_rating']);
    final status = _reportStatus(p);
    return Padding(
      padding: const EdgeInsets.only(bottom: 10),
      child: Material(
        color: AppColors.surface,
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(16), side: BorderSide(color: AppColors.line)),
        clipBehavior: Clip.antiAlias,
        child: InkWell(
          onTap: () async {
            await Navigator.of(context).push(
              MaterialPageRoute(builder: (_) => ProductReportPage(productId: _i(p['id']), productName: _s(p['name']))),
            );
            onChanged(); // the report may have been generated
          },
          child: Padding(
            padding: const EdgeInsets.all(14),
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
                          Text(_s(p['name']), style: const TextStyle(fontSize: 16, fontWeight: FontWeight.w700)),
                          Text(_s(p['category_name']), style: TextStyle(fontSize: 13, color: AppColors.muted)),
                        ],
                      ),
                    ),
                    Container(
                      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
                      decoration: BoxDecoration(color: status.bg, borderRadius: BorderRadius.circular(999)),
                      child: Text(status.label, style: TextStyle(fontSize: 12, fontWeight: FontWeight.w700, color: status.fg)),
                    ),
                  ],
                ),
                const SizedBox(height: 12),
                Text('$responses ${responses == 1 ? 'response' : 'responses'}',
                    style: TextStyle(fontSize: 13.5, fontWeight: FontWeight.w700, color: AppColors.ink)),
                const SizedBox(height: 10),
                Row(
                  children: [
                    if (rating != null && rating > 0) ...[
                      RatingStars(rating: rating, size: 15),
                      const SizedBox(width: 6),
                      Text(rating.toStringAsFixed(1), style: const TextStyle(fontWeight: FontWeight.w700)),
                      const SizedBox(width: 14),
                    ],
                    Expanded(child: ToneBar(counts: _m(p['sentiment']), height: 6)),
                    const SizedBox(width: 4),
                    Icon(Icons.chevron_right_rounded, color: AppColors.muted),
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

/* ================================================================
   One product's consolidated report
   ================================================================ */

class ProductReportPage extends StatefulWidget {
  const ProductReportPage({super.key, required this.productId, required this.productName});

  final int productId;
  final String productName;

  @override
  State<ProductReportPage> createState() => _ProductReportPageState();
}

class _ProductReportPageState extends State<ProductReportPage> {
  final _api = SurveyApi();
  Map<String, dynamic>? _data;
  String? _error;
  bool _generating = false;
  String? _genError;
  int _elapsed = 0;
  Timer? _timer;

  @override
  void initState() {
    super.initState();
    _load();
  }

  @override
  void dispose() {
    _timer?.cancel();
    super.dispose();
  }

  Future<void> _load() async {
    try {
      final d = await _api.productFeedback(widget.productId);
      if (mounted) {
        setState(() {
          _data = d;
          _error = null;
        });
      }
    } catch (e) {
      if (mounted) setState(() => _error = apiErrorText(e));
    }
  }

  Future<void> _generate() async {
    setState(() {
      _generating = true;
      _genError = null;
      _elapsed = 0;
    });
    _timer = Timer.periodic(const Duration(seconds: 1), (_) {
      if (mounted) setState(() => _elapsed++);
    });
    try {
      final report = await _api.generateReport(widget.productId);
      if (mounted) setState(() => _data = {...?_data, 'report': report});
    } catch (e) {
      if (mounted) setState(() => _genError = apiErrorText(e));
    } finally {
      _timer?.cancel();
      if (mounted) setState(() => _generating = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        backgroundColor: AppColors.bg,
        surfaceTintColor: Colors.transparent,
        title: Text(widget.productName, style: const TextStyle(fontSize: 18, fontWeight: FontWeight.w700)),
      ),
      body: _data == null
          ? (_error == null
              ? Center(child: CircularProgressIndicator(color: AppColors.brand))
              : Padding(
                  padding: const EdgeInsets.all(24),
                  child: Column(
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      Text(_error!, textAlign: TextAlign.center),
                      const SizedBox(height: 12),
                      FilledButton(onPressed: _load, child: const Text('Try again')),
                    ],
                  ),
                ))
          : RefreshIndicator(color: AppColors.brand, onRefresh: _load, child: _body()),
    );
  }

  Widget _body() {
    final d = _data!;
    final product = _m(d['product']);
    final summary = _m(d['summary']);
    final report = d['report'] is Map ? _m(d['report']) : null;
    final responses = _i(summary['responses']);
    final canGenerate = summary['can_consolidate'] == true;
    final updating = summary['report_updating'] == true;
    final avg = _d(summary['avg_rating']);

    return ListView(
      physics: const AlwaysScrollableScrollPhysics(),
      padding: const EdgeInsets.fromLTRB(16, 4, 16, 32),
      children: [
        Text(_s(product['category_name']).toUpperCase(),
            style: TextStyle(fontSize: 11.5, fontWeight: FontWeight.w800, letterSpacing: 0.8, color: AppColors.brand)),
        const SizedBox(height: 10),

        // Summary numbers
        _Card(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(
                children: [
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text('Responses', style: TextStyle(fontSize: 12.5, color: AppColors.muted)),
                        Text('$responses', style: const TextStyle(fontSize: 22, fontWeight: FontWeight.w800)),
                      ],
                    ),
                  ),
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text('Average rating', style: TextStyle(fontSize: 12.5, color: AppColors.muted)),
                        Row(
                          children: [
                            Text(avg == null ? '—' : avg.toStringAsFixed(1),
                                style: const TextStyle(fontSize: 22, fontWeight: FontWeight.w800)),
                            if (avg != null) ...[const SizedBox(width: 6), RatingStars(rating: avg, size: 14)],
                          ],
                        ),
                      ],
                    ),
                  ),
                ],
              ),
              const SizedBox(height: 12),
              Text('Customer sentiment', style: TextStyle(fontSize: 12.5, color: AppColors.muted)),
              const SizedBox(height: 6),
              ToneBar(counts: _m(summary['sentiment']), height: 10),
              const SizedBox(height: 6),
              _ToneLegend(counts: _m(summary['sentiment'])),
            ],
          ),
        ),

        // Report status + Generate
        _Card(
          color: report == null ? AppColors.brandSoft : AppColors.surface,
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              if (_generating) ...[
                Row(
                  children: [
                    SizedBox(width: 18, height: 18, child: CircularProgressIndicator(strokeWidth: 2.4, color: AppColors.brand)),
                    const SizedBox(width: 10),
                    Text('Updating the report… ${_elapsed}s', style: const TextStyle(fontWeight: FontWeight.w700)),
                  ],
                ),
                const SizedBox(height: 6),
                Text('Combining $responses stored analyses. This usually takes under a minute.',
                    style: TextStyle(fontSize: 13.5, color: AppColors.muted)),
              ] else if (report != null) ...[
                Text(updating ? 'Consolidated report · updating with new surveys…' : 'Consolidated report',
                    style: const TextStyle(fontWeight: FontWeight.w800)),
                const SizedBox(height: 2),
                Text(
                  'Based on ${_i(report['response_count'])} ${_i(report['response_count']) == 1 ? 'survey' : 'surveys'}. '
                  'It updates by itself after every new analysis.',
                  style: TextStyle(fontSize: 13.5, color: AppColors.muted),
                ),
                const SizedBox(height: 10),
                SizedBox(
                  width: double.infinity,
                  child: OutlinedButton(onPressed: canGenerate ? _generate : null, child: const Text('Refresh now')),
                ),
              ] else if (canGenerate) ...[
                const Text('Creating the consolidated report…', style: TextStyle(fontWeight: FontWeight.w800)),
                const SizedBox(height: 2),
                Text("It's made automatically from the analyzed surveys, usually within a minute.",
                    style: TextStyle(fontSize: 13.5, color: AppColors.ink2)),
                const SizedBox(height: 10),
                SizedBox(
                  width: double.infinity,
                  child: FilledButton(onPressed: _generate, child: const Text('Create now')),
                ),
              ] else ...[
                const Text('No analyzed surveys yet', style: TextStyle(fontWeight: FontWeight.w800)),
                const SizedBox(height: 2),
                Text('The consolidated report appears automatically after the first analyzed survey.',
                    style: TextStyle(fontSize: 13.5, color: AppColors.muted)),
              ],
              if (_genError != null) ...[
                const SizedBox(height: 10),
                Text(_genError!, style: TextStyle(fontSize: 13.5, color: _negative)),
              ],
            ],
          ),
        ),

        if (report != null && !_generating) ..._reportSections(report),
      ],
    );
  }

  List<Widget> _reportSections(Map<String, dynamic> report) {
    final st = _m(report['stats']);
    final nv = _m(report['narrative']);
    final n = _i(st['response_count']) == 0 ? _i(report['response_count']) : _i(st['response_count']);
    final rating = _d(report['overall_rating']);
    final dist = _m(st['rating_distribution']);
    final sd = _m(st['sentiment_distribution']);
    final positives = _maps(nv['common_positives']);
    final issues = _maps(nv['recurring_issues']);
    final aspects = _maps(st['aspects']).take(8).toList();
    final suggestions = _strings(nv['suggestions']);
    final actions = _strings(nv['recommended_actions']);
    final neutral = _strings(nv['neutral_feedback']);
    final openAnswers = _m(nv['open_answers']);
    final questions = _maps(st['questions']);
    final recipeRecs = _maps(nv['recipe_recommendations']);

    return [
      // Rating
      _Card(
        title: 'Overall rating',
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Text(rating == null ? '—' : rating.toStringAsFixed(1),
                    style: TextStyle(fontSize: 34, fontWeight: FontWeight.w800, color: toneColor(toneForRating(rating ?? 0)))),
                const SizedBox(width: 6),
                Text('/ 5', style: TextStyle(fontSize: 16, color: AppColors.muted)),
                const Spacer(),
                if (rating != null) RatingStars(rating: rating, size: 20),
              ],
            ),
            Text('Average of $n customers', style: TextStyle(fontSize: 13, color: AppColors.muted)),
            const SizedBox(height: 10),
            _DistBars(rows: [for (final k in ['5', '4', '3', '2', '1']) ('$k ★', _i(dist[k]))], total: n),
            const SizedBox(height: 12),
            ToneBar(counts: sd, height: 10),
            const SizedBox(height: 6),
            _ToneLegend(counts: sd),
          ],
        ),
      ),

      if (_s(nv['conclusion']).isNotEmpty)
        _Card(title: 'Overall conclusion', child: Text(_s(nv['conclusion']), style: const TextStyle(fontSize: 15, height: 1.55))),

      if (positives.isNotEmpty)
        _Card(
          title: 'Common positive points',
          child: _CountList(
            items: positives, total: n, color: _positive, soft: AppColors.posSoft, icon: Icons.check_rounded),
        ),

      if (issues.isNotEmpty)
        _Card(
          title: 'Recurring issues',
          child: _CountList(
            items: issues, total: n, color: _negative, soft: AppColors.negSoft, icon: Icons.priority_high_rounded),
        ),

      if (aspects.isNotEmpty)
        _Card(
          title: 'Frequently mentioned themes',
          child: Column(
            children: [
              for (final a in aspects)
                Padding(
                  padding: const EdgeInsets.symmetric(vertical: 6),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Row(
                        children: [
                          Expanded(child: Text(_s(a['name']), style: const TextStyle(fontWeight: FontWeight.w700))),
                          Text('${_i(a['customers'])} of $n', style: const TextStyle(fontSize: 13, fontWeight: FontWeight.w700)),
                        ],
                      ),
                      const SizedBox(height: 4),
                      ClipRRect(
                        borderRadius: BorderRadius.circular(999),
                        child: LinearProgressIndicator(
                          value: n == 0 ? 0 : (_i(a['customers']) / n).clamp(0, 1).toDouble(),
                          minHeight: 6,
                          color: AppColors.brand,
                          backgroundColor: AppColors.bg,
                        ),
                      ),
                      const SizedBox(height: 4),
                      Text(
                        '${_i(a['positive'])} praised · ${_i(a['negative'])} concerned'
                        '${_d(a['avg_rating']) != null ? ' · avg ${_d(a['avg_rating'])!.toStringAsFixed(1)}' : ''}',
                        style: TextStyle(fontSize: 12.5, color: AppColors.muted),
                      ),
                    ],
                  ),
                ),
            ],
          ),
        ),

      if (suggestions.isNotEmpty || neutral.isNotEmpty)
        _Card(
          title: 'Suggestions from customers',
          child: _Bullets(items: [...suggestions, ...neutral], icon: Icons.lightbulb_outline_rounded, color: _mixed),
        ),

      if (actions.isNotEmpty)
        _Card(
          title: 'Recommended actions',
          child: _Bullets(items: actions, icon: Icons.arrow_forward_rounded, color: AppColors.brand),
        ),

      _Card(
        title: 'Recipe recommendations',
        child: recipeRecs.isEmpty
            ? Text(
                "No recipe changes suggested. Add this product's recipe in the web Catalog, then regenerate.",
                style: TextStyle(fontSize: 13.5, height: 1.45, color: AppColors.muted),
              )
            : Column(
                children: [
                  for (final r in recipeRecs)
                    Container(
                      width: double.infinity,
                      margin: const EdgeInsets.only(bottom: 8),
                      padding: const EdgeInsets.fromLTRB(12, 10, 12, 12),
                      decoration: BoxDecoration(
                        color: AppColors.bg,
                        borderRadius: BorderRadius.circular(12),
                        border: Border(left: BorderSide(color: AppColors.brand, width: 4)),
                      ),
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Row(
                            children: [
                              if (_s(r['step']).isNotEmpty) ...[
                                Container(
                                  padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 2),
                                  decoration: BoxDecoration(color: AppColors.brandSoft, borderRadius: BorderRadius.circular(999)),
                                  child: Text(_s(r['step']),
                                      style: TextStyle(fontSize: 12, fontWeight: FontWeight.w700, color: AppColors.brand)),
                                ),
                                const SizedBox(width: 8),
                              ],
                              Expanded(
                                child: Text(_s(r['ingredient']),
                                    style: TextStyle(fontWeight: FontWeight.w700, color: AppColors.ink)),
                              ),
                              Text('${_i(r['customers'])} of $n',
                                  style: TextStyle(fontSize: 12.5, fontWeight: FontWeight.w800, color: AppColors.ink)),
                            ],
                          ),
                          const SizedBox(height: 6),
                          Text(_s(r['change']), style: TextStyle(fontSize: 14.5, height: 1.45, color: AppColors.ink)),
                          if (_s(r['reason']).isNotEmpty)
                            Padding(
                              padding: const EdgeInsets.only(top: 4),
                              child: Text('Because: ${_s(r['reason'])}', style: TextStyle(fontSize: 12.5, color: AppColors.muted)),
                            ),
                        ],
                      ),
                    ),
                  Text(
                    'Test changes in small batches with your food technologist before changing production.',
                    style: TextStyle(fontSize: 12, color: AppColors.muted),
                  ),
                ],
              ),
      ),

      if (questions.isNotEmpty)
        _Card(
          title: 'Survey answers',
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              for (final q in questions) ...[
                Text(_s(q['question']), style: const TextStyle(fontSize: 14, fontWeight: FontWeight.w700, height: 1.35)),
                const SizedBox(height: 6),
                ..._questionResult(q, openAnswers),
                Divider(height: 24, color: AppColors.line),
              ],
            ],
          ),
        ),

      Text(
        'All numbers are counted directly from the $n stored analyses. '
        '${_s(nv['generated_by']) == 'ai' ? 'The wording was written by AI from those numbers.' : 'The wording was generated from the numbers alone.'} '
        'Respondents\' names and contact details are not used in this report.',
        style: TextStyle(fontSize: 12.5, height: 1.45, color: AppColors.muted),
      ),
    ];
  }

  List<Widget> _questionResult(Map<String, dynamic> q, Map<String, dynamic> openAnswers) {
    final type = _s(q['type']);
    final answered = _i(q['answered']);
    final dist = _m(q['distribution']);
    if (type == 'rating') {
      final avg = _d(q['average']);
      return [
        Text('Average ${avg == null ? '—' : avg.toStringAsFixed(1)} / 5', style: TextStyle(fontSize: 13, color: AppColors.muted)),
        _DistBars(rows: [for (final k in ['5', '4', '3', '2', '1']) ('$k ★', _i(dist[k]))], total: answered),
      ];
    }
    if (type == 'yes_no' || type == 'single_choice') {
      return [_DistBars(rows: [for (final e in dist.entries) (e.key, _i(e.value))], total: answered)];
    }
    // Free text: English summary (answers come in many languages).
    final oa = _m(openAnswers['${q['question_id']}']);
    final points = _maps(oa['points']);
    final english = _strings(oa['translations']);
    if (_s(oa['summary']).isEmpty && points.isEmpty && english.isEmpty) {
      return [Text('Regenerate the report to get these answers in English.', style: TextStyle(fontSize: 13, color: AppColors.muted))];
    }
    return [
      if (_s(oa['summary']).isNotEmpty)
        Padding(
          padding: const EdgeInsets.only(bottom: 8),
          child: Text(_s(oa['summary']), style: TextStyle(fontSize: 14, height: 1.5, color: AppColors.ink2)),
        ),
      if (points.isNotEmpty)
        for (final p in points)
          Container(
            width: double.infinity,
            margin: const EdgeInsets.only(bottom: 6),
            padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 8),
            decoration: BoxDecoration(color: AppColors.bg, borderRadius: BorderRadius.circular(10)),
            child: Row(
              children: [
                Expanded(child: Text(_s(p['point']), style: const TextStyle(fontSize: 13.5))),
                Text('${_i(p['customers'])} of ${_i(oa['answered']) == 0 ? answered : _i(oa['answered'])}',
                    style: const TextStyle(fontSize: 12.5, fontWeight: FontWeight.w800)),
              ],
            ),
          )
      else
        for (final t in english.take(6))
          Padding(
            padding: const EdgeInsets.only(bottom: 6),
            child: Text('“$t”', style: TextStyle(fontSize: 13.5, height: 1.4, color: AppColors.ink2)),
          ),
    ];
  }
}