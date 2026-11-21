import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../main.dart';
import '../services/api_service.dart' show ApiException;
import '../services/org_api.dart';
import '../services/recording_vault.dart';
import '../widgets/search_dropdown.dart';
import 'org_screens.dart' show EmptyNote, Panel, Pill, showMessage;

/* ============================================================
   Projects (the same backend as the web portal: api/projects.py)
   ============================================================
     Super Admin  /org/projects: list, Add project (with the response target), project page:
                  survey plans (add / edit / delete), Edit response target, Start survey,
                  Delete project (only before consumer feedback; plans first).
     Admin        /admin/projects: read-only, and the team of each survey plan with each
                  person's share of its responses (Allocate team).
   Also the recording passkey chain: the Super Admin sets it and sends it to Admins
   (OrgPasskeyCard); an Admin passes it on to survey persons (AdminPasskeyPanel).
*/

Future<Json> _api(String method, String path, {Object? body}) => OrgApi.request(method, path, body: body);

final _wholeNumber = RegExp(r'^[1-9][0-9]*$');
String? _wholeProblem(String v, String label) {
  final t = v.trim();
  if (t.isEmpty) return '$label: enter a number.';
  if (!_wholeNumber.hasMatch(t)) return '$label must be a whole number from 1 (no decimals, signs or zero).';
  return null;
}

final _digitsOnly = [FilteringTextInputFormatter.digitsOnly];
String _plural(int n, String w) => '$n $w${n == 1 ? '' : 's'}';
String _day(String? iso) {
  final d = DateTime.tryParse(iso ?? '');
  if (d == null) return '';
  const m = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  return '${d.day} ${m[d.month - 1]} ${d.year}';
}

String _iso(DateTime d) => '${d.year}-${d.month.toString().padLeft(2, '0')}-${d.day.toString().padLeft(2, '0')}';

const _statusTone = {'draft': 'muted', 'ongoing': 'ok', 'completed': 'info'};

// ---------------------------------------------------------------- list

class ProjectsPage extends StatefulWidget {
  const ProjectsPage({super.key, required this.admin});

  final bool admin; // Admin: read-only list, opens projects to allocate teams

  @override
  State<ProjectsPage> createState() => _ProjectsPageState();
}

class _ProjectsPageState extends State<ProjectsPage> {
  Json? _data;
  String? _error;
  String _q = '';

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final d = await _api('GET', widget.admin ? '/admin/projects' : '/org/projects');
      if (mounted) setState(() {
        _data = d;
        _error = null;
      });
    } on ApiException catch (e) {
      if (mounted) setState(() => _error = e.message);
    }
  }

  Future<void> _open(int id) async {
    await Navigator.of(context).push(MaterialPageRoute(builder: (_) => ProjectDetailPage(projectId: id, admin: widget.admin)));
    _load();
  }

  @override
  Widget build(BuildContext context) {
    if (_error != null && _data == null) {
      return Center(child: Padding(padding: const EdgeInsets.all(24), child: Text(_error!, textAlign: TextAlign.center)));
    }
    if (_data == null) return const Center(child: CircularProgressIndicator());
    final k = asJson(_data!['kpis']);
    final q = _q.trim().toLowerCase();
    final rows = asJsonList(_data!['projects']).where((p) {
      if (q.isEmpty) return true;
      return [p['project_name'], p['project_code'], asJson(p['category'])['name'], asJson(p['product'])['name'], p['version_label']]
          .any((x) => str(x).toLowerCase().contains(q));
    }).toList();
    return RefreshIndicator(
      onRefresh: _load,
      child: ListView(padding: const EdgeInsets.fromLTRB(16, 12, 16, 96), children: [
        Row(children: [
          Expanded(
            child: Text(widget.admin ? 'Open a project to allocate the team of each survey plan.' : 'One project per product recipe.',
                style: TextStyle(color: AppColors.muted)),
          ),
          if (!widget.admin)
            FilledButton.icon(
              onPressed: () async {
                final id = await Navigator.of(context).push<int>(MaterialPageRoute(builder: (_) => const AddProjectPage()));
                if (id != null) await _open(id);
                _load();
              },
              icon: const Icon(Icons.add),
              label: const Text('Add project'),
            ),
        ]),
        const SizedBox(height: 12),
        Wrap(spacing: 8, runSpacing: 8, children: [
          for (final (label, key, tone) in const [('Total', 'total', 'info'), ('Ongoing', 'ongoing', 'ok'), ('Completed', 'completed', 'info'), ('Draft', 'draft', 'muted')])
            Pill('$label ${intOf(key == 'total' ? k['total'] : k[key])}', tone: tone),
        ]),
        const SizedBox(height: 12),
        TextField(
          decoration: const InputDecoration(prefixIcon: Icon(Icons.search), hintText: 'Search projects, products or recipes'),
          onChanged: (v) => setState(() => _q = v),
        ),
        const SizedBox(height: 12),
        if (rows.isEmpty)
          EmptyNote(icon: Icons.folder_open_outlined, title: 'No projects', text: widget.admin ? "You're notified when the Super Admin creates one." : 'Add a project to study a product recipe.'),
        for (final p in rows)
          Padding(
            padding: const EdgeInsets.only(bottom: 10),
            child: Panel(
              onTap: () => _open(intOf(p['id'])),
              child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                Row(children: [
                  Expanded(child: Text(str(p['project_name']), style: TextStyle(fontWeight: FontWeight.w800, fontSize: 15.5, color: AppColors.ink))),
                  Pill(str(p['status_label']), tone: _statusTone[str(p['status'])] ?? 'muted'),
                ]),
                const SizedBox(height: 4),
                Text('${str(p['project_code'])} · ${str(asJson(p['product'])['name'])} · ${str(p['version_label'])}',
                    style: TextStyle(color: AppColors.muted, fontSize: 13)),
                const SizedBox(height: 6),
                Text(
                  p['live_round'] != null
                      ? 'Survey ${intOf(asJson(p['live_round'])['number'])} · ${intOf(asJson(p['live_round'])['done'])} of ${intOf(asJson(p['live_round'])['target'])}'
                      : intOf(p['rounds']) > 0
                          ? '${_plural(intOf(p['rounds']), 'survey')} done'
                          : 'Not started',
                  style: TextStyle(fontSize: 13, color: AppColors.ink2),
                ),
                if (p['total_target'] != null) Text('Response target: ${intOf(p['total_target'])}', style: TextStyle(fontSize: 13, color: AppColors.ink2)),
              ]),
            ),
          ),
      ]),
    );
  }
}

// ---------------------------------------------------------------- add project (Super Admin)

class AddProjectPage extends StatefulWidget {
  const AddProjectPage({super.key});

  @override
  State<AddProjectPage> createState() => _AddProjectPageState();
}

class _AddProjectPageState extends State<AddProjectPage> {
  final _name = TextEditingController();
  final _description = TextEditingController();
  final _target = TextEditingController();
  List<Json>? _categories;
  int? _category;
  int? _product;
  int? _version;
  bool _busy = false;
  String? _error;

  @override
  void initState() {
    super.initState();
    _api('GET', '/org/projects/options').then((d) {
      if (mounted) setState(() => _categories = asJsonList(d['categories']));
    }).catchError((e) {
      if (mounted) setState(() => _error = '$e');
    });
  }

  @override
  void dispose() {
    _name.dispose();
    _description.dispose();
    _target.dispose();
    super.dispose();
  }

  List<Json> get _products => _category == null
      ? const []
      : asJsonList((_categories ?? []).firstWhere((c) => intOf(c['id']) == _category, orElse: () => <String, dynamic>{})['products']);
  List<int> get _versions => _product == null
      ? const []
      : ((_products.firstWhere((p) => intOf(p['id']) == _product, orElse: () => <String, dynamic>{})['versions'] as List?) ?? const [])
          .map((v) => intOf(v))
          .toList();

  Future<void> _create() async {
    final problem = _name.text.trim().length < 2
        ? 'Enter the project name.'
        : _product == null || _version == null
            ? 'Choose the category, product and recipe.'
            : _wholeProblem(_target.text, 'Total consumer responses');
    if (problem != null) return setState(() => _error = problem);
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      final p = await _api('POST', '/org/projects', body: {
        'project_name': _name.text.trim(),
        'description': _description.text.trim().isEmpty ? null : _description.text.trim(),
        'category_id': _category,
        'product_id': _product,
        'product_version': _version,
        'total_target': int.parse(_target.text.trim()),
      });
      if (mounted) Navigator.of(context).pop(intOf(p['id']));
    } on ApiException catch (e) {
      if (mounted) setState(() {
        _busy = false;
        _error = e.message;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: AppColors.bg,
      appBar: AppBar(title: const Text('Add a project')),
      body: _categories == null && _error == null
          ? const Center(child: CircularProgressIndicator())
          : ListView(padding: const EdgeInsets.all(16), children: [
              TextField(controller: _name, decoration: const InputDecoration(labelText: 'Project name *', hintText: 'e.g. VitaBoost launch study')),
              const SizedBox(height: 10),
              TextField(controller: _description, minLines: 2, maxLines: 4, decoration: const InputDecoration(labelText: 'Description (optional)')),
              const SizedBox(height: 10),
              SearchDropdownField<int>(
                value: _category,
                decoration: const InputDecoration(labelText: 'Category *'),
                items: [
                  for (final c in _categories ?? const <Json>[])
                    DropdownMenuItem(value: intOf(c['id']), enabled: asJsonList(c['products']).isNotEmpty, child: Text(str(c['name']))),
                ],
                onChanged: (v) => setState(() {
                  _category = v;
                  _product = null;
                  _version = null;
                }),
              ),
              const SizedBox(height: 10),
              SearchDropdownField<int>(
                value: _product,
                decoration: const InputDecoration(labelText: 'Product *'),
                items: [for (final p in _products) DropdownMenuItem(value: intOf(p['id']), child: Text(str(p['name'])))],
                onChanged: (v) => setState(() {
                  _product = v;
                  final vs = _versions;
                  _version = vs.isEmpty ? null : vs.last;
                }),
              ),
              const SizedBox(height: 10),
              SearchDropdownField<int>(
                value: _version,
                decoration: const InputDecoration(labelText: 'Recipe *'),
                items: [for (final v in _versions) DropdownMenuItem(value: v, child: Text('Recipe v$v'))],
                onChanged: (v) => setState(() => _version = v),
              ),
              const SizedBox(height: 4),
              Text('Only recipes without a project are listed.', style: TextStyle(fontSize: 12.5, color: AppColors.muted)),
              const SizedBox(height: 12),
              SizedBox(
                width: 220,
                child: TextField(
                  controller: _target,
                  keyboardType: TextInputType.number,
                  inputFormatters: _digitsOnly,
                  decoration: const InputDecoration(labelText: 'Total consumer responses *', hintText: 'e.g. 100', helperText: 'The survey plans share this target.'),
                ),
              ),
              if (_error != null) ...[const SizedBox(height: 12), Text(_error!, style: TextStyle(color: AppColors.neg))],
            ]),
      bottomNavigationBar: SafeArea(
        minimum: const EdgeInsets.fromLTRB(16, 8, 16, 12),
        child: FilledButton(onPressed: _busy ? null : _create, child: Text(_busy ? 'Creating…' : 'Create project')),
      ),
    );
  }
}

// ---------------------------------------------------------------- project page

class ProjectDetailPage extends StatefulWidget {
  const ProjectDetailPage({super.key, required this.projectId, required this.admin});

  final int projectId;
  final bool admin;

  @override
  State<ProjectDetailPage> createState() => _ProjectDetailPageState();
}

class _ProjectDetailPageState extends State<ProjectDetailPage> {
  Json? _project;
  Json? _plan;
  String? _error;

  String get _base => widget.admin ? '/admin/projects/${widget.projectId}' : '/org/projects/${widget.projectId}';

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final r = await Future.wait([_api('GET', _base), _api('GET', '$_base/plans')]);
      if (mounted) setState(() {
        _project = r[0];
        _plan = r[1];
        _error = null;
      });
    } on ApiException catch (e) {
      if (mounted) setState(() => _error = e.message);
    }
  }

  Future<void> _run(Future<void> Function() work, String ok) async {
    try {
      await work();
      if (mounted) showMessage(context, ok);
      _load();
    } on ApiException catch (e) {
      if (mounted) showMessage(context, e.message, error: true);
    }
  }

  Future<void> _editPlan([Json? plan]) async {
    final saved = await showModalBottomSheet<bool>(
      context: context,
      isScrollControlled: true,
      useSafeArea: true,
      builder: (_) => _PlanEditorSheet(projectId: widget.projectId, plan: plan, limit: _limitFor(plan)),
    );
    if (saved == true) _load();
  }

  int? _limitFor(Json? p) {
    final room = _plan?['available'] ?? _plan?['total_target'];
    if (room == null) return null;
    return (intOf(room) - intOf(_plan?['planned']) + intOf(p?['target'])).clamp(0, 100000);
  }

  Future<void> _deletePlan(Json p) async {
    final ok = await confirmAction(context, title: 'Delete “${str(p['name'])}”?', message: 'Its team allocation is removed too.');
    if (ok) await _run(() => _api('DELETE', '/action-plans/${intOf(p['id'])}'), 'Plan deleted.');
  }

  Future<void> _deleteProject() async {
    final plans = asJsonList(_plan?['plans']);
    if (plans.isNotEmpty) {
      await showDialog<void>(
        context: context,
        builder: (ctx) => AlertDialog(
          title: Text(plans.length == 1 ? 'Delete the active survey plan first' : 'Delete the active survey plans first'),
          content: Text('To delete ${str(_project?['project_code'])}, first delete its active survey plan${plans.length == 1 ? '' : 's'}: '
              '${plans.map((p) => str(p['name'])).join(', ')}. Then delete the project.'),
          actions: [FilledButton(onPressed: () => Navigator.of(ctx).pop(), child: const Text('OK'))],
        ),
      );
      return;
    }
    final ok = await confirmAction(context,
        title: 'Delete ${str(_project?['project_code'])}?',
        message: '“${str(_project?['project_name'])}” is deleted. The product and its recipe are not changed.',
        confirmLabel: 'Delete project');
    if (!ok) return;
    try {
      await _api('DELETE', '/org/projects/${widget.projectId}');
      if (mounted) Navigator.of(context).pop();
    } on ApiException catch (e) {
      if (mounted) showMessage(context, e.message, error: true);
    }
  }

  Future<void> _editTarget() async {
    final c = TextEditingController(text: _plan?['total_target'] == null ? '' : '${intOf(_plan!['total_target'])}');
    final value = await showDialog<int>(
      context: context,
      builder: (ctx) {
        String? err;
        return StatefulBuilder(builder: (ctx, setD) {
          return AlertDialog(
            title: const Text('Edit response target'),
            content: Column(mainAxisSize: MainAxisSize.min, crossAxisAlignment: CrossAxisAlignment.start, children: [
              Text('${str(_project?['project_code'])} · ${str(asJson(_project?['product'])['name'])} recipe v${intOf(_project?['product_version'])}',
                  style: TextStyle(fontSize: 13, color: AppColors.muted)),
              const SizedBox(height: 10),
              TextField(controller: c, autofocus: true, keyboardType: TextInputType.number, inputFormatters: _digitsOnly,
                  decoration: InputDecoration(labelText: 'Response target', errorText: err)),
            ]),
            actions: [
              TextButton(onPressed: () => Navigator.of(ctx).pop(), child: const Text('Cancel')),
              FilledButton(
                onPressed: () {
                  final p = _wholeProblem(c.text, 'The response target');
                  if (p != null) return setD(() => err = p);
                  Navigator.of(ctx).pop(int.parse(c.text.trim()));
                },
                child: const Text('Save target'),
              ),
            ],
          );
        });
      },
    );
    if (value == null) return;
    try {
      final r = await _api('PUT', '/org/projects/${widget.projectId}/target', body: {'total_target': value});
      if (!mounted) return;
      final re = asJson(r['reopened']);
      showMessage(context, re.isEmpty ? 'Response target saved.' : 'Survey ${intOf(re['number'])} reopened: now ${intOf(re['target'])} consumers.');
      _load();
    } on ApiException catch (e) {
      if (mounted) showMessage(context, e.message, error: true);
    }
  }

  Future<void> _start() async {
    final number = intOf(_plan?['next_number'], intOf(asJson(_plan?['round'])['number']) + 1);
    final ok = await confirmAction(context,
        title: 'Start Survey $number?',
        message: '${intOf(_plan?['target'])} consumers across ${_plural(asJsonList(_plan?['plans']).length, 'plan')}. '
            'The Admin allocates the team afterwards; only allocated survey persons collect.',
        confirmLabel: 'Start survey',
        cancelLabel: 'Not now');
    if (ok) await _run(() => _api('POST', '/org/projects/${widget.projectId}/start', body: {}), 'Survey $number started.');
  }

  Future<void> _allocate(Json p) async {
    final saved = await showModalBottomSheet<bool>(
      context: context,
      isScrollControlled: true,
      useSafeArea: true,
      builder: (_) => _AllocateSheet(plan: p, plans: asJsonList(_plan?['plans'])),
    );
    if (saved == true) _load();
  }

  @override
  Widget build(BuildContext context) {
    final p = _project;
    final plan = _plan;
    return Scaffold(
      backgroundColor: AppColors.bg,
      appBar: AppBar(
        title: Text(p == null ? 'Project' : str(p['project_code'])),
        actions: [
          if (!widget.admin && p != null && p['can_delete'] == true)
            IconButton(tooltip: 'Delete project', onPressed: _deleteProject, icon: const Icon(Icons.delete_outline)),
        ],
      ),
      body: p == null || plan == null
          ? Center(child: _error != null ? Padding(padding: const EdgeInsets.all(24), child: Text(_error!)) : const CircularProgressIndicator())
          : RefreshIndicator(
              onRefresh: _load,
              child: ListView(padding: const EdgeInsets.fromLTRB(16, 12, 16, 32), children: _body(p, plan)),
            ),
    );
  }

  List<Widget> _body(Json p, Json plan) {
    final live = str(plan['stage']) == 'live';
    final closed = str(plan['stage']) == 'ended' && plan['can_edit'] == true && plan['can_add_plans'] == false;
    final plans = asJsonList(plan['plans']);
    final total = plan['total_target'];
    final used = intOf(plan['used']);
    final room = plan['available'] ?? total;
    final canEdit = !widget.admin && plan['can_edit'] == true;
    final round = asJson(plan['round']);
    return [
      Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Expanded(child: Text(str(p['project_name']), style: TextStyle(fontFamily: kDisplayFont, fontSize: 21, fontWeight: FontWeight.w800, color: AppColors.ink))),
        Pill(str(p['status_label']), tone: _statusTone[str(p['status'])] ?? 'muted'),
      ]),
      const SizedBox(height: 4),
      Text('${str(asJson(p['category'])['name'])} · ${str(asJson(p['product'])['name'])} · recipe v${intOf(p['product_version'])}',
          style: TextStyle(color: AppColors.muted)),
      if (widget.admin) ...[const SizedBox(height: 6), const Pill('Read only', tone: 'muted')],
      if (str(p['description']).isNotEmpty) ...[const SizedBox(height: 10), Text(str(p['description']), style: TextStyle(color: AppColors.ink2))],
      const SizedBox(height: 16),
      Row(children: [
        Text('Survey plan', style: TextStyle(fontWeight: FontWeight.w800, fontSize: 17, color: AppColors.ink)),
        const SizedBox(width: 8),
        if (round.isNotEmpty) Pill('Survey ${intOf(round['number'])} · ${live ? 'collecting' : 'completed'}', tone: live ? 'ok' : 'info'),
      ]),
      const SizedBox(height: 6),
      if (closed)
        Text('Survey ${intOf(round['number'])} is completed. To survey again, set the response target above ${intOf(plan['reopen_above'], used)} '
            '(Edit response target): Survey ${intOf(round['number'])} reopens for the extra responses.', style: TextStyle(color: AppColors.ink2)),
      const SizedBox(height: 8),
      Panel(
        color: AppColors.brandSoft,
        padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
        child: Text(
          total == null
              ? 'No response target set yet.'
              : 'Response target: ${intOf(total)}'
                  '${used > 0 ? ' · Survey ${intOf(round['number'])} collected $used' : ''}'
                  '${closed ? '' : ' · Planned ${intOf(plan['planned'])} · ${room == null ? '' : '${intOf(room) - intOf(plan['planned'])} left'}'}',
          style: TextStyle(color: AppColors.ink, fontWeight: FontWeight.w600),
        ),
      ),
      if (canEdit) ...[
        const SizedBox(height: 10),
        Wrap(spacing: 8, runSpacing: 8, children: [
          OutlinedButton.icon(onPressed: _editTarget, icon: const Icon(Icons.track_changes_outlined), label: const Text('Edit response target')),
          if (!closed)
            FilledButton.icon(
              onPressed: room != null && (_limitFor(null) ?? 1) <= 0 ? null : () => _editPlan(),
              icon: const Icon(Icons.add),
              label: const Text('Add survey plan'),
            ),
        ]),
      ],
      const SizedBox(height: 12),
      for (final pl in plans)
        Padding(
          padding: const EdgeInsets.only(bottom: 10),
          child: PlanCardView(
            plan: pl,
            onEdit: canEdit ? () => _editPlan(pl) : null,
            onDelete: canEdit && intOf(pl['done']) == 0 ? () => _deletePlan(pl) : null,
            onAllocate: widget.admin && plan['can_allocate'] == true ? () => _allocate(pl) : null,
          ),
        ),
      if (plans.isEmpty && !closed) Text('No survey plan yet.', style: TextStyle(color: AppColors.muted)),
      if (!widget.admin && !live && !closed) ...[
        const SizedBox(height: 8),
        Panel(
          color: AppColors.brandSoft,
          child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
            Text(str(plan['start_problem'], 'Ready: ${intOf(plan['target'])} consumers across ${_plural(plans.length, 'plan')}.'),
                style: TextStyle(color: AppColors.ink)),
            const SizedBox(height: 10),
            FilledButton(
              onPressed: plan['can_start'] == true ? _start : null,
              child: Text('Start Survey ${intOf(plan['next_number'], intOf(round['number']) + 1)}'),
            ),
          ]),
        ),
      ],
      if (live && !widget.admin) ...[
        const SizedBox(height: 8),
        Text('Survey ${intOf(round['number'])} is collecting. End it from the product\'s Surveys tab.', style: TextStyle(color: AppColors.muted)),
      ],
    ];
  }
}

// ---------------------------------------------------------------- plan card

class PlanCardView extends StatelessWidget {
  const PlanCardView({super.key, required this.plan, this.onEdit, this.onDelete, this.onAllocate});

  final Json plan;
  final VoidCallback? onEdit;
  final VoidCallback? onDelete;
  final VoidCallback? onAllocate;

  @override
  Widget build(BuildContext context) {
    final p = plan;
    final collecting = p['round_id'] != null;
    final target = intOf(p['target']);
    final done = intOf(p['done']);
    final members = asJsonList(p['members']);
    final cities = (p['cities'] as List?)?.map((c) => '$c').toList() ?? const <String>[];
    final allocated = intOf(p['allocated']);
    final complete = p['allocation_complete'] == true;
    final (state, tone) = !collecting ? ('Planned', 'muted') : (done >= target ? ('Done', 'info') : ('Collecting', 'ok'));
    final dates = p['start_date'] != null || p['end_date'] != null ? '${_day(p['start_date']) == '' ? '…' : _day(p['start_date'])} – ${_day(p['end_date']) == '' ? '…' : _day(p['end_date'])}' : 'No dates set';
    Widget meta(IconData icon, Color color, String label, Widget value) => Padding(
          padding: const EdgeInsets.only(bottom: 6),
          child: Row(children: [
            Container(
              width: 26,
              height: 26,
              decoration: BoxDecoration(color: color.withValues(alpha: 0.13), borderRadius: BorderRadius.circular(7)),
              child: Icon(icon, size: 15, color: color),
            ),
            const SizedBox(width: 8),
            SizedBox(width: 72, child: Text(label, style: TextStyle(fontSize: 11, fontWeight: FontWeight.w700, letterSpacing: 0.4, color: AppColors.muted))),
            Expanded(child: value),
          ]),
        );
    return Panel(
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Expanded(
            child: Wrap(spacing: 8, runSpacing: 4, crossAxisAlignment: WrapCrossAlignment.center, children: [
              Text(str(p['name']), style: TextStyle(fontWeight: FontWeight.w800, fontSize: 16, color: AppColors.ink)),
              Pill(state, tone: tone),
            ]),
          ),
          Column(crossAxisAlignment: CrossAxisAlignment.end, children: [
            Text('${collecting ? done : target}', style: TextStyle(fontFamily: kDisplayFont, fontSize: 22, fontWeight: FontWeight.w800, color: AppColors.brand)),
            Text(collecting ? 'of $target responses' : 'responses', style: TextStyle(fontSize: 11.5, color: AppColors.muted)),
          ]),
        ]),
        if (collecting) ...[
          const SizedBox(height: 6),
          ClipRRect(
            borderRadius: BorderRadius.circular(4),
            child: LinearProgressIndicator(value: target == 0 ? 0 : (done / target).clamp(0, 1).toDouble(), minHeight: 6),
          ),
        ],
        const SizedBox(height: 10),
        meta(Icons.calendar_month_outlined, const Color(0xFF4F46E5), 'DATES', Text(dates, style: TextStyle(fontWeight: FontWeight.w600, color: AppColors.ink))),
        meta(Icons.groups_2_outlined, const Color(0xFF0D9488), 'AUDIENCE',
            Text(p['audience'] == 'internal' ? 'Internal only' : p['audience'] == 'external' ? 'External only' : 'Internal and external',
                style: TextStyle(fontWeight: FontWeight.w600, color: AppColors.ink))),
        meta(Icons.place_outlined, const Color(0xFFDB2777), 'CITIES',
            cities.isEmpty ? Text('Any city', style: TextStyle(color: AppColors.muted)) : Wrap(spacing: 6, runSpacing: 4, children: [for (final c in cities) Pill(c, tone: 'neg')])),
        const SizedBox(height: 4),
        Row(children: [
          Text('Team', style: TextStyle(fontWeight: FontWeight.w800, color: AppColors.ink)),
          const Spacer(),
          Pill(members.isEmpty ? 'Not allocated yet' : complete ? '$allocated of $target shared' : '$allocated of $target shared · ${intOf(p['unallocated'])} left',
              tone: members.isNotEmpty && complete ? 'ok' : 'warn'),
        ]),
        const SizedBox(height: 4),
        for (final m in members)
          Padding(
            padding: const EdgeInsets.symmetric(vertical: 2),
            child: Row(children: [
              Expanded(child: Text('${str(m['name'])} · ${(m['cities'] as List?)?.isEmpty ?? true ? 'any city' : (m['cities'] as List).join(', ')}',
                  style: TextStyle(color: AppColors.ink2))),
              Text(m['target'] == null ? 'no share' : '${intOf(m['done'])} / ${intOf(m['target'])}',
                  style: TextStyle(fontWeight: FontWeight.w700, color: AppColors.brand)),
            ]),
          ),
        if (members.isEmpty) Text('The Admin allocates the survey persons for this plan.', style: TextStyle(fontSize: 13, color: AppColors.muted)),
        if (str(p['notes']).isNotEmpty) ...[
          const SizedBox(height: 6),
          Text(str(p['notes']), maxLines: 2, overflow: TextOverflow.ellipsis, style: TextStyle(fontSize: 12.5, color: AppColors.ink2)),
        ],
        if (onEdit != null || onDelete != null || onAllocate != null) ...[
          const Divider(height: 20),
          Row(mainAxisAlignment: MainAxisAlignment.end, children: [
            if (onAllocate != null) FilledButton(onPressed: onAllocate, child: Text(members.isEmpty ? 'Allocate team' : 'Change team')),
            if (onEdit != null) ...[const SizedBox(width: 8), OutlinedButton(onPressed: onEdit, child: const Text('Edit'))],
            if (onDelete != null) ...[
              const SizedBox(width: 8),
              OutlinedButton(onPressed: onDelete, style: OutlinedButton.styleFrom(foregroundColor: AppColors.neg), child: const Text('Delete')),
            ],
          ]),
        ],
      ]),
    );
  }
}

// ---------------------------------------------------------------- plan editor (Super Admin)

class _PlanEditorSheet extends StatefulWidget {
  const _PlanEditorSheet({required this.projectId, this.plan, this.limit});

  final int projectId;
  final Json? plan;
  final int? limit;

  @override
  State<_PlanEditorSheet> createState() => _PlanEditorSheetState();
}

class _PlanEditorSheetState extends State<_PlanEditorSheet> {
  late final _name = TextEditingController(text: str(widget.plan?['name']));
  late final _target = TextEditingController(text: widget.plan == null ? '' : '${intOf(widget.plan!['target'])}');
  late final _notes = TextEditingController(text: str(widget.plan?['notes']));
  final _city = TextEditingController();
  late String? _start = widget.plan?['start_date'] as String?;
  late String? _end = widget.plan?['end_date'] as String?;
  late String _audience = str(widget.plan?['audience']);
  late final List<String> _cities = [...((widget.plan?['cities'] as List?)?.map((c) => '$c') ?? const <String>[])];
  bool _busy = false;
  String? _error;

  @override
  void dispose() {
    for (final c in [_name, _target, _notes, _city]) {
      c.dispose();
    }
    super.dispose();
  }

  Future<void> _pick(bool start) async {
    final today = DateTime.now();
    final first = DateTime(today.year, today.month, today.day);
    final from = !start && _start != null && DateTime.parse(_start!).isAfter(first) ? DateTime.parse(_start!) : first;
    final current = DateTime.tryParse((start ? _start : _end) ?? '');
    final picked = await showDatePicker(
      context: context,
      firstDate: from, // earlier dates can't be chosen
      lastDate: DateTime(today.year + 5),
      initialDate: current != null && !current.isBefore(from) ? current : from,
    );
    if (picked == null) return;
    setState(() {
      if (start) {
        _start = _iso(picked);
        if (_end != null && _end!.compareTo(_start!) < 0) _end = null;
      } else {
        _end = _iso(picked);
      }
    });
  }

  void _addCity() {
    final c = _city.text.trim().replaceAll(RegExp(r'\s+'), ' ');
    if (c.isNotEmpty && !_cities.any((x) => x.toLowerCase() == c.toLowerCase())) setState(() => _cities.add(c));
    _city.clear();
  }

  Future<void> _save() async {
    if (_city.text.trim().isNotEmpty) _addCity();
    String? problem = _name.text.trim().length < 2 ? 'Give the plan a name, e.g. “Delhi team”.' : _wholeProblem(_target.text, 'Consumer responses');
    if (problem == null && widget.limit != null && int.parse(_target.text.trim()) > widget.limit!) {
      problem = 'This plan can have at most ${widget.limit} consumers: raise the response target first.';
    }
    if (problem != null) return setState(() => _error = problem);
    setState(() {
      _busy = true;
      _error = null;
    });
    final body = {
      'name': _name.text.trim(),
      'target': int.parse(_target.text.trim()),
      'start_date': _start,
      'end_date': _end,
      'cities': _cities,
      'audience': _audience.isEmpty ? null : _audience,
      'notes': _notes.text.trim().isEmpty ? null : _notes.text.trim(),
    };
    try {
      if (widget.plan == null) {
        await _api('POST', '/org/projects/${widget.projectId}/plans', body: body);
      } else {
        await _api('PUT', '/action-plans/${intOf(widget.plan!['id'])}', body: body);
      }
      if (mounted) Navigator.of(context).pop(true);
    } on ApiException catch (e) {
      if (mounted) setState(() {
        _busy = false;
        _error = e.message;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    Widget dateBox(String label, String? value, bool start) => Expanded(
          child: InkWell(
            onTap: () => _pick(start),
            child: InputDecorator(
              decoration: InputDecoration(labelText: label, prefixIcon: const Icon(Icons.calendar_month_outlined, size: 18)),
              child: Text(value == null ? 'Choose' : _day(value), style: TextStyle(color: value == null ? AppColors.muted : AppColors.ink)),
            ),
          ),
        );
    return Padding(
      padding: EdgeInsets.only(bottom: MediaQuery.of(context).viewInsets.bottom),
      child: ListView(shrinkWrap: true, padding: const EdgeInsets.fromLTRB(16, 16, 16, 24), children: [
        Text(widget.plan == null ? 'Add a survey plan' : 'Edit “${str(widget.plan!['name'])}”',
            style: TextStyle(fontWeight: FontWeight.w800, fontSize: 18, color: AppColors.ink)),
        if (widget.limit != null)
          Padding(padding: const EdgeInsets.only(top: 4), child: Text('Up to ${widget.limit} consumer responses for this plan.', style: TextStyle(color: AppColors.muted, fontSize: 13))),
        const SizedBox(height: 12),
        TextField(controller: _name, decoration: const InputDecoration(labelText: 'Plan name *', hintText: 'e.g. Delhi team')),
        const SizedBox(height: 10),
        Row(children: [
          Expanded(
            child: TextField(controller: _target, keyboardType: TextInputType.number, inputFormatters: _digitsOnly,
                decoration: const InputDecoration(labelText: 'Consumer responses *', hintText: 'e.g. 30')),
          ),
          const SizedBox(width: 10),
          Expanded(
            child: SearchDropdownField<String>(
              value: _audience,
              decoration: const InputDecoration(labelText: 'Audience'),
              items: const [
                DropdownMenuItem(value: '', child: Text('Internal and external')),
                DropdownMenuItem(value: 'internal', child: Text('Internal only')),
                DropdownMenuItem(value: 'external', child: Text('External only')),
              ],
              onChanged: (v) => setState(() => _audience = v ?? ''),
            ),
          ),
        ]),
        const SizedBox(height: 10),
        Row(children: [dateBox('Start date', _start, true), const SizedBox(width: 10), dateBox('End date', _end, false)]),
        const SizedBox(height: 10),
        TextField(
          controller: _city,
          textCapitalization: TextCapitalization.words,
          onSubmitted: (_) => _addCity(),
          decoration: InputDecoration(labelText: 'Cities (empty = anywhere)', suffixIcon: IconButton(onPressed: _addCity, icon: const Icon(Icons.add))),
        ),
        if (_cities.isNotEmpty)
          Padding(
            padding: const EdgeInsets.only(top: 6),
            child: Wrap(spacing: 6, runSpacing: 6, children: [
              for (final c in _cities) InputChip(label: Text(c), onDeleted: () => setState(() => _cities.remove(c))),
            ]),
          ),
        const SizedBox(height: 10),
        TextField(controller: _notes, minLines: 2, maxLines: 4, decoration: const InputDecoration(labelText: 'Other requirements')),
        if (_error != null) ...[const SizedBox(height: 10), Text(_error!, style: TextStyle(color: AppColors.neg))],
        const SizedBox(height: 14),
        FilledButton(onPressed: _busy ? null : _save, child: Text(_busy ? 'Saving…' : widget.plan == null ? 'Add plan' : 'Save plan')),
      ]),
    );
  }
}

// ---------------------------------------------------------------- team allocation (Admin)

class _AllocateSheet extends StatefulWidget {
  const _AllocateSheet({required this.plan, required this.plans});

  final Json plan;
  final List<Json> plans;

  @override
  State<_AllocateSheet> createState() => _AllocateSheetState();
}

class _AllocateSheetState extends State<_AllocateSheet> {
  List<Json>? _people;
  late final Map<int, List<String>> _chosen = {
    for (final m in asJsonList(widget.plan['members'])) intOf(m['user_id']): [...((m['cities'] as List?)?.map((c) => '$c') ?? const <String>[])],
  };
  late final Map<int, TextEditingController> _shares = {
    for (final m in asJsonList(widget.plan['members'])) intOf(m['user_id']): TextEditingController(text: m['target'] == null ? '' : '${intOf(m['target'])}'),
  };
  late final Map<int, int> _done = {for (final m in asJsonList(widget.plan['members'])) intOf(m['user_id']): intOf(m['done'])};
  List<Json>? _history;
  bool _busy = false;
  String? _error;

  int get _target => intOf(widget.plan['target']);
  List<String> get _planCities => (widget.plan['cities'] as List?)?.map((c) => '$c').toList() ?? const [];

  @override
  void initState() {
    super.initState();
    _api('GET', '/admin/users').then((d) {
      if (mounted) setState(() => _people = asJsonList(d['users']).where((u) => u['role'] == 'executive' && u['is_active'] != false).toList());
    }).catchError((e) {
      if (mounted) setState(() => _error = '$e');
    });
  }

  @override
  void dispose() {
    for (final c in _shares.values) {
      c.dispose();
    }
    super.dispose();
  }

  int get _allocated => _chosen.keys.fold(0, (s, id) => s + (int.tryParse(_shares[id]?.text.trim() ?? '') ?? 0));

  void _toggle(int id) => setState(() {
        if (_chosen.containsKey(id)) {
          _chosen.remove(id);
        } else {
          _chosen[id] = [..._planCities];
          _shares.putIfAbsent(id, TextEditingController.new);
        }
      });

  void _split() => setState(() {
        final ids = _chosen.keys.toList();
        if (ids.isEmpty) return;
        final each = _target ~/ ids.length;
        var extra = _target - each * ids.length;
        for (final id in ids) {
          _shares[id]!.text = '${each + (extra-- > 0 ? 1 : 0)}';
        }
      });

  String? get _problem {
    for (final id in _chosen.keys) {
      final name = str((_people ?? const <Json>[]).firstWhere((u) => intOf(u['id']) == id, orElse: () => <String, dynamic>{})['name'], 'Each person');
      final p = _wholeProblem(_shares[id]?.text ?? '', 'Responses for $name');
      if (p != null) return p;
      if (int.parse(_shares[id]!.text.trim()) < (_done[id] ?? 0)) return '$name already submitted ${_done[id]}: give them at least ${_done[id]}.';
      if (_planCities.isNotEmpty && (_chosen[id] ?? const []).isEmpty) return 'Give $name at least one city.';
    }
    if (_allocated > _target) return "That's ${_allocated - _target} more than the plan's $_target.";
    return null;
  }

  Future<void> _save() async {
    final p = _problem;
    if (p != null) return setState(() => _error = p);
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      await _api('PUT', '/admin/action-plans/${intOf(widget.plan['id'])}/members', body: {
        'members': [
          for (final e in _chosen.entries) {'user_id': e.key, 'cities': e.value, 'target': int.parse(_shares[e.key]!.text.trim())},
        ],
      });
      if (mounted) {
        showMessage(context, 'Team saved for “${str(widget.plan['name'])}”.');
        Navigator.of(context).pop(true);
      }
    } on ApiException catch (e) {
      if (mounted) setState(() {
        _busy = false;
        _error = e.message;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final remaining = _target - _allocated;
    final elsewhere = <int, List<String>>{};
    for (final p in widget.plans.where((p) => p['id'] != widget.plan['id'])) {
      for (final m in asJsonList(p['members'])) {
        elsewhere.putIfAbsent(intOf(m['user_id']), () => []).add(str(p['name']));
      }
    }
    return Padding(
      padding: EdgeInsets.only(bottom: MediaQuery.of(context).viewInsets.bottom),
      child: ListView(shrinkWrap: true, padding: const EdgeInsets.fromLTRB(16, 16, 16, 24), children: [
        Text('Team for “${str(widget.plan['name'])}”', style: TextStyle(fontWeight: FontWeight.w800, fontSize: 18, color: AppColors.ink)),
        const SizedBox(height: 8),
        Container(
          padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
          decoration: BoxDecoration(
            color: remaining == 0 && _chosen.isNotEmpty ? AppColors.posSoft : remaining < 0 ? AppColors.negSoft : AppColors.bg,
            borderRadius: BorderRadius.circular(10),
          ),
          child: Row(children: [
            Text('Required $_target  ·  Allocated $_allocated  ·  Remaining $remaining', style: TextStyle(fontWeight: FontWeight.w700, color: AppColors.ink)),
            const Spacer(),
            if (_chosen.isNotEmpty) TextButton(onPressed: _split, child: const Text('Split evenly')),
          ]),
        ),
        const SizedBox(height: 8),
        if (_people == null && _error == null) const Center(child: Padding(padding: EdgeInsets.all(16), child: CircularProgressIndicator())),
        for (final u in _people ?? const <Json>[])
          Container(
            margin: const EdgeInsets.only(bottom: 6),
            padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
            decoration: BoxDecoration(border: Border.all(color: AppColors.line), borderRadius: BorderRadius.circular(10)),
            child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Row(children: [
                Checkbox(value: _chosen.containsKey(intOf(u['id'])), onChanged: (_) => _toggle(intOf(u['id']))),
                Expanded(
                  child: Text.rich(TextSpan(children: [
                    TextSpan(text: str(u['name']), style: const TextStyle(fontWeight: FontWeight.w700)),
                    if (elsewhere[intOf(u['id'])] != null)
                      TextSpan(text: ' · also in ${elsewhere[intOf(u['id'])]!.join(', ')}', style: TextStyle(fontSize: 12, color: AppColors.muted)),
                  ])),
                ),
                if (_chosen.containsKey(intOf(u['id'])))
                  SizedBox(
                    width: 74,
                    child: TextField(
                      controller: _shares[intOf(u['id'])],
                      keyboardType: TextInputType.number,
                      inputFormatters: _digitsOnly,
                      onChanged: (_) => setState(() {}),
                      decoration: InputDecoration(isDense: true, labelText: (_done[intOf(u['id'])] ?? 0) > 0 ? '${_done[intOf(u['id'])]} done' : 'Responses'),
                    ),
                  ),
              ]),
              if (_chosen.containsKey(intOf(u['id'])) && _planCities.isNotEmpty)
                Padding(
                  padding: const EdgeInsets.fromLTRB(44, 0, 0, 6),
                  child: Wrap(spacing: 6, children: [
                    for (final c in _planCities)
                      FilterChip(
                        label: Text(c),
                        selected: _chosen[intOf(u['id'])]!.contains(c),
                        onSelected: (on) => setState(() => on ? _chosen[intOf(u['id'])]!.add(c) : _chosen[intOf(u['id'])]!.remove(c)),
                      ),
                  ]),
                ),
            ]),
          ),
        if (_people != null && _people!.isEmpty) Text('No active survey persons.', style: TextStyle(color: AppColors.muted)),
        if (_error != null || (_chosen.isNotEmpty && _problem != null))
          Padding(padding: const EdgeInsets.only(top: 6), child: Text(_error ?? _problem!, style: TextStyle(color: AppColors.neg))),
        ExpansionTile(
          tilePadding: EdgeInsets.zero,
          title: const Text('Allocation history', style: TextStyle(fontWeight: FontWeight.w600, fontSize: 14)),
          onExpansionChanged: (open) {
            if (open && _history == null) {
              _api('GET', '/admin/action-plans/${intOf(widget.plan['id'])}/allocation-history').then((d) {
                if (mounted) setState(() => _history = asJsonList(d['history']));
              }).catchError((_) {
                if (mounted) setState(() => _history = const []);
              });
            }
          },
          children: [
            if (_history == null) const Padding(padding: EdgeInsets.all(8), child: Text('Loading…')),
            if (_history != null && _history!.isEmpty) const Padding(padding: EdgeInsets.all(8), child: Text('No changes yet.')),
            for (final h in _history ?? const <Json>[])
              Align(
                alignment: Alignment.centerLeft,
                child: Padding(
                  padding: const EdgeInsets.only(bottom: 4),
                  child: Text('${str(h['member'])}: ${h['old'] ?? '—'} → ${h['new'] ?? 'removed'} · ${str(h['by'], '—')} · ${_day(h['at'] as String?)}',
                      style: const TextStyle(fontSize: 12.5)),
                ),
              ),
          ],
        ),
        const SizedBox(height: 8),
        FilledButton(onPressed: _busy || _people == null ? null : _save, child: Text(_busy ? 'Saving…' : 'Save team')),
      ]),
    );
  }
}

// ---------------------------------------------------------------- recording passkey chain

/// Super Admin: set or change the recording passkey and send it to the Admins.
class OrgPasskeyCard extends StatefulWidget {
  const OrgPasskeyCard({super.key});

  @override
  State<OrgPasskeyCard> createState() => _OrgPasskeyCardState();
}

class _OrgPasskeyCardState extends State<OrgPasskeyCard> {
  Json? _status;
  final _draft = TextEditingController();
  String? _fresh;
  bool _editing = false;
  bool _busy = false;

  @override
  void initState() {
    super.initState();
    _load();
  }

  @override
  void dispose() {
    _draft.dispose();
    super.dispose();
  }

  Future<void> _load() async {
    try {
      final s = await _api('GET', '/org/recording-passkey');
      if (mounted) setState(() => _status = s);
    } on ApiException catch (_) {}
  }

  Future<void> _save() async {
    final v = _draft.text.trim();
    if (v.replaceAll(RegExp(r'[\s-]'), '').length < 8) return showMessage(context, 'The passkey needs at least 8 characters.', error: true);
    setState(() => _busy = true);
    try {
      await _api('PUT', '/org/recording-passkey', body: {'passkey': v});
      if (!mounted) return;
      setState(() {
        _fresh = v;
        _editing = false;
        _draft.clear();
      });
      showMessage(context, 'Passkey saved. Send it to your Admins now: it is shown only this once.');
      _load();
    } on ApiException catch (e) {
      if (mounted) showMessage(context, e.message, error: true);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _send() async {
    final admins = asJsonList(_status?['admins']).map((a) => intOf(a['user_id'])).toList();
    try {
      await _api('POST', '/org/recording-passkey/share', body: {'passkey': _fresh, 'user_ids': admins});
      if (mounted) showMessage(context, 'Passkey sent to ${_plural(admins.length, 'Admin')}. Each sees it once.');
    } on ApiException catch (e) {
      if (mounted) showMessage(context, e.message, error: true);
    }
  }

  @override
  Widget build(BuildContext context) {
    final s = _status;
    if (s == null) return const SizedBox.shrink();
    final admins = asJsonList(s['admins']);
    return Panel(
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Row(children: [
          Icon(Icons.key_rounded, color: AppColors.brand, size: 20),
          const SizedBox(width: 8),
          Expanded(child: Text('Recording passkey', style: TextStyle(fontWeight: FontWeight.w800, color: AppColors.ink))),
          if (!_editing) TextButton(onPressed: () => setState(() => _editing = true), child: Text(s['has_passkey'] == true ? 'Change' : 'Set passkey')),
        ]),
        Text(
          '${s['has_passkey'] == true ? 'Set · ends in “…${str(s['hint'])}”. ' : 'Not set yet. '}You and your Admins play recordings without it; '
          'survey persons need it for their own surveys. Send it to your Admins, they pass it on.',
          style: TextStyle(fontSize: 12.5, color: AppColors.muted),
        ),
        if (_editing) ...[
          const SizedBox(height: 8),
          TextField(
            controller: _draft,
            autocorrect: false,
            textCapitalization: TextCapitalization.characters,
            decoration: InputDecoration(
              labelText: 'New passkey',
              suffixIcon: IconButton(tooltip: 'Generate', icon: const Icon(Icons.auto_awesome_rounded), onPressed: () => setState(() => _draft.text = RecordingVault.generatePasskey())),
            ),
          ),
          const SizedBox(height: 6),
          Row(children: [
            FilledButton(onPressed: _busy ? null : _save, child: const Text('Save')),
            TextButton(onPressed: () => setState(() => _editing = false), child: const Text('Cancel')),
          ]),
        ],
        if (_fresh != null) ...[
          const SizedBox(height: 8),
          Container(
            padding: const EdgeInsets.all(10),
            decoration: BoxDecoration(color: AppColors.warnSoft, borderRadius: BorderRadius.circular(10)),
            child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Text('New passkey (shown only now):', style: TextStyle(fontSize: 12.5, color: AppColors.warnDeep)),
              SelectableText(_fresh!, style: const TextStyle(fontWeight: FontWeight.w800, fontSize: 16, letterSpacing: 1)),
              const SizedBox(height: 6),
              Wrap(spacing: 8, children: [
                OutlinedButton(onPressed: () => Clipboard.setData(ClipboardData(text: _fresh!)), child: const Text('Copy')),
                FilledButton(onPressed: admins.isEmpty ? null : _send, child: Text('Send to ${_plural(admins.length, 'Admin')}')),
              ]),
            ]),
          ),
        ],
      ]),
    );
  }
}

/// Admin: see the passkey the Super Admin sent (once) and pass it on to survey persons.
class AdminPasskeyPanel extends StatefulWidget {
  const AdminPasskeyPanel({super.key});

  @override
  State<AdminPasskeyPanel> createState() => _AdminPasskeyPanelState();
}

class _AdminPasskeyPanelState extends State<AdminPasskeyPanel> {
  Json? _d;
  final _passkey = TextEditingController();
  final Set<int> _chosen = {};
  bool _busy = false;

  @override
  void initState() {
    super.initState();
    _load();
  }

  @override
  void dispose() {
    _passkey.dispose();
    super.dispose();
  }

  Future<void> _load() async {
    try {
      final d = await _api('GET', '/admin/recording-passkey/people');
      if (mounted) setState(() => _d = d);
    } on ApiException catch (_) {}
  }

  Future<void> _reveal() async {
    try {
      final r = await _api('POST', '/recordings/passkey/reveal');
      if (!mounted) return;
      setState(() => _passkey.text = str(r['passkey']));
      showMessage(context, 'Shown once: send it to your survey persons below, or note it safely.');
      _load();
    } on ApiException catch (e) {
      if (mounted) showMessage(context, e.message, error: true);
    }
  }

  Future<void> _send() async {
    setState(() => _busy = true);
    try {
      final r = await _api('POST', '/admin/recording-passkey/share', body: {'passkey': _passkey.text.trim(), 'user_ids': _chosen.toList()});
      if (!mounted) return;
      showMessage(context, 'Passkey sent to ${_plural(intOf(r['sent']), 'survey person')}.');
      setState(_chosen.clear);
      _load();
    } on ApiException catch (e) {
      if (mounted) showMessage(context, e.message, error: true);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final d = _d;
    if (d == null) return const SizedBox.shrink();
    final people = asJsonList(d['people']);
    return Panel(
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Row(children: [
          Icon(Icons.key_rounded, color: AppColors.brand, size: 20),
          const SizedBox(width: 8),
          Expanded(child: Text('Recording passkey for survey persons', style: TextStyle(fontWeight: FontWeight.w800, color: AppColors.ink))),
        ]),
        Text(d['has_passkey'] == true ? 'Survey persons need it to play their own recordings. You play recordings without it.' : "The Super Admin hasn't set the passkey yet.",
            style: TextStyle(fontSize: 12.5, color: AppColors.muted)),
        if (d['passkey_waiting_for_me'] == true) ...[
          const SizedBox(height: 6),
          OutlinedButton.icon(onPressed: _reveal, icon: const Icon(Icons.visibility_outlined), label: const Text('Show the passkey sent to me')),
        ],
        if (d['has_passkey'] == true) ...[
          const SizedBox(height: 8),
          TextField(controller: _passkey, autocorrect: false, textCapitalization: TextCapitalization.characters,
              onChanged: (_) => setState(() {}), decoration: const InputDecoration(labelText: 'Passkey from the Super Admin')),
          const SizedBox(height: 4),
          for (final p in people)
            CheckboxListTile(
              dense: true,
              contentPadding: EdgeInsets.zero,
              value: _chosen.contains(intOf(p['user_id'])),
              onChanged: (on) => setState(() => on == true ? _chosen.add(intOf(p['user_id'])) : _chosen.remove(intOf(p['user_id']))),
              title: Text(str(p['name'])),
              subtitle: p['share_pending'] == true ? const Text('Not opened yet') : null,
            ),
          FilledButton(
            onPressed: _busy || _passkey.text.trim().isEmpty || _chosen.isEmpty ? null : _send,
            child: Text(_busy ? 'Sending…' : 'Send to ${_plural(_chosen.length, 'survey person')}'),
          ),
        ],
      ]),
    );
  }
}
