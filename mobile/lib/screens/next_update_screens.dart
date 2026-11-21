import 'package:dio/dio.dart';

import '../widgets/design.dart';

import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart' show Clipboard, ClipboardData;
import 'package:url_launcher/url_launcher.dart';

import '../main.dart';
import '../services/api_service.dart';
import '../services/org_api.dart';
import '../services/session.dart';
import 'org_screens.dart';
import '../widgets/search_dropdown.dart';

/*
  Next update (requirements PDF), the app side of the web's new screens:
    RecipeExtrasView      costing (Cost per KG x Usage %), standards, attachments, earlier steps
    ReportStatusCard      survey person: report status, admin's note, Submit for Admin review
    ReportReviewsView     admins: reports to approve and forward / return
    OrgSurveysPage        Super Admin: every survey, its report and Mark reviewed
    RecipeMasterPage      Super Admin: every recipe with cost per kg and standards; the rules
    MastersPage           admins: Occupation, Class (SEC), Respondent type lists
    ReportDownloads       Excel / PowerPoint of a product's round report (opens in the browser)
*/

class NextApi {
  static final Dio _dio = Dio(BaseOptions(
    baseUrl: ApiConfig.baseUrl,
    connectTimeout: const Duration(seconds: 20),
    receiveTimeout: const Duration(seconds: 60),
  ))
    ..interceptors.add(InterceptorsWrapper(onRequest: (options, handler) {
      final token = Session.token;
      if (token != null && token.isNotEmpty) options.headers['Authorization'] = 'Bearer $token';
      handler.next(options);
    }));

  static Future<Json> _send(String method, String path, {Object? body, Map<String, dynamic>? query}) async {
    try {
      final r = await _dio.request(path, data: body, queryParameters: query, options: Options(method: method));
      return asJson(r.data);
    } on DioException catch (e) {
      final d = e.response?.data;
      throw d is Map && d['detail'] is String ? d['detail'] as String : 'Something went wrong. Check the connection and try again.';
    }
  }

  // Recipe Master, standards, attachments
  static Future<Json> recipe(int productId) => _send('GET', '/products/$productId/recipe');
  static Future<Json> recipeMaster() => _send('GET', '/recipes');
  // Recipe plan and action plans (web: components/ActionPlans.jsx)
  static Future<Json> recipePlan(int productId) => _send('GET', '/products/$productId/recipe-plan');
  static Future<Json> actionPlans(int productId) => _send('GET', '/products/$productId/action-plans');
  // Super Admin accounts (each change needs the signed-in Super Admin's password)
  static Future<Json> superAdmins() => _send('GET', '/org/super-admins');
  static Future<Json> addSuperAdmin(Json body) => _send('POST', '/org/super-admins', body: body);
  static Future<Json> resetSuperAdmin(int id, String password) =>
      _send('POST', '/org/super-admins/$id/reset-password', body: {'current_password': password});
  static Future<Json> setSuperAdminActive(int id, bool active, String password) =>
      _send('PUT', '/org/super-admins/$id', body: {'is_active': active, 'current_password': password});
  // Custom reports from a prompt (one product or many)
  static Future<Json> reportOptions() => _send('GET', '/reports/options');
  static Future<Json> planReport(Json body) => _send('POST', '/reports/plan', body: body);
  static Future<Json> standards({int? productId}) => _send('GET', productId == null ? '/recipe-standards' : '/recipe-standards?product_id=$productId');
  static Future<Json> changeStandard(int id, Json body) => _send('PUT', '/recipe-standards/$id', body: body);
  static Future<Json> addStandard(Json body) => _send('POST', '/recipe-standards', body: body);
  static Future<Json> removeStandard(int id) => _send('DELETE', '/recipe-standards/$id');
  static Future<Json> uploadAttachment(int productId, String path, String name) async =>
      _send('POST', '/products/$productId/recipe/attachments',
          body: FormData.fromMap({'file': await MultipartFile.fromFile(path, filename: name)}));
  static Future<Json> removeAttachment(int id) => _send('DELETE', '/recipe-attachments/$id');

  // Master lists
  static Future<Json> masters({bool all = false}) => _send('GET', '/masters', query: all ? {'all': true} : null);
  static Future<Json> addMaster(String kind, String name) => _send('POST', '/admin/masters/$kind', body: {'name': name});
  static Future<Json> setMasterActive(String kind, int id, bool on) =>
      _send('PATCH', '/admin/masters/$kind/$id', body: {'is_active': on});


  // Report review chain
  static Future<Json> reportHistory(int responseId) => _send('GET', '/surveys/responses/$responseId/report/history');
  static Future<Json> submitReport(int responseId) => _send('POST', '/surveys/responses/$responseId/report/submit', body: {});
  static Future<Json> reportReviews(String status) => _send('GET', '/admin/report-reviews', query: {'status': status});
  static Future<Json> approveReport(int id, String note) =>
      _send('POST', '/admin/report-reviews/$id/approve', body: {'note': note.trim().isEmpty ? null : note.trim()});
  static Future<Json> returnReport(int id, String note) => _send('POST', '/admin/report-reviews/$id/return', body: {'note': note.trim()});

  // Super Admin: every survey
  static Future<Json> orgSurveys(String reportStatus) =>
      _send('GET', '/org/surveys', query: reportStatus.isEmpty ? null : {'report_status': reportStatus});
  static Future<Json> orgSurvey(int id) => _send('GET', '/org/surveys/$id');
  static Future<Json> reviewOrgSurvey(int id, String note) =>
      _send('POST', '/org/surveys/$id/review', body: {'note': note.trim().isEmpty ? null : note.trim()});

  /// Files (attachments, Excel, PowerPoint) open in the phone's browser, which previews or saves them.
  static Future<void> open(BuildContext context, String path) async {
    final sep = path.contains('?') ? '&' : '?';
    final uri = Uri.parse('${ApiConfig.baseUrl}$path${sep}token=${Uri.encodeComponent(Session.token ?? '')}');
    final ok = await launchUrl(uri, mode: LaunchMode.externalApplication);
    if (!ok && context.mounted) showMessage(context, "The file couldn't be opened.", error: true);
  }
}

const reportLabels = {
  'generated': 'Generated',
  'pending_admin': 'Pending Admin review',
  'returned': 'Returned to survey person',
  'forwarded': 'Approved by Admin · forwarded',
  'reviewed': 'Reviewed by Super Admin',
};
const reportTones = {'generated': 'muted', 'pending_admin': 'warn', 'returned': 'neg', 'forwarded': 'ok', 'reviewed': 'ok'};

String _money(dynamic v) => v == null ? '—' : '₹${(v as num).toStringAsFixed(2)}';
String _when(dynamic iso) {
  final d = DateTime.tryParse(str(iso));
  return d == null ? '' : formatDate(d.toLocal());
}

Widget _title(String text) => Padding(
      padding: const EdgeInsets.only(top: 14, bottom: 6),
      child: Text(text, style: TextStyle(fontSize: 16, fontWeight: FontWeight.w800, color: AppColors.ink)),
    );

// ---------------------------------------------------------------- Recipe extras

class RecipeExtrasView extends StatefulWidget {
  const RecipeExtrasView({super.key, required this.productId, this.canEdit = false, this.standardsOnly = false, this.data, this.costingOnly = false});

  final int productId;

  /// Given data (an earlier survey's version) instead of loading the current recipe.
  final Json? data;

  /// Only the costing table (Cost per KG, Cost in Use).
  final bool costingOnly;
  final bool canEdit; // Super Admin: upload / remove attachments, manage this product's standards
  final bool standardsOnly; // the product's Standards tab: results and rules only

  @override
  State<RecipeExtrasView> createState() => _RecipeExtrasViewState();
}

class _RecipeExtrasViewState extends State<RecipeExtrasView> {
  Json? _d;
  String? _error;
  bool _busy = false;
  List<Json> _productRules = const []; // rules made for this product only (editor)

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    if (widget.data != null) {
      setState(() => _d = widget.data);
      return;
    }
    try {
      final d = await NextApi.recipe(widget.productId);
      final rules = widget.canEdit ? asJsonList((await NextApi.standards(productId: widget.productId))['standards']) : const <Json>[];
      if (mounted) {
        setState(() {
          _d = d;
          _productRules = rules;
        });
      }
    } catch (e) {
      if (mounted) setState(() => _error = '$e');
    }
  }

  Future<void> _upload() async {
    // This file_picker version returns a non-null list (empty if the person cancels),
    // the same as survey_flow.dart's _pickFile.
    final List<PlatformFile> picked;
    try {
      picked = await FilePicker.pickFiles(
          type: FileType.custom, allowedExtensions: ['pdf', 'png', 'jpg', 'jpeg', 'webp', 'doc', 'docx', 'xls', 'xlsx', 'ppt', 'pptx', 'csv', 'txt']);
    } catch (e) {
      if (mounted) showMessage(context, "The phone's files couldn't be opened: $e", error: true);
      return;
    }
    if (picked.isEmpty || !mounted) return;
    final f = picked.first;
    if (f.path == null) {
      showMessage(context, "This file can't be read. Copy it to the phone's storage and choose it again.", error: true);
      return;
    }
    setState(() => _busy = true);
    try {
      await NextApi.uploadAttachment(widget.productId, f.path!, f.name);
      await _load();
    } catch (e) {
      if (mounted) showMessage(context, '$e', error: true);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  /// Results (expected, actual, MET / NOT MET, reason) and, for the Super Admin, the rules
  /// made for this product: add, edit, remove.
  List<Widget> _standardsWidgets(Json standards, List<Json> results) {
    return [
      _title('Recipe standards'),
      if (results.isEmpty)
        Text('No standard rules apply yet.', style: TextStyle(color: AppColors.muted))
      else
        Panel(
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Pill(standards['passed'] == true ? 'All standards met' : '${intOf(standards['failed_count'])} not met',
                tone: standards['passed'] == true ? 'ok' : 'neg'),
            const SizedBox(height: 6),
            for (final r in results)
              Padding(
                padding: const EdgeInsets.symmetric(vertical: 6),
                child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                  Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
                    Expanded(child: Text(str(r['name']), style: TextStyle(fontWeight: FontWeight.w700, color: AppColors.ink))),
                    Pill(str(r['status'], r['passed'] == true ? 'MET' : 'NOT MET'), tone: r['passed'] == true ? 'ok' : 'neg'),
                  ]),
                  if (str(r['expected']).isNotEmpty) Text('Expected: ${str(r['expected'])}', style: TextStyle(color: AppColors.muted, fontSize: 13)),
                  if (str(r['actual']).isNotEmpty) Text('Actual: ${str(r['actual'])}', style: TextStyle(color: AppColors.muted, fontSize: 13)),
                  Text(str(r['reason'], str(r['detail'])),
                      style: TextStyle(color: r['passed'] == true ? AppColors.muted : AppColors.neg, fontSize: 13)),
                ]),
              ),
          ]),
        ),
      if (widget.canEdit) ...[
        Row(children: [
          Expanded(child: _title('Rules for this product')),
          TextButton.icon(
            onPressed: () async {
              if (await showRuleDialog(context, productId: widget.productId)) _load();
            },
            icon: const Icon(Icons.add),
            label: const Text('Add rule'),
          ),
        ]),
        if (_productRules.isEmpty) Text('No rules for this product only.', style: TextStyle(color: AppColors.muted)),
        for (final r in _productRules)
          ListTile(
            contentPadding: EdgeInsets.zero,
            title: Text(str(r['name'])),
            subtitle: Text('${_ruleKinds[str(r['kind'])] ?? ''} · ${ruleCondition(r)}'),
            onTap: () async {
              if (await showRuleDialog(context, productId: widget.productId, rule: r)) _load();
            },
            trailing: IconButton(
              tooltip: 'Remove rule',
              icon: Icon(Icons.delete_outline, color: AppColors.neg),
              onPressed: () async {
                await NextApi.removeStandard(intOf(r['id'])).catchError((_) => <String, dynamic>{});
                _load();
              },
            ),
          ),
      ],
    ];
  }

  /// Rules for the whole category or all products, from before per-product standards:
  /// shown so the Super Admin can remove them from here.
  List<Widget> _inheritedRules(List<Json> results) {
    final inherited = results.where((r) => str(r['scope']).isNotEmpty && str(r['scope']) != 'product').toList();
    if (inherited.isEmpty) return const [];
    return [
      _title('Also applied: category or all products'),
      Text('Created before standards were set per product. Remove them if they should not apply here.',
          style: TextStyle(fontSize: 12.5, color: AppColors.muted)),
      for (final r in inherited)
        ListTile(
          contentPadding: EdgeInsets.zero,
          title: Text(str(r['name'])),
          subtitle: Text('${str(r['scope']) == 'all' ? 'All products' : 'This category'} · ${str(r['expected'])}'),
          trailing: IconButton(
            tooltip: 'Remove rule',
            icon: Icon(Icons.delete_outline, color: AppColors.neg),
            onPressed: () async {
              final ok = await confirmAction(context, title: 'Remove “${str(r['name'])}”?',
                  message: str(r['scope']) == 'all' ? 'It applies to every product.' : 'It applies to every product in this category.',
                  confirmLabel: 'Remove');
              if (!ok) return;
              await NextApi.removeStandard(intOf(r['rule_id'])).catchError((_) => <String, dynamic>{});
              _load();
            },
          ),
        ),
    ];
  }

  @override
  Widget build(BuildContext context) {
    final d = _d;
    if (d == null) {
      return _error == null ? const Padding(padding: EdgeInsets.all(16), child: Center(child: CircularProgressIndicator())) : Text(_error!);
    }
    final costing = asJson(d['costing']);
    final rows = asJsonList(costing['ingredients']);
    final standards = asJson(d['standards']);
    final results = asJsonList(standards['results']);
    final files = asJsonList(d['attachments']);
    final legacy = asJson(d['legacy_steps']);
    if (widget.standardsOnly) {
      return Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        if (d['recipe'] == null) Text('Add a recipe on the Recipe tab: the standards are checked against it.', style: TextStyle(color: AppColors.muted)),
        ..._standardsWidgets(standards, results),
        ..._inheritedRules(results),
      ]);
    }
    return Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
      if (rows.isNotEmpty) ...[
        _title('Costing'),
        Text('Cost in Use = Cost per KG × Usage %', style: TextStyle(fontSize: 12.5, color: AppColors.muted)),
        Panel(
          child: Column(children: [
            for (final r in rows)
              Padding(
                padding: const EdgeInsets.symmetric(vertical: 4),
                child: Row(children: [
                  Expanded(child: Text(str(r['name']), style: const TextStyle(fontWeight: FontWeight.w700))),
                  SizedBox(width: 58, child: Text(r['usage_pct'] == null ? '—' : '${r['usage_pct']}%', textAlign: TextAlign.right)),
                  SizedBox(width: 72, child: Text(_money(r['cost_per_kg']), textAlign: TextAlign.right, style: TextStyle(color: AppColors.muted))),
                  SizedBox(width: 72, child: Text(_money(r['cost_in_use']), textAlign: TextAlign.right)),
                ]),
              ),
            const Divider(),
            Row(children: [
              const Expanded(child: Text('Recipe cost per kg', style: TextStyle(fontWeight: FontWeight.w800))),
              Text(_money(costing['cost_per_kg']), style: const TextStyle(fontWeight: FontWeight.w800)),
            ]),
            if (costing['missing_costs'] is List && (costing['missing_costs'] as List).isNotEmpty)
              Align(
                alignment: Alignment.centerLeft,
                child: Text('${(costing['missing_costs'] as List).length} without a Cost per KG',
                    style: TextStyle(fontSize: 12.5, color: AppColors.warn)),
              ),
          ]),
        ),
      ],
      if (widget.costingOnly && rows.isEmpty) Text('No costs were entered for this version.', style: TextStyle(color: AppColors.muted)),
      if (!widget.costingOnly) ...[
      // Standards: read-only results here for admins; the Super Admin manages them on the Standards tab
      if (!widget.canEdit) ..._standardsWidgets(standards, results),
      Row(children: [
        Expanded(child: _title('Process attachments (${files.length})')),
        if (widget.canEdit)
          TextButton.icon(onPressed: _busy ? null : _upload, icon: const Icon(Icons.attach_file), label: Text(_busy ? 'Uploading…' : 'Attach')),
      ]),
      if (files.isEmpty) Text('No files attached.', style: TextStyle(color: AppColors.muted)),
      for (final f in files)
        ListTile(
          contentPadding: EdgeInsets.zero,
          leading: const Icon(Icons.description_outlined),
          title: Text(str(f['filename'])),
          subtitle: Text([str(f['uploaded_by']), _when(f['created_at'])].where((s) => s.isNotEmpty).join(' · ')),
          onTap: () => NextApi.open(context, '/recipe-attachments/${intOf(f['id'])}/file'),
          trailing: widget.canEdit
              ? IconButton(
                  tooltip: 'Remove',
                  icon: Icon(Icons.delete_outline, color: AppColors.neg),
                  onPressed: () async {
                    await NextApi.removeAttachment(intOf(f['id'])).catchError((_) => <String, dynamic>{});
                    _load();
                  })
              : const Icon(Icons.open_in_new, size: 18),
        ),
      if (asJsonList(legacy['steps']).isNotEmpty) ...[
        _title('Steps from version ${intOf(legacy['version'], 1)} (read-only)'),
        Text("New recipe versions don't use steps.", style: TextStyle(fontSize: 12.5, color: AppColors.muted)),
        for (final (i, s) in asJsonList(legacy['steps']).indexed)
          Padding(padding: const EdgeInsets.only(top: 4), child: Text('${i + 1}. ${str(s['title'])}')),
      ],
      ],
    ]);
  }
}

// ---------------------------------------------------------------- Survey person: report status

class ReportStatusCard extends StatefulWidget {
  const ReportStatusCard({super.key, required this.responseId});

  final int responseId;

  @override
  State<ReportStatusCard> createState() => _ReportStatusCardState();
}

class _ReportStatusCardState extends State<ReportStatusCard> {
  Json? _h;
  bool _busy = false;

  @override
  void initState() {
    super.initState();
    NextApi.reportHistory(widget.responseId).then((h) => mounted ? setState(() => _h = h) : null).catchError((_) => null);
  }

  Future<void> _submit() async {
    setState(() => _busy = true);
    try {
      final h = await NextApi.submitReport(widget.responseId);
      if (mounted) setState(() => _h = h);
    } catch (e) {
      if (mounted) showMessage(context, '$e', error: true);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final h = _h;
    final status = str(h?['report_status']);
    if (h == null || status.isEmpty) return const SizedBox.shrink();
    Json? returned;
    for (final e in asJsonList(h['history'])) {
      if (e['to'] == 'returned') returned = e; // the latest return
    }
    return Panel(
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Row(children: [
          const Expanded(child: Text('Report approval', style: TextStyle(fontWeight: FontWeight.w800))),
          Pill(reportLabels[status] ?? status, tone: reportTones[status] ?? 'muted'),
        ]),
        if (status == 'returned' && returned != null && str(returned['note']).isNotEmpty)
          Padding(padding: const EdgeInsets.only(top: 6), child: Text("Admin's note: “${str(returned['note'])}”")),
        if (status == 'generated' || status == 'returned') ...[
          const SizedBox(height: 8),
          FilledButton(onPressed: _busy ? null : _submit, child: Text(status == 'returned' ? 'Submit again' : 'Submit for Admin review')),
        ],
      ]),
    );
  }
}

// ---------------------------------------------------------------- Admin: report reviews

class ReportReviewsView extends StatefulWidget {
  const ReportReviewsView({super.key});

  @override
  State<ReportReviewsView> createState() => _ReportReviewsViewState();
}

class _ReportReviewsViewState extends State<ReportReviewsView> {
  String _filter = 'pending_admin';
  List<Json>? _rows;
  int _pending = 0;
  String? _error;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final r = await NextApi.reportReviews(_filter);
      if (!mounted) return;
      setState(() {
        _rows = asJsonList(r['reports']);
        _pending = intOf(asJson(r['counts'])['pending_admin']);
        _error = null;
      });
    } catch (e) {
      if (mounted) setState(() => _error = '$e');
    }
  }

  Future<void> _decide(Json r, bool approve) async {
    final note = TextEditingController();
    final ok = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: Text(approve ? 'Approve and forward?' : 'Return to the survey person?'),
        content: TextField(controller: note, decoration: InputDecoration(labelText: approve ? 'Note (optional)' : 'What should they fix?')),
        actions: [
          TextButton(onPressed: () => Navigator.of(ctx).pop(false), child: const Text('Cancel')),
          FilledButton(onPressed: () => Navigator.of(ctx).pop(true), child: Text(approve ? 'Approve' : 'Return')),
        ],
      ),
    );
    if (ok != true) return;
    try {
      final id = intOf(r['response_id']);
      approve ? await NextApi.approveReport(id, note.text) : await NextApi.returnReport(id, note.text);
      if (mounted) showMessage(context, approve ? 'Approved and forwarded to the Super Admin.' : 'Returned to the survey person.');
      _load();
    } catch (e) {
      if (mounted) showMessage(context, '$e', error: true);
    }
  }

  @override
  Widget build(BuildContext context) {
    final rows = _rows;
    return RefreshIndicator(
      onRefresh: _load,
      child: ListView(padding: const EdgeInsets.fromLTRB(16, 12, 16, 24), children: [
        Wrap(spacing: 8, runSpacing: 8, children: [
          for (final (k, l) in const [('pending_admin', 'Pending'), ('forwarded', 'Forwarded'), ('returned', 'Returned'), ('reviewed', 'Reviewed')])
            ChoiceChip(
              label: Text(k == 'pending_admin' ? '$l $_pending' : l),
              selected: _filter == k,
              onSelected: (_) {
                setState(() {
                  _filter = k;
                  _rows = null;
                });
                _load();
              },
            ),
        ]),
        const SizedBox(height: 10),
        if (_error != null) Text(_error!, style: TextStyle(color: AppColors.neg)),
        if (rows == null && _error == null) const Padding(padding: EdgeInsets.all(32), child: Center(child: CircularProgressIndicator())),
        if (rows != null && rows.isEmpty) const EmptyNote(icon: Icons.task_alt, title: 'Nothing here', text: 'Reports submitted by survey persons appear here.'),
        for (final r in rows ?? const <Json>[])
          Card(
            margin: const EdgeInsets.only(bottom: 10),
            child: Padding(
              padding: const EdgeInsets.all(14),
              child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                Row(children: [
                  Expanded(child: Text('Survey #${intOf(r['response_id'])} · ${str(r['product_name'])}', style: const TextStyle(fontWeight: FontWeight.w800))),
                  Pill(reportLabels[str(r['report_status'])] ?? '', tone: reportTones[str(r['report_status'])] ?? 'muted'),
                ]),
                Text([str(r['customer']), str(r['city']), if (str(r['executive_name']).isNotEmpty) 'by ${str(r['executive_name'])}'].where((s) => s.isNotEmpty).join(' · '),
                    style: TextStyle(fontSize: 13, color: AppColors.muted)),
                const SizedBox(height: 6),
                Text('${r['rating'] != null ? '${(r['rating'] as num).toStringAsFixed(1)} / 5 · ' : ''}${str(r['summary'], 'No summary.')}'),
                if (r['report_status'] == 'pending_admin') ...[
                  const SizedBox(height: 8),
                  Row(children: [
                    Expanded(child: FilledButton(onPressed: () => _decide(r, true), child: const Text('Approve'))),
                    const SizedBox(width: 8),
                    Expanded(child: OutlinedButton(onPressed: () => _decide(r, false), child: const Text('Return'))),
                  ]),
                ],
              ]),
            ),
          ),
      ]),
    );
  }
}

// ---------------------------------------------------------------- Super Admin: every survey

class OrgSurveysPage extends StatefulWidget {
  const OrgSurveysPage({super.key});

  @override
  State<OrgSurveysPage> createState() => _OrgSurveysPageState();
}

class _OrgSurveysPageState extends State<OrgSurveysPage> {
  String _filter = '';
  List<Json>? _rows;
  Json _counts = {};
  String? _error;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final r = await NextApi.orgSurveys(_filter);
      if (!mounted) return;
      setState(() {
        _rows = asJsonList(r['surveys']);
        _counts = asJson(r['counts']);
        _error = null;
      });
    } catch (e) {
      if (mounted) setState(() => _error = '$e');
    }
  }

  Future<void> _open(Json s) async {
    await showModalBottomSheet<void>(
      context: context,
      isScrollControlled: true,
      useSafeArea: true,
      builder: (_) => _OrgSurveySheet(id: intOf(s['response_id'])),
    );
    _load();
  }

  @override
  Widget build(BuildContext context) {
    final rows = _rows;
    return RefreshIndicator(
      onRefresh: _load,
      child: ListView(padding: const EdgeInsets.fromLTRB(16, 12, 16, 24), children: [
        Text('Every survey, by every survey person, with its report and review status.', style: TextStyle(color: AppColors.muted)),
        const SizedBox(height: 10),
        Wrap(spacing: 8, runSpacing: 8, children: [
          for (final (k, l) in const [('', 'All'), ('forwarded', 'Forwarded to me'), ('reviewed', 'Reviewed'), ('pending_admin', 'Pending Admin')])
            ChoiceChip(
              label: Text(k == 'forwarded' && intOf(_counts['forwarded']) > 0 ? '$l ${intOf(_counts['forwarded'])}' : l),
              selected: _filter == k,
              onSelected: (_) {
                setState(() {
                  _filter = k;
                  _rows = null;
                });
                _load();
              },
            ),
        ]),
        const SizedBox(height: 10),
        if (_error != null) Text(_error!, style: TextStyle(color: AppColors.neg)),
        if (rows == null && _error == null) const Padding(padding: EdgeInsets.all(32), child: Center(child: CircularProgressIndicator())),
        if (rows != null && rows.isEmpty) const EmptyNote(icon: Icons.inbox_outlined, title: 'No surveys', text: ''),
        for (final s in rows ?? const <Json>[])
          Card(
            margin: const EdgeInsets.only(bottom: 10),
            child: ListTile(
              onTap: () => _open(s),
              title: Text('Survey #${intOf(s['response_id'])} · ${str(s['product_name'])}', style: const TextStyle(fontWeight: FontWeight.w700)),
              subtitle: Text([str(s['executive_name']), [str(s['city']), str(s['country'])].where((x) => x.isNotEmpty).join(', '), _when(s['created_at'])]
                  .where((x) => x.isNotEmpty)
                  .join(' · ')),
              trailing: Pill(reportLabels[str(s['report_status'])] ?? (s['status'] == 'analyzed' ? 'Analyzed' : 'Not analyzed'),
                  tone: reportTones[str(s['report_status'])] ?? 'muted'),
            ),
          ),
      ]),
    );
  }
}

class _OrgSurveySheet extends StatefulWidget {
  const _OrgSurveySheet({required this.id});

  final int id;

  @override
  State<_OrgSurveySheet> createState() => _OrgSurveySheetState();
}

class _OrgSurveySheetState extends State<_OrgSurveySheet> {
  Json? _d;
  final _note = TextEditingController();
  bool _busy = false;

  @override
  void initState() {
    super.initState();
    NextApi.orgSurvey(widget.id).then((d) => mounted ? setState(() => _d = d) : null).catchError((_) => null);
  }

  @override
  void dispose() {
    _note.dispose();
    super.dispose();
  }

  Future<void> _review() async {
    setState(() => _busy = true);
    try {
      final r = await NextApi.reviewOrgSurvey(widget.id, _note.text);
      if (mounted) setState(() => _d = {...?_d, 'report_status': r['report_status'], 'history': r['history']});
    } catch (e) {
      if (mounted) showMessage(context, '$e', error: true);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final d = _d;
    final a = asJson(d?['analysis']);
    return Padding(
      padding: EdgeInsets.only(bottom: MediaQuery.viewInsetsOf(context).bottom),
      child: DraggableScrollableSheet(
        expand: false,
        initialChildSize: 0.85,
        maxChildSize: 0.95,
        builder: (context, scroll) => d == null
            ? const Center(child: CircularProgressIndicator())
            : ListView(controller: scroll, padding: const EdgeInsets.fromLTRB(20, 8, 20, 24), children: [
                Text('Survey #${widget.id} · ${str(d['product_name'])}', style: const TextStyle(fontFamily: kDisplayFont, fontSize: 19, fontWeight: FontWeight.w800)),
                const SizedBox(height: 4),
                Text([str(d['customer']), str(d['city']), str(d['country']), str(d['respondent_type']),
                  if (str(d['audience']).isNotEmpty) str(d['audience']) == 'internal' ? 'Internal' : 'External', str(d['occupation']), str(d['sec_class'])]
                    .where((x) => x.isNotEmpty)
                    .join(' · ')),
                const SizedBox(height: 8),
                Pill(reportLabels[str(d['report_status'])] ?? 'Not generated yet', tone: reportTones[str(d['report_status'])] ?? 'muted'),
                if (a.isNotEmpty) ...[
                  _title('Report'),
                  Text('Rating: ${a['rating'] == null ? '—' : '${(a['rating'] as num).toStringAsFixed(1)} / 5'} · ${str(a['sentiment'], '—')}'),
                  const SizedBox(height: 4),
                  Text(str(a['summary'])),
                ],
                if (asJsonList(d['answers']).isNotEmpty) _title('Answers'),
                for (final q in asJsonList(d['answers']))
                  Padding(padding: const EdgeInsets.only(bottom: 4), child: Text('${str(q['question'])}  ${str(q['value'], '—')}')),
                // The full analysis page, the same one admins open (read-only for the Super Admin)
                if (d['feedback_id'] != null)
                  Padding(
                    padding: const EdgeInsets.only(top: 12),
                    child: FilledButton.icon(
                      onPressed: () => Navigator.of(context).push(MaterialPageRoute(builder: (_) => FeedbackDetailPage(id: intOf(d['feedback_id'])))),
                      icon: const Icon(Icons.insights_outlined),
                      label: const Text('Open full analysis'),
                    ),
                  ),
                if (str(a['transcript']).isNotEmpty) ...[_title('Transcript'), Text(str(a['transcript']))],
                if (asJsonList(d['history']).isNotEmpty) _title('Review history'),
                for (final h in asJsonList(d['history']))
                  Text('${_when(h['at'])} · ${str(h['by'])}: ${str(h['label'])}${str(h['note']).isNotEmpty ? ' · “${str(h['note'])}”' : ''}',
                      style: TextStyle(fontSize: 13, color: AppColors.muted)),
                if (d['report_status'] == 'forwarded') ...[
                  const SizedBox(height: 12),
                  TextField(controller: _note, decoration: const InputDecoration(labelText: 'Note (optional)')),
                  const SizedBox(height: 8),
                  FilledButton(onPressed: _busy ? null : _review, child: const Text('Mark reviewed')),
                ],
              ]),
      ),
    );
  }
}

// ---------------------------------------------------------------- Super Admin: Recipe Master

const _ruleKinds = {
  'required_ingredient': 'Must contain an ingredient',
  'ingredient_share': 'Ingredient Usage % between',
  'ingredient_cost_per_kg': 'Ingredient Cost per KG at most',
  'max_cost_per_kg': 'Recipe cost per kg at most (whole recipe)',
  'costs_complete': 'Every ingredient has a Cost per KG',
};
const _needsIngredient = ['required_ingredient', 'ingredient_share', 'ingredient_cost_per_kg'];

String _num(dynamic v) => v == null ? '' : (v is num && v == v.roundToDouble() ? v.toInt().toString() : '$v');

/// The rule's condition in words (web: StandardRuleForm.jsx ruleCondition).
String ruleCondition(Json r) {
  final ing = str(r['ingredient']);
  switch (str(r['kind'])) {
    case 'required_ingredient':
      return 'contains $ing';
    case 'ingredient_share':
      final lo = r['min_value'], hi = r['max_value'];
      return '$ing: ${lo != null && hi != null ? '${_num(lo)}% – ${_num(hi)}%' : lo != null ? 'at least ${_num(lo)}%' : 'at most ${_num(hi)}%'}';
    case 'ingredient_cost_per_kg':
      return '$ing at most ₹${_num(r['max_value'])}/kg';
    case 'max_cost_per_kg':
      return 'recipe at most ₹${_num(r['max_value'])}/kg';
    default:
      return 'every ingredient costed';
  }
}

/// Add or edit a recipe standard rule. Scope, exactly one: [productId] (this product), or
/// chosen in the dialog: all products or one category. Returns true when saved.
Future<bool> showRuleDialog(BuildContext context, {int? productId, Json? rule}) async {
  final name = TextEditingController(text: str(rule?['name']));
  final ingredient = TextEditingController(text: str(rule?['ingredient']));
  final min = TextEditingController(text: _num(rule?['min_value']));
  final max = TextEditingController(text: _num(rule?['max_value']));
  var kind = str(rule?['kind'], 'ingredient_share');
  // '' = not chosen yet, 'all' = every product, otherwise a category id
  var scope = rule == null ? '' : (rule['applies_to_all'] == true ? 'all' : rule['category_id'] != null ? '${rule['category_id']}' : '');
  var categories = <Json>[];
  if (productId == null) {
    try {
      categories = await OrgApi.categories(includeInactive: false);
    } catch (_) {}
  }
  if (!context.mounted) return false;
  String? error;
  final saved = await showDialog<bool>(
    context: context,
    builder: (ctx) => StatefulBuilder(
      builder: (ctx, set) {
        final isCost = kind == 'ingredient_cost_per_kg' || kind == 'max_cost_per_kg';
        Future<void> save() async {
          if (name.text.trim().length < 3) {
            set(() => error = 'Give the rule a name (3 letters or more).');
            return;
          }
          if (productId == null && scope.isEmpty) {
            set(() => error = 'Choose what the rule applies to.');
            return;
          }
          final body = <String, dynamic>{
            'name': name.text.trim(),
            'kind': kind,
            'ingredient': _needsIngredient.contains(kind) && ingredient.text.trim().isNotEmpty ? ingredient.text.trim() : null,
            'min_value': kind == 'ingredient_share' ? double.tryParse(min.text.trim()) : null,
            'max_value': kind == 'ingredient_share' || isCost ? double.tryParse(max.text.trim().replaceAll(',', '')) : null,
            'product_id': productId,
            'category_id': productId == null && scope.isNotEmpty && scope != 'all' ? int.tryParse(scope) : null,
            'applies_to_all': productId == null && scope == 'all',
          };
          try {
            if (rule == null) {
              await NextApi.addStandard(body);
            } else {
              await NextApi.changeStandard(intOf(rule['id']), body);
            }
            if (ctx.mounted) Navigator.of(ctx).pop(true);
          } catch (e) {
            set(() => error = '$e');
          }
        }

        return AlertDialog(
          title: Text(rule == null ? (productId == null ? 'New standard rule' : 'New rule for this product') : 'Edit rule'),
          content: SingleChildScrollView(
            child: Column(mainAxisSize: MainAxisSize.min, children: [
              TextField(controller: name, decoration: const InputDecoration(labelText: 'Rule name')),
              SearchDropdownField<String>(
                initialValue: kind,
                isExpanded: true,
                decoration: const InputDecoration(labelText: 'Rule type'),
                items: [for (final e in _ruleKinds.entries) DropdownMenuItem(value: e.key, child: Text(e.value, overflow: TextOverflow.ellipsis))],
                onChanged: (v) => set(() => kind = v ?? kind),
              ),
              if (_needsIngredient.contains(kind))
                TextField(controller: ingredient, decoration: const InputDecoration(labelText: 'Ingredient, e.g. cardamom powder')),
              if (kind == 'ingredient_share')
                TextField(controller: min, keyboardType: const TextInputType.numberWithOptions(decimal: true), decoration: const InputDecoration(labelText: 'Min %')),
              if (kind == 'ingredient_share' || isCost)
                TextField(controller: max, keyboardType: const TextInputType.numberWithOptions(decimal: true),
                    decoration: InputDecoration(labelText: isCost ? 'Max ₹ per kg' : 'Max %')),
              if (productId == null)
                SearchDropdownField<String>(
                  initialValue: scope.isEmpty ? null : scope,
                  isExpanded: true,
                  decoration: const InputDecoration(labelText: 'Applies to'),
                  hint: const Text('Choose…'),
                  items: [
                    const DropdownMenuItem(value: 'all', child: Text('All products')),
                    for (final c in categories)
                      DropdownMenuItem(value: '${intOf(c['id'])}', child: Text('Only ${str(c['name'])}', overflow: TextOverflow.ellipsis)),
                  ],
                  onChanged: (v) => set(() => scope = v ?? ''),
                ),
              if (error != null) Padding(padding: const EdgeInsets.only(top: 10), child: Text(error!, style: TextStyle(color: AppColors.neg))),
            ]),
          ),
          actions: [
            TextButton(onPressed: () => Navigator.of(ctx).pop(false), child: const Text('Cancel')),
            FilledButton(onPressed: save, child: Text(rule == null ? 'Add' : 'Save')),
          ],
        );
      },
    ),
  );
  return saved == true;
}

class RecipeMasterPage extends StatefulWidget {
  const RecipeMasterPage({super.key});

  @override
  State<RecipeMasterPage> createState() => _RecipeMasterPageState();
}

class _RecipeMasterPageState extends State<RecipeMasterPage> {
  List<Json>? _recipes;
  String? _error;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final r = [await NextApi.recipeMaster()];
      if (!mounted) return;
      setState(() {
        _recipes = asJsonList(r[0]['recipes']);
      });
    } catch (e) {
      if (mounted) setState(() => _error = '$e');
    }
  }

  @override
  Widget build(BuildContext context) {
    final recipes = _recipes;
    return Scaffold(
      backgroundColor: AppColors.bg,
      appBar: AppBar(title: const Text('Recipe Master')),
      body: RefreshIndicator(
        onRefresh: _load,
        child: ListView(padding: const EdgeInsets.fromLTRB(16, 12, 16, 24), children: [
          if (_error != null) Text(_error!, style: TextStyle(color: AppColors.neg)),
          if (recipes == null && _error == null) const Padding(padding: EdgeInsets.all(32), child: Center(child: CircularProgressIndicator())),
          for (final r in recipes ?? const <Json>[])
            Card(
              margin: const EdgeInsets.only(bottom: 8),
              child: ListTile(
                title: Text(str(r['product_name']), style: const TextStyle(fontWeight: FontWeight.w700)),
                subtitle: Text([
                  str(r['category_name']),
                  r['has_recipe'] == true ? 'v${intOf(r['version'], 1)} · ${intOf(r['ingredients'])} ingredients' : 'No recipe',
                  'Cost/kg ${_money(r['cost_per_kg'])}',
                ].where((x) => x.isNotEmpty).join(' · ')),
                trailing: r['standards'] == null || intOf(asJson(r['standards'])['checked']) == 0
                    ? null
                    : Pill(asJson(r['standards'])['passed'] == true ? 'Standards met' : '${intOf(asJson(r['standards'])['failed_count'])} not met',
                        tone: asJson(r['standards'])['passed'] == true ? 'ok' : 'neg'),
              ),
            ),
          Padding(
            padding: const EdgeInsets.only(top: 12),
            child: Text('Recipe standards are set per product: open a product, then its Standards tab.',
                style: TextStyle(fontSize: 12.5, color: AppColors.muted)),
          ),
        ]),
      ),
    );
  }
}

// ---------------------------------------------------------------- Admin: master lists

class MastersPage extends StatefulWidget {
  const MastersPage({super.key});

  @override
  State<MastersPage> createState() => _MastersPageState();
}

class _MastersPageState extends State<MastersPage> {
  Json? _lists;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    final d = await NextApi.masters(all: true).catchError((_) => <String, dynamic>{});
    if (mounted) setState(() => _lists = d);
  }

  Future<void> _add(String kind, String label) async {
    final c = TextEditingController();
    final ok = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: Text('Add to $label'),
        content: TextField(controller: c, autofocus: true, decoration: const InputDecoration(labelText: 'Name')),
        actions: [
          TextButton(onPressed: () => Navigator.of(ctx).pop(false), child: const Text('Cancel')),
          FilledButton(onPressed: () => Navigator.of(ctx).pop(true), child: const Text('Add')),
        ],
      ),
    );
    if (ok != true || c.text.trim().isEmpty) return;
    try {
      await NextApi.addMaster(kind, c.text.trim());
      _load();
    } catch (e) {
      if (mounted) showMessage(context, '$e', error: true);
    }
  }

  @override
  Widget build(BuildContext context) {
    final lists = _lists;
    return Scaffold(
      backgroundColor: AppColors.bg,
      appBar: AppBar(title: const Text('Master lists')),
      body: lists == null
          ? const Center(child: CircularProgressIndicator())
          : ListView(padding: const EdgeInsets.fromLTRB(16, 12, 16, 24), children: [
              for (final (kind, label) in const [('respondent_type', 'Respondent type'), ('occupation', 'Occupation'), ('sec_class', 'Class (SEC)')]) ...[
                Row(children: [
                  Expanded(child: _title(label)),
                  TextButton.icon(onPressed: () => _add(kind, label), icon: const Icon(Icons.add), label: const Text('Add')),
                ]),
                Wrap(spacing: 6, runSpacing: 6, children: [
                  for (final v in asJsonList(lists[kind]))
                    FilterChip(
                      label: Text(str(v['name'])),
                      selected: v['is_active'] == true,
                      tooltip: v['is_active'] == true ? 'Tap to hide' : 'Tap to show again',
                      onSelected: (on) async {
                        await NextApi.setMasterActive(kind, intOf(v['id']), on).catchError((_) => <String, dynamic>{});
                        _load();
                      },
                    ),
                ]),
              ],
            ]),
    );
  }
}

// ---------------------------------------------------------------- Report downloads

/// A product's report: Excel downloads directly; PowerPoint asks what the slides should contain.
class ReportDownloads extends StatelessWidget {
  const ReportDownloads({super.key, required this.productId, this.round});

  final int productId;
  final int? round;

  void _ask(BuildContext context, String format) {
    showModalBottomSheet<void>(
      context: context,
      isScrollControlled: true,
      useSafeArea: true,
      builder: (_) => Padding(
        padding: EdgeInsets.fromLTRB(20, 8, 20, 20 + MediaQuery.viewInsetsOf(context).bottom),
        child: SingleChildScrollView(
          child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, mainAxisSize: MainAxisSize.min, children: [
            Text('PowerPoint report',
                style: TextStyle(fontFamily: kDisplayFont, fontSize: 18, fontWeight: FontWeight.w800, color: AppColors.ink)),
            const SizedBox(height: 4),
            Text('Describe what the slides should contain. The numbers always come from the surveys.', style: TextStyle(color: AppColors.muted)),
            const SizedBox(height: 12),
            ReportPromptPanel(format: format, productIds: [productId], round: round),
          ]),
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final q = round == null ? '' : '?round=$round';
    return Row(children: [
      Expanded(
        child: OutlinedButton.icon(
          onPressed: () => NextApi.open(context, '/product-feedback/$productId/export.xlsx$q'),
          icon: const Icon(Icons.table_chart_outlined),
          label: const Text('Excel'),
        ),
      ),
      const SizedBox(width: 8),
      Expanded(
        child: OutlinedButton.icon(onPressed: () => _ask(context, 'pptx'), icon: const Icon(Icons.slideshow_outlined), label: const Text('PowerPoint')),
      ),
    ]);
  }
}

const _promptExamples = {
  'pptx': ['Executive summary in 5 slides', 'Only complaints, with recipe recommendations', 'Compare all surveys', 'Only retailers, by city'],
  'xlsx': ['Every survey with its answers', 'Only concerns and recommended actions', 'Compare all surveys', 'Breakdown by respondent type and city'],
};

/// Prompt -> preview -> download (web: components/ReportPrompt.jsx).
class ReportPromptPanel extends StatefulWidget {
  const ReportPromptPanel({super.key, required this.format, this.productIds = const [], this.round});

  final String format;
  final List<int> productIds;
  final int? round;

  @override
  State<ReportPromptPanel> createState() => _ReportPromptPanelState();
}

/// PowerPoint requests start with this design brief (backend: custom_reports.DESIGN_BRIEF);
/// the person adds what they need after it. The brief alone gives the standard deck.
const designBrief = 'Act as a senior UI/UX designer and create a clear, visually engaging PowerPoint presentation: ';

class _ReportPromptPanelState extends State<ReportPromptPanel> {
  late final _prompt = TextEditingController(text: widget.format == 'pptx' ? designBrief : '');
  Json? _plan;
  bool _busy = false;
  String? _error;

  String get _kind => widget.format == 'pptx' ? 'PowerPoint' : 'Excel';

  @override
  void dispose() {
    _prompt.dispose();
    super.dispose();
  }

  Future<void> _prepare() async {
    FocusManager.instance.primaryFocus?.unfocus();
    setState(() {
      _busy = true;
      _error = null;
      _plan = null;
    });
    try {
      final plan = await NextApi.planReport({
        'prompt': _prompt.text.trim(),
        'format': widget.format,
        'product_ids': widget.productIds,
        if (widget.round != null) 'round': widget.round,
      });
      if (mounted) setState(() => _plan = plan);
    } catch (e) {
      if (mounted) setState(() => _error = '$e');
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final plan = _plan;
    final warnings = plan == null || plan['warnings'] is! List ? const [] : plan['warnings'] as List;
    final surveys = intOf(plan?['surveys']);
    return Column(crossAxisAlignment: CrossAxisAlignment.stretch, mainAxisSize: MainAxisSize.min, children: [
      TextField(
        controller: _prompt,
        minLines: 2,
        maxLines: 4,
        maxLength: 2000,
        textInputAction: TextInputAction.done,
        onSubmitted: (_) => FocusManager.instance.primaryFocus?.unfocus(),
        onChanged: (_) => setState(() => _plan = null),
        decoration: InputDecoration(
          labelText: 'What should the $_kind contain?',
          hintText: 'Leave empty for the standard report',
          helperText: widget.format == 'pptx' ? 'Write your request after the design brief; the brief alone gives the standard deck.' : null,
          helperMaxLines: 2,
          counterText: '',
        ),
      ),
      const SizedBox(height: 8),
      Wrap(spacing: 6, runSpacing: 6, children: [
        for (final x in _promptExamples[widget.format]!)
          ActionChip(label: Text(x), onPressed: () => setState(() {
                _prompt.text = (widget.format == 'pptx' ? designBrief : '') + x;
                _plan = null;
              })),
      ]),
      if (plan != null) ...[
        const SizedBox(height: 12),
        Container(
          padding: const EdgeInsets.all(12),
          decoration: BoxDecoration(color: AppColors.brandSoft, borderRadius: BorderRadius.circular(12)),
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Text('$surveys ${surveys == 1 ? 'survey' : 'surveys'} · ${str(plan['summary'])}', style: TextStyle(fontWeight: FontWeight.w700, color: AppColors.ink, height: 1.4)),
            if (plan['planned_by'] == 'keywords')
              Padding(
                padding: const EdgeInsets.only(top: 4),
                child: Text('The AI was unavailable, so the request was read from its keywords.', style: TextStyle(color: AppColors.muted, fontSize: 13)),
              ),
            for (final w in warnings)
              Padding(padding: const EdgeInsets.only(top: 4), child: Text('$w', style: TextStyle(color: AppColors.warn, fontSize: 13))),
          ]),
        ),
      ],
      if (_error != null) Padding(padding: const EdgeInsets.only(top: 8), child: Text(_error!, style: TextStyle(color: AppColors.neg))),
      const SizedBox(height: 12),
      if (plan == null)
        FilledButton(onPressed: _busy ? null : _prepare, child: Text(_busy ? 'Preparing…' : 'Prepare $_kind'))
      else ...[
        FilledButton.icon(
          onPressed: surveys == 0 ? null : () => NextApi.open(context, '/reports/file/${intOf(plan['request_id'])}'),
          icon: const Icon(Icons.download_outlined),
          label: Text('Download $_kind'),
        ),
        TextButton(onPressed: () => setState(() => _plan = null), child: const Text('Change request')),
      ],
    ]);
  }
}

/// Analytics (web: components/AnalyticsPage.jsx): reports across one, several or all products.
class AnalyticsPage extends StatelessWidget {
  const AnalyticsPage({super.key});

  @override
  Widget build(BuildContext context) => Scaffold(
        backgroundColor: AppColors.bg,
        appBar: AppBar(title: const Text('Analytics')),
        body: const AnalyticsView(),
      );
}

class AnalyticsView extends StatefulWidget {
  const AnalyticsView({super.key});

  @override
  State<AnalyticsView> createState() => _AnalyticsViewState();
}

class _AnalyticsViewState extends State<AnalyticsView> {
  List<Json>? _products;
  final Set<int> _picked = {};
  String? _error;

  @override
  void initState() {
    super.initState();
    NextApi.reportOptions().then((o) {
      if (mounted) setState(() => _products = (o['products'] is List ? o['products'] as List : const []).map(asJson).toList());
    }).catchError((e) {
      if (mounted) setState(() => _error = '$e');
    });
  }

  @override
  Widget build(BuildContext context) {
    final products = _products;
    final groups = <String, List<Json>>{};
    for (final p in products ?? const <Json>[]) {
      groups.putIfAbsent(str(p['category'], 'Other'), () => []).add(p);
    }
    return ListView(
      keyboardDismissBehavior: ScrollViewKeyboardDismissBehavior.onDrag,
      padding: const EdgeInsets.fromLTRB(16, 12, 16, 32),
      children: [
        Text('PowerPoint reports from a prompt, for one, several or all products. Every number comes from the surveys.',
            style: TextStyle(color: AppColors.muted, height: 1.4)),
        const SizedBox(height: 12),
        if (_error != null) Text(_error!, style: TextStyle(color: AppColors.neg)),
        Row(children: [
          Expanded(child: Text('Products ${_picked.length}/${products?.length ?? 0}', style: TextStyle(fontWeight: FontWeight.w800, color: AppColors.ink))),
          TextButton(onPressed: () => setState(() => _picked.addAll((products ?? const []).map((p) => intOf(p['id'])))), child: const Text('Select all')),
          TextButton(onPressed: () => setState(_picked.clear), child: const Text('Clear')),
        ]),
        if (products == null && _error == null) const Center(child: Padding(padding: EdgeInsets.all(16), child: CircularProgressIndicator())),
        for (final entry in groups.entries) ...[
          Padding(
            padding: const EdgeInsets.only(top: 8, bottom: 2),
            child: Text(entry.key.toUpperCase(), style: TextStyle(fontSize: 12, fontWeight: FontWeight.w800, color: AppColors.muted, letterSpacing: 0.5)),
          ),
          for (final p in entry.value)
            CheckboxListTile(
              contentPadding: EdgeInsets.zero,
              dense: true,
              value: _picked.contains(intOf(p['id'])),
              onChanged: (v) => setState(() => v == true ? _picked.add(intOf(p['id'])) : _picked.remove(intOf(p['id']))),
              title: Text(str(p['name'])),
              subtitle: Text(p['rounds'] is List && (p['rounds'] as List).isNotEmpty ? 'Surveys ${(p['rounds'] as List).join(', ')}' : 'No surveys yet'),
            ),
        ],
        const SizedBox(height: 16),
        ReportPromptPanel(format: 'pptx', productIds: _picked.toList()),
      ],
    );
  }
}


// ---------------------------------------------------------------- Super Admins (web: SuperAdminsCard.jsx)

/// Every Super Admin login of the organization: add one, reset another's password,
/// disable / reactivate. Each change asks for your own password; a new or reset login
/// gets a one-time starting password, changed at the first sign-in.
class SuperAdminsPage extends StatefulWidget {
  const SuperAdminsPage({super.key});

  @override
  State<SuperAdminsPage> createState() => _SuperAdminsPageState();
}

class _SuperAdminsPageState extends State<SuperAdminsPage> {
  List<Json>? _rows;
  String? _error;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final r = await NextApi.superAdmins();
      if (mounted) setState(() => _rows = asJsonList(r['super_admins']));
    } catch (e) {
      if (mounted) setState(() => _error = '$e');
    }
  }

  /// Asks for your password; returns it, or null when cancelled.
  Future<String?> _askPassword(String title, String message) async {
    final pw = TextEditingController();
    final ok = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: Text(title),
        content: Column(mainAxisSize: MainAxisSize.min, crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text(message, style: TextStyle(color: AppColors.muted)),
          const SizedBox(height: 10),
          TextField(controller: pw, obscureText: true, autofocus: true, decoration: const InputDecoration(labelText: 'Your password')),
        ]),
        actions: [
          TextButton(onPressed: () => Navigator.of(ctx).pop(false), child: const Text('Cancel')),
          FilledButton(onPressed: () => Navigator.of(ctx).pop(true), child: const Text('Confirm')),
        ],
      ),
    );
    return ok == true && pw.text.isNotEmpty ? pw.text : null;
  }

  Future<void> _showCredentials(Json result, {bool reset = false}) async {
    final u = asJson(result['user']);
    final text = 'Email: ${str(u['email'])}\nStarting password: ${str(result['password'])}\n'
        "You'll choose your own password at the first sign-in.";
    await showDialog<void>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: Text(reset ? 'New starting password' : 'Super Admin added'),
        content: Column(mainAxisSize: MainAxisSize.min, crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text(str(u['name']), style: TextStyle(fontWeight: FontWeight.w800, color: AppColors.ink)),
          const SizedBox(height: 6),
          SelectableText(text),
          const SizedBox(height: 8),
          Text('Shown only now. Send it privately.', style: TextStyle(color: AppColors.muted, fontSize: 12.5)),
        ]),
        actions: [
          TextButton(
            onPressed: () async {
              await Clipboard.setData(ClipboardData(text: text));
              if (ctx.mounted) showMessage(ctx, 'Copied.');
            },
            child: const Text('Copy'),
          ),
          FilledButton(onPressed: () => Navigator.of(ctx).pop(), child: const Text('Done')),
        ],
      ),
    );
  }

  Future<void> _add() async {
    final name = TextEditingController(), email = TextEditingController(), pw = TextEditingController();
    String? error;
    final result = await showDialog<Json>(
      context: context,
      builder: (ctx) => StatefulBuilder(
        builder: (ctx, set) => AlertDialog(
          title: const Text('Add Super Admin'),
          content: SingleChildScrollView(
            child: Column(mainAxisSize: MainAxisSize.min, children: [
              TextField(controller: name, decoration: InputDecoration(label: requiredLabel('Full name'))),
              TextField(controller: email, keyboardType: TextInputType.emailAddress, decoration: const InputDecoration(labelText: 'Sign-in email')),
              TextField(controller: pw, obscureText: true, decoration: const InputDecoration(labelText: 'Your password (to confirm)')),
              if (error != null) Padding(padding: const EdgeInsets.only(top: 10), child: Text(error!, style: TextStyle(color: AppColors.neg))),
            ]),
          ),
          actions: [
            TextButton(onPressed: () => Navigator.of(ctx).pop(), child: const Text('Cancel')),
            FilledButton(
              onPressed: () async {
                try {
                  final r = await NextApi.addSuperAdmin({'name': name.text.trim(), 'email': email.text.trim(), 'current_password': pw.text});
                  if (ctx.mounted) Navigator.of(ctx).pop(r);
                } catch (e) {
                  set(() => error = '$e');
                }
              },
              child: const Text('Add'),
            ),
          ],
        ),
      ),
    );
    if (result == null || !mounted) return;
    await _load();
    if (mounted) await _showCredentials(result);
  }

  Future<void> _reset(Json u) async {
    final pw = await _askPassword("Reset ${str(u['name'])}'s password?", "A new starting password is shown once; they're signed out everywhere.");
    if (pw == null || !mounted) return;
    try {
      final r = await NextApi.resetSuperAdmin(intOf(u['id']), pw);
      await _load();
      if (mounted) await _showCredentials(r, reset: true);
    } catch (e) {
      if (mounted) showMessage(context, '$e', error: true);
    }
  }

  Future<void> _toggle(Json u) async {
    final active = u['is_active'] == true;
    final pw = await _askPassword(active ? "Disable ${str(u['name'])}?" : "Reactivate ${str(u['name'])}?",
        active ? "They're signed out everywhere and can't sign in until reactivated." : 'They can sign in again.');
    if (pw == null || !mounted) return;
    try {
      await NextApi.setSuperAdminActive(intOf(u['id']), !active, pw);
      if (mounted) showMessage(context, active ? "${str(u['name'])} is disabled." : "${str(u['name'])} is active again.");
      await _load();
    } catch (e) {
      if (mounted) showMessage(context, '$e', error: true);
    }
  }

  @override
  Widget build(BuildContext context) {
    final rows = _rows;
    return Scaffold(
      backgroundColor: AppColors.bg,
      appBar: AppBar(title: const Text('Super Admins')),
      floatingActionButton: FloatingActionButton.extended(onPressed: _add, icon: const Icon(Icons.person_add_alt_1), label: const Text('Add Super Admin')),
      body: _error != null
          ? Center(child: Padding(padding: const EdgeInsets.all(20), child: Text(_error!, style: TextStyle(color: AppColors.neg))))
          : rows == null
              ? const Center(child: CircularProgressIndicator())
              : RefreshIndicator(
                  onRefresh: _load,
                  child: ListView(padding: const EdgeInsets.fromLTRB(16, 12, 16, 96), children: [
                    Text("Everyone who can manage this organization's products, recipes, standards, questions and surveys.",
                        style: TextStyle(color: AppColors.muted)),
                    const SizedBox(height: 8),
                    for (final u in rows)
                      Card(
                        child: ListTile(
                          title: Text(u['is_me'] == true ? '${str(u['name'])} (you)' : str(u['name'])),
                          subtitle: Text([
                            str(u['email']),
                            if (u['must_change_password'] == true) 'must choose a password at the next sign-in',
                          ].join(' · ')),
                          trailing: Row(mainAxisSize: MainAxisSize.min, children: [
                            Pill(u['is_active'] == true ? 'Active' : 'Disabled', tone: u['is_active'] == true ? 'ok' : 'neg'),
                            if (u['is_me'] != true)
                              PopupMenuButton<String>(
                                tooltip: 'Actions',
                                onSelected: (a) => a == 'reset' ? _reset(u) : _toggle(u),
                                itemBuilder: (_) => [
                                  const PopupMenuItem(value: 'reset', child: Text('Reset password')),
                                  PopupMenuItem(value: 'toggle', child: Text(u['is_active'] == true ? 'Disable' : 'Reactivate')),
                                ],
                              ),
                          ]),
                        ),
                      ),
                  ]),
                ),
    );
  }
}


// ---------------------------------------------------------------- Action plans (web: ActionPlans.jsx)

/// The product's recipe plan and the action plans of its current round: progress, dates,
/// cities and each team member's cities. Read-only in the app; planning and team
/// allocation are done on the web (Super Admin: Action plans tab; Admin: Who can survey).
class ActionPlansView extends StatefulWidget {
  const ActionPlansView({super.key, required this.productId});

  final int productId;

  @override
  State<ActionPlansView> createState() => _ActionPlansViewState();
}

class _ActionPlansViewState extends State<ActionPlansView> {
  Json? _recipe;
  Json? _plans;
  String? _error;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final r = await Future.wait([NextApi.recipePlan(widget.productId), NextApi.actionPlans(widget.productId)]);
      if (mounted) setState(() {
        _recipe = asJson(r[0]['recipe_plan']);
        _plans = r[1];
        _error = null;
      });
    } catch (e) {
      if (mounted) setState(() => _error = '$e');
    }
  }

  static String _dates(Json p) {
    final s = str(p['start_date']), e = str(p['end_date']);
    return s.isEmpty && e.isEmpty ? 'No dates set' : '${s.isEmpty ? '…' : s} – ${e.isEmpty ? '…' : e}';
  }

  static List<String> _list(dynamic v) => [for (final x in (v as List? ?? const [])) '$x'];

  @override
  Widget build(BuildContext context) {
    if (_error != null) return Center(child: Padding(padding: const EdgeInsets.all(20), child: Text(_error!, style: TextStyle(color: AppColors.neg))));
    if (_plans == null) return const Center(child: CircularProgressIndicator());
    final round = asJson(_plans!['round']);
    final plans = asJsonList(_plans!['plans']);
    final rp = _recipe ?? {};
    return RefreshIndicator(
      onRefresh: _load,
      child: ListView(padding: const EdgeInsets.all(16), children: [
        Panel(
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Text('Recipe plan', style: TextStyle(fontWeight: FontWeight.w800, fontSize: 16, color: AppColors.ink)),
            const SizedBox(height: 6),
            if (rp.isEmpty)
              Text('No recipe plan yet.', style: TextStyle(color: AppColors.muted))
            else ...[
              Text('${rp['target'] ?? '–'} consumer responses'),
              Text(_dates(rp)),
              Text(_list(rp['cities']).isEmpty ? 'Any city' : _list(rp['cities']).join(', ')),
              if (str(rp['notes']).isNotEmpty) Text(str(rp['notes']), style: TextStyle(color: AppColors.muted)),
            ],
          ]),
        ),
        const SizedBox(height: 12),
        Text(round.isEmpty ? 'Action plans' : 'Action plans · Survey ${intOf(round['number'])} · recipe v${intOf(round['recipe_version'])}',
            style: TextStyle(fontWeight: FontWeight.w800, fontSize: 16, color: AppColors.ink)),
        const SizedBox(height: 4),
        Text(round.isEmpty ? 'Start a survey to plan its execution.' : '${plans.length} plans · target ${intOf(round['target'])} consumers',
            style: TextStyle(color: AppColors.muted)),
        const SizedBox(height: 8),
        for (final p in plans)
          Padding(
            padding: const EdgeInsets.only(bottom: 10),
            child: Panel(
              child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                Row(children: [
                  Expanded(child: Text(str(p['name']), style: TextStyle(fontWeight: FontWeight.w800, color: AppColors.ink))),
                  Text('${intOf(p['done'])} of ${intOf(p['target'])}', style: TextStyle(color: AppColors.muted)),
                ]),
                const SizedBox(height: 6),
                LinearProgressIndicator(
                  value: intOf(p['target']) == 0 ? 0 : (intOf(p['done']) / intOf(p['target'])).clamp(0, 1).toDouble(),
                  minHeight: 6,
                  borderRadius: BorderRadius.circular(6),
                ),
                const SizedBox(height: 6),
                Text(_dates(p)),
                Wrap(spacing: 6, runSpacing: 4, children: [
                  for (final c in _list(p['cities'])) Pill(c, tone: 'info'),
                  if (_list(p['cities']).isEmpty) Pill('Any city', tone: 'muted'),
                ]),
                const SizedBox(height: 6),
                Text('Team ${asJsonList(p['members']).length}',
                    style: const TextStyle(fontWeight: FontWeight.w700)),
                for (final m in asJsonList(p['members']))
                  Text('• ${str(m['name'])} · ${_list(m['cities']).isEmpty ? 'any city' : _list(m['cities']).join(', ')}'),
                if (asJsonList(p['members']).isEmpty) Text('No one allocated yet.', style: TextStyle(color: AppColors.muted)),
              ]),
            ),
          ),
        const SizedBox(height: 4),
        Text('Plans are created on the web (Super Admin: product → Action plans) and teams allocated there by Admins.',
            style: TextStyle(fontSize: 12, color: AppColors.muted)),
      ]),
    );
  }
}