import 'package:flutter/material.dart';

import '../main.dart';
import '../services/admin_api.dart';
import '../services/org_api.dart';
import 'org_screens.dart';

/*
  Product checks (admins): same as the web's Survey history > Product checks.
  After each analysis the server checks that the customer talked about the product
  the survey person selected (backend services/product_check.py):
    Mismatch detected  -> kept out of every report until reviewed here
    Unverified         -> no product named (or mixed): counted under the selected product
  Confirm keeps the selected product, Correct moves the survey to the right product,
  Mark invalid keeps it out for good (the recording is kept). Every decision is audited.
*/

const _filters = [('pending', 'Needs review'), ('unverified', 'Unverified'), ('reviewed', 'Reviewed')];
const _reviewLabel = {'confirmed': 'Confirmed', 'corrected': 'Corrected', 'invalid': 'Marked invalid'};

String _evidence(Json c) {
  final ev = asJson(c['evidence']);
  final mentions = asJson(ev['mentions']).entries.map((e) => '“${e.key}” ×${e.value}');
  final words = asJson(ev['words']).entries.map((e) => '${e.key} words ×${e.value}');
  final all = [...mentions, ...words];
  return all.isEmpty ? 'No product named in the recording.' : all.join(' · ');
}

String _when(dynamic iso) {
  final d = DateTime.tryParse(str(iso));
  return d == null ? '' : formatDate(d.toLocal());
}

/// Full page (opened from a "Product mismatch" notification).
class ProductChecksPage extends StatelessWidget {
  const ProductChecksPage({super.key});

  @override
  Widget build(BuildContext context) => Scaffold(
        backgroundColor: AppColors.bg,
        appBar: AppBar(title: const Text('Product checks')),
        body: const ProductChecksView(),
      );
}

/// The list with its filter (History > Checks tab for admins).
class ProductChecksView extends StatefulWidget {
  const ProductChecksView({super.key});

  @override
  State<ProductChecksView> createState() => _ProductChecksViewState();
}

class _ProductChecksViewState extends State<ProductChecksView> {
  String _filter = 'pending';
  List<Json>? _checks;
  Json _counts = {};
  String? _error;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final r = await AdminApi.productChecks(_filter);
      if (!mounted) return;
      setState(() {
        _checks = (r['checks'] is List ? r['checks'] as List : const []).map(asJson).toList();
        _counts = asJson(r['counts']);
        _error = null;
      });
    } catch (e) {
      if (mounted) setState(() => _error = '$e');
    }
  }

  Future<void> _open(Json c) async {
    final changed = await showModalBottomSheet<bool>(
      context: context,
      isScrollControlled: true,
      useSafeArea: true,
      builder: (_) => _CheckSheet(check: c),
    );
    if (changed == true) _load();
  }

  @override
  Widget build(BuildContext context) {
    final checks = _checks;
    return RefreshIndicator(
      onRefresh: _load,
      child: ListView(
        padding: const EdgeInsets.fromLTRB(16, 12, 16, 24),
        children: [
          Text('After each analysis we check that the customer talked about the selected product. '
              'Mismatches stay out of the reports until you review them.',
              style: TextStyle(color: AppColors.muted, height: 1.4)),
          const SizedBox(height: 12),
          Wrap(spacing: 8, runSpacing: 8, children: [
            for (final (key, label) in _filters)
              ChoiceChip(
                label: Text(_counts[key] != null && key != 'reviewed' ? '$label ${intOf(_counts[key])}' : label),
                selected: _filter == key,
                onSelected: (_) {
                  setState(() {
                    _filter = key;
                    _checks = null;
                  });
                  _load();
                },
              ),
          ]),
          const SizedBox(height: 12),
          if (_error != null) Text(_error!, style: TextStyle(color: AppColors.neg)),
          if (checks == null && _error == null) const Padding(padding: EdgeInsets.all(32), child: Center(child: CircularProgressIndicator())),
          if (checks != null && checks.isEmpty)
            EmptyNote(
              icon: Icons.fact_check_outlined,
              title: _filter == 'pending' ? 'Nothing to review' : (_filter == 'unverified' ? 'No unverified surveys' : 'No reviews yet'),
              text: _filter == 'pending' ? 'Every analyzed survey talks about the product that was selected.' : '',
            ),
          if (checks != null)
            for (final c in checks) _CheckCard(check: c, onTap: () => _open(c)),
        ],
      ),
    );
  }
}

class _CheckCard extends StatelessWidget {
  const _CheckCard({required this.check, required this.onTap});

  final Json check;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final c = check;
    final survey = asJson(c['survey']);
    final reviewed = _reviewLabel[str(c['review_status'])];
    final detected = asJson(c['detected_product']);
    final tone = reviewed != null ? 'ok' : (c['status'] == 'mismatch' ? 'neg' : 'warn');
    return Card(
      margin: const EdgeInsets.only(bottom: 10),
      child: InkWell(
        borderRadius: BorderRadius.circular(16),
        onTap: onTap,
        child: Padding(
          padding: const EdgeInsets.all(14),
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Row(children: [
              Expanded(child: Text('Survey #${intOf(c['response_id'])}', style: TextStyle(fontWeight: FontWeight.w800, color: AppColors.ink))),
              Pill(reviewed ?? str(c['label']), tone: tone),
            ]),
            const SizedBox(height: 2),
            Text([str(survey['customer']), if (str(survey['executive_name']).isNotEmpty) 'by ${str(survey['executive_name'])}', _when(survey['created_at'])]
                    .where((s) => s.isNotEmpty)
                    .join(' · '),
                style: TextStyle(fontSize: 13, color: AppColors.muted)),
            const SizedBox(height: 10),
            Text('Selected: ${str(asJson(c['selected_product'])['name'])}', style: TextStyle(color: AppColors.ink2)),
            Text('Detected: ${detected.isEmpty ? 'not identified' : str(detected['name'])}'
                '${c['confidence'] != null && (c['confidence'] as num) > 0 ? ' · ${((c['confidence'] as num) * 100).round()}% sure' : ''}',
                style: TextStyle(fontWeight: FontWeight.w700, color: AppColors.ink)),
            if (asJson(c['corrected_product']).isNotEmpty)
              Text('Corrected to: ${str(asJson(c['corrected_product'])['name'])}', style: TextStyle(color: AppColors.pos, fontWeight: FontWeight.w700)),
            const SizedBox(height: 6),
            Text(_evidence(c), style: TextStyle(fontSize: 12.5, color: AppColors.muted)),
            if (c['status'] == 'mismatch' && c['review_status'] == 'pending') ...[
              const SizedBox(height: 8),
              Text("Kept out of ${str(asJson(c['selected_product'])['name'])}'s report until you review it.",
                  style: TextStyle(fontSize: 13, color: AppColors.neg, fontWeight: FontWeight.w600)),
            ],
          ]),
        ),
      ),
    );
  }
}

/// Details: transcript, AI summary, history, and the review actions.
class _CheckSheet extends StatefulWidget {
  const _CheckSheet({required this.check});

  final Json check;

  @override
  State<_CheckSheet> createState() => _CheckSheetState();
}

class _CheckSheetState extends State<_CheckSheet> {
  Json? _detail;
  List<Json> _products = const [];
  int? _correctTo;
  final _remarks = TextEditingController();
  bool _busy = false;
  String? _error;

  int get _id => intOf(widget.check['response_id']);

  @override
  void initState() {
    super.initState();
    AdminApi.productCheck(_id).then((d) => mounted ? setState(() => _detail = d) : null).catchError((e) {
      if (mounted) setState(() => _error = '$e');
      return null;
    });
    AdminApi.products().then((p) => mounted ? setState(() => _products = p) : null).catchError((_) => null);
    final detected = asJson(widget.check['detected_product']);
    if (detected.isNotEmpty) _correctTo = intOf(detected['id']);
  }

  @override
  void dispose() {
    _remarks.dispose();
    super.dispose();
  }

  Future<void> _act(String action) async {
    FocusManager.instance.primaryFocus?.unfocus();
    if (action == 'invalid') {
      final ok = await confirmAction(context,
          title: 'Mark survey #$_id invalid?',
          message: "It stays out of every report. The recording and answers are kept.",
          confirmLabel: 'Mark invalid',
          cancelLabel: 'Cancel');
      if (!ok) return;
    }
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      await AdminApi.reviewProductCheck(_id, action, productId: action == 'correct' ? _correctTo : null, remarks: _remarks.text);
      if (!mounted) return;
      showMessage(context, action == 'correct' ? 'Survey moved to the right product.' : (action == 'confirm' ? 'Product confirmed.' : 'Survey marked invalid.'));
      Navigator.of(context).pop(true);
    } catch (e) {
      if (mounted) {
        setState(() {
          _busy = false;
          _error = '$e';
        });
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final c = widget.check;
    final d = _detail;
    final selected = asJson(c['selected_product']);
    final reviewed = _reviewLabel[str(c['review_status'])];
    final history = d == null || d['history'] is! List ? const <Json>[] : (d['history'] as List).map(asJson).toList();
    return Padding(
      padding: EdgeInsets.only(bottom: MediaQuery.viewInsetsOf(context).bottom), // keyboard: keep the actions visible
      child: DraggableScrollableSheet(
        expand: false,
        initialChildSize: 0.85,
        maxChildSize: 0.95,
        builder: (context, scroll) => ListView(
          controller: scroll,
          keyboardDismissBehavior: ScrollViewKeyboardDismissBehavior.onDrag,
          padding: const EdgeInsets.fromLTRB(20, 8, 20, 24),
          children: [
            Text('Survey #$_id', style: TextStyle(fontSize: 19, fontWeight: FontWeight.w800, color: AppColors.ink)),
            const SizedBox(height: 4),
            Text('Selected ${str(selected['name'])} · detected ${str(asJson(c['detected_product'])['name'], 'nothing')}',
                style: TextStyle(color: AppColors.ink2)),
            const SizedBox(height: 4),
            Text(_evidence(c), style: TextStyle(fontSize: 12.5, color: AppColors.muted)),
            const SizedBox(height: 14),
            if (d == null && _error == null) const Center(child: Padding(padding: EdgeInsets.all(16), child: CircularProgressIndicator())),
            if (d != null && str(d['summary']).isNotEmpty) ...[
              Text('AI summary', style: TextStyle(fontWeight: FontWeight.w700, color: AppColors.ink)),
              const SizedBox(height: 4),
              Text(str(d['summary']), style: TextStyle(height: 1.45, color: AppColors.ink2)),
              const SizedBox(height: 12),
            ],
            if (d != null) ...[
              Text('Transcript', style: TextStyle(fontWeight: FontWeight.w700, color: AppColors.ink)),
              const SizedBox(height: 4),
              Container(
                padding: const EdgeInsets.all(12),
                decoration: BoxDecoration(color: AppColors.bg, borderRadius: BorderRadius.circular(12)),
                child: Text(str(d['transcript'], 'No transcript.'), style: TextStyle(height: 1.45, color: AppColors.ink2)),
              ),
            ],
            if (history.isNotEmpty) ...[
              const SizedBox(height: 12),
              Text('History', style: TextStyle(fontWeight: FontWeight.w700, color: AppColors.ink)),
              for (final h in history)
                Padding(
                  padding: const EdgeInsets.only(top: 4),
                  child: Text('${_when(h['at'])} · ${str(h['by'])}: ${str(h['summary'])}', style: TextStyle(fontSize: 13, color: AppColors.muted)),
                ),
            ],
            const SizedBox(height: 16),
            if (reviewed != null)
              Text('$reviewed by ${str(c['reviewed_by'], 'an admin')}${str(c['remarks']).isNotEmpty ? ' · “${str(c['remarks'])}”' : ''}',
                  style: TextStyle(color: AppColors.pos, fontWeight: FontWeight.w600))
            else ...[
              TextField(
                controller: _remarks,
                textInputAction: TextInputAction.done,
                onSubmitted: (_) => FocusManager.instance.primaryFocus?.unfocus(),
                decoration: const InputDecoration(labelText: 'Remarks (optional)'),
              ),
              const SizedBox(height: 12),
              FilledButton.tonal(
                onPressed: _busy ? null : () => _act('confirm'),
                child: Text('Confirm ${str(selected['name'])}'),
              ),
              const SizedBox(height: 16),
              DropdownButtonFormField<int>(
                initialValue: _products.any((p) => intOf(p['id']) == _correctTo) ? _correctTo : null,
                isExpanded: true,
                decoration: const InputDecoration(labelText: 'Correct product'),
                items: [
                  for (final p in _products.where((p) => intOf(p['id']) != intOf(selected['id'])))
                    DropdownMenuItem(value: intOf(p['id']), child: Text(str(p['name']), overflow: TextOverflow.ellipsis)),
                ],
                onChanged: _busy ? null : (v) => setState(() => _correctTo = v),
              ),
              const SizedBox(height: 8),
              FilledButton(
                onPressed: _busy || _correctTo == null ? null : () => _act('correct'),
                child: const Text('Move survey to this product'),
              ),
              const SizedBox(height: 8),
              OutlinedButton(
                onPressed: _busy ? null : () => _act('invalid'),
                style: OutlinedButton.styleFrom(foregroundColor: AppColors.neg),
                child: const Text('Mark invalid'),
              ),
            ],
            if (_error != null) Padding(padding: const EdgeInsets.only(top: 10), child: Text(_error!, style: TextStyle(color: AppColors.neg))),
          ],
        ),
      ),
    );
  }
}
