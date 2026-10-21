import 'dart:io';

import 'package:file_picker/file_picker.dart';
import 'package:video_player/video_player.dart';
import 'package:flutter/material.dart';
import 'package:geolocator/geolocator.dart';

import '../main.dart';
import '../services/session.dart';
import '../services/survey_api.dart';
import '../services/survey_queue.dart';
import '../widgets/media_player.dart';
import 'capture_screens.dart';

/*
  Field survey on the phone, one step per screen:
    category -> product -> respondent -> questions (one per screen) -> recording -> review
  Submit saves the survey on the phone; it uploads and is analyzed in the background.
*/

const _usagePeriods = [
  ('last_week', 'In the last week'),
  ('last_month', 'In the last month'),
  ('last_3_months', 'In the last 3 months'),
  ('more_than_3_months', 'More than 3 months ago'),
];

const _genders = ['Male', 'Female', 'Other', 'Prefer not to say'];
const _fileTypes = ['mp4', 'mov', 'mkv', 'webm', '3gp', 'mp3', 'wav', 'm4a', 'ogg', 'aac', 'amr', 'opus'];
const _audioTypes = ['mp3', 'wav', 'm4a', 'ogg', 'aac', 'amr', 'opus'];

// Same limits for every way of adding the customer's feedback.
const _minSeconds = 30;
const _maxSeconds = 35 * 60;
const _maxUploadBytes = 500 * 1024 * 1024; // 500 MB

String _fileSize(int bytes) => bytes >= 1024 * 1024
    ? '${(bytes / (1024 * 1024)).toStringAsFixed(1)} MB'
    : '${(bytes / 1024).ceil()} KB';

class SurveyFlowScreen extends StatefulWidget {
  const SurveyFlowScreen({super.key, this.startProduct, this.keepUsage});

  /// Skip straight to the respondent step for this product ("next respondent").
  final RecentProduct? startProduct;

  /// Usage period carried over from the previous respondent.
  final String? keepUsage;

  @override
  State<SurveyFlowScreen> createState() => _SurveyFlowScreenState();
}

class _SurveyFlowScreenState extends State<SurveyFlowScreen> {
  final _api = SurveyApi();

  // Catalog
  List<CatalogCategory> _categories = [];
  List<CatalogProduct> _products = [];
  bool _loadingCatalog = true;
  String? _catalogError;
  String _search = '';
  int? _categoryId;
  CatalogProduct? _product;
  List<SurveyQuestionItem> _questions = [];
  bool _loadingQuestions = false;

  // Respondent
  final _name = TextEditingController();
  final _mobile = TextEditingController();
  final _age = TextEditingController();
  final _city = TextEditingController();
  final _address = TextEditingController();
  final _executive = TextEditingController();
  final _surveyLocation = TextEditingController();
  String? _gender;
  String? _usage;
  bool _consent = false;

  // Answers and recording
  final Map<int, String> _answers = {};
  // One text box per free-text question, and a key per question card so a
  // missed question can be scrolled into view.
  final Map<int, TextEditingController> _textControllers = {};
  final Map<int, GlobalKey> _questionKeys = {};
  int? _missingQuestionId; // highlighted in red after Continue
  RecordedMedia? _media;
  bool _checkingFile = false; // reading an uploaded file's length
  bool _silentConfirmed = false; // "use it anyway" for a recording that seems silent

  // GPS for this survey: locating | ok | off (service off) | denied | failed
  String _gps = 'locating';
  double? _lat;
  double? _lng;
  double? _accuracy;

  int _step = 0;
  String? _error;
  bool _submitting = false;

  // Steps: 0 category, 1 product, 2 respondent, 3 all questions, 4 recording, 5 review
  static const int _questionsStep = 3;
  int get _qCount => _questions.length;
  int get _recordingStep => 4;
  int get _reviewStep => 5;
  bool get _isQuestion => _step == _questionsStep;
  int get _answeredCount => _questions.where((q) => (_answers[q.id] ?? '').trim().isNotEmpty).length;

  bool get _hasEntries =>
      _name.text.trim().isNotEmpty || _answers.values.any((v) => v.trim().isNotEmpty) || _media != null;

  @override
  void initState() {
    super.initState();
    // Signed in: the survey is done by this account's executive.
    _executive.text = Session.user.value?.name ?? AppProfile.executiveName.value;
    _surveyLocation.text = AppProfile.lastSurveyLocation.value;
    _usage = widget.keepUsage;
    _loadCatalog();
    _captureLocation();
  }

  /// Location of this survey (optional): never blocks the survey.
  Future<void> _captureLocation({bool ask = false}) async {
    if (mounted && _gps != 'locating') setState(() => _gps = 'locating');
    try {
      if (!await Geolocator.isLocationServiceEnabled()) {
        if (mounted) setState(() => _gps = 'off');
        return;
      }
      var permission = await Geolocator.checkPermission();
      if (permission == LocationPermission.denied && ask) {
        permission = await Geolocator.requestPermission();
      }
      if (permission == LocationPermission.denied || permission == LocationPermission.deniedForever) {
        if (mounted) setState(() => _gps = 'denied');
        return;
      }
      final pos = await Geolocator.getCurrentPosition(
        locationSettings: const LocationSettings(accuracy: LocationAccuracy.high, timeLimit: Duration(seconds: 20)),
      );
      if (!mounted) return;
      setState(() {
        _lat = double.parse(pos.latitude.toStringAsFixed(6));
        _lng = double.parse(pos.longitude.toStringAsFixed(6));
        _accuracy = pos.accuracy;
        _gps = 'ok';
      });
    } catch (_) {
      if (mounted) setState(() => _gps = 'failed');
    }
  }

  @override
  void dispose() {
    for (final c in [_name, _mobile, _age, _city, _address, _executive, _surveyLocation, ..._textControllers.values]) {
      c.dispose();
    }
    super.dispose();
  }

  Future<void> _loadCatalog() async {
    // On first load (from initState) the flags are already set; setState
    // isn't allowed while the screen is still being created.
    if (!_loadingCatalog || _catalogError != null) {
      setState(() {
        _loadingCatalog = true;
        _catalogError = null;
      });
    }
    try {
      final results = await Future.wait([_api.categories(), _api.products()]);
      _categories = results[0] as List<CatalogCategory>;
      _products = results[1] as List<CatalogProduct>;
      final start = widget.startProduct;
      if (start != null) {
        final p = _products.where((x) => x.id == start.id).firstOrNull;
        if (p != null && p.surveyReady) {
          _categoryId = p.categoryId;
          await _chooseProduct(p, jump: false);
          if (mounted) setState(() => _step = 2);
        }
      }
    } catch (e) {
      _catalogError = apiErrorText(e);
    } finally {
      if (mounted) setState(() => _loadingCatalog = false);
    }
  }

  Future<void> _chooseProduct(CatalogProduct p, {bool jump = true}) async {
    setState(() {
      _product = p;
      _loadingQuestions = true;
      _error = null;
    });
    try {
      final qs = await _api.questions(p.id);
      if (!mounted) return;
      setState(() {
        _questions = qs;
        _answers.removeWhere((k, _) => !qs.any((q) => q.id == k));
        _textControllers.removeWhere((k, c) {
          final gone = !qs.any((q) => q.id == k);
          if (gone) c.dispose();
          return gone;
        });
        if (jump) _step = 2;
      });
    } catch (e) {
      if (mounted) setState(() => _error = apiErrorText(e));
    } finally {
      if (mounted) setState(() => _loadingQuestions = false);
    }
  }

  /* ---------------- Navigation ---------------- */

  void _go(int step) {
    FocusScope.of(context).unfocus();
    setState(() {
      _error = null;
      _missingQuestionId = null;
      _step = step;
    });
  }

  Future<void> _back() async {
    if (_step == 0 || (widget.startProduct != null && _step == 2)) {
      if (!_hasEntries || await _confirmLeave()) {
        if (mounted) Navigator.of(context).pop();
      }
      return;
    }
    _go(_step - 1);
  }

  Future<bool> _confirmLeave() => confirmAction(
        context,
        title: 'Leave this survey?',
        message: "The details and answers entered so far haven't been saved and will be lost.",
        confirmLabel: 'Leave',
        cancelLabel: 'Stay',
      );

  String? _validateRespondent() {
    if (_name.text.trim().isEmpty) return "Enter the respondent's name.";
    final digits = _mobile.text.replaceAll(RegExp(r'\D'), '');
    if (_mobile.text.trim().isNotEmpty && (digits.length < 10 || digits.length > 13)) {
      return 'Enter a valid mobile number (10 digits).';
    }
    if (_age.text.trim().isNotEmpty) {
      final a = int.tryParse(_age.text.trim());
      if (a == null || a < 1 || a > 120) return 'Enter an age between 1 and 120.';
    }
    if (_city.text.trim().isEmpty) return 'Enter the city or locality.';
    if (_executive.text.trim().isEmpty) return 'Enter your name as the survey executive.';
    if (_surveyLocation.text.trim().isEmpty) return 'Enter where this survey is taking place.';
    if (_usage == null) return 'Choose when the customer used the product.';
    if (!_consent) return "The respondent's consent is needed to continue.";
    return null;
  }

  void _next() {
    if (_step == 2) {
      final problem = _validateRespondent();
      if (problem != null) {
        setState(() => _error = problem);
        return;
      }
    } else if (_isQuestion) {
      final missing = _questions.where((q) => q.required && (_answers[q.id] ?? '').trim().isEmpty).firstOrNull;
      if (missing != null) {
        final number = _questions.indexOf(missing) + 1;
        setState(() {
          _error = 'Answer question $number: "${missing.text}"';
          _missingQuestionId = missing.id;
        });
        // Bring the missed question into view.
        final ctx = _questionKeys[missing.id]?.currentContext;
        if (ctx != null) {
          Scrollable.ensureVisible(ctx, duration: const Duration(milliseconds: 300), alignment: 0.1);
        }
        return;
      }
    } else if (_step == _recordingStep) {
      if (_media == null) {
        setState(() => _error = "Record or choose the customer's feedback first.");
        return;
      }
      if (_media!.durationSeconds > 0 && _media!.durationSeconds < _minSeconds) {
        setState(() => _error = 'The recording must be at least 30 seconds long.');
        return;
      }
      if (_media!.durationSeconds > _maxSeconds) {
        setState(() => _error = 'The recording can be at most 35 minutes long.');
        return;
      }
      if (_media!.seemsSilent && !_silentConfirmed) {
        _confirmSilent();
        return;
      }
    }
    _go(_step + 1);
  }

  void _answer(SurveyQuestionItem q, String value) {
    setState(() {
      _answers[q.id] = value;
      if (_missingQuestionId == q.id) {
        _missingQuestionId = null;
        _error = null;
      }
    });
  }

  /* ---------------- Recording ---------------- */

  Future<void> _confirmSilent() async {
    final ok = await confirmAction(
      context,
      title: 'This recording seems silent',
      message: "No voice was picked up, so the analysis will find nothing to analyze. Record again, or use it anyway if you're sure it has sound.",
      confirmLabel: 'Use it anyway',
      cancelLabel: 'Record again',
    );
    if (!mounted) return;
    if (ok) {
      setState(() => _silentConfirmed = true);
      _next();
    } else {
      setState(() => _media = null);
    }
  }

  Future<void> _record(bool video) async {
    final result = await Navigator.of(context).push<RecordedMedia>(
      MaterialPageRoute(
        builder: (_) => video ? const RecordVideoScreen(returnResult: true) : const RecordAudioScreen(returnResult: true),
      ),
    );
    if (result != null && mounted) {
      setState(() {
        _media = result;
        _silentConfirmed = false;
        _error = null;
      });
    }
  }

  /// Length of an audio or video file on the phone (null if it can't be read).
  Future<double?> _probeDuration(String path) async {
    final c = VideoPlayerController.file(File(path));
    try {
      await c.initialize().timeout(const Duration(seconds: 15));
      final d = c.value.duration;
      return d.inMilliseconds > 0 ? d.inMilliseconds / 1000 : null;
    } catch (_) {
      return null;
    } finally {
      await c.dispose();
    }
  }

  /// Upload from device: browse the phone's storage for an audio or video file.
  Future<void> _pickFile() async {
    // This file_picker version returns a non-null list from pickFiles (empty if
    // the person cancels), so errors are caught with try/catch.
    final List<PlatformFile> picked;
    try {
      picked = await FilePicker.pickFiles(type: FileType.custom, allowedExtensions: _fileTypes);
    } catch (e) {
      if (mounted) setState(() => _error = "The phone's files couldn't be opened: $e");
      return;
    }
    if (picked.isEmpty || !mounted) return;
    final f = picked.first;
    if (f.path == null) {
      setState(() => _error = "This file can't be read. Copy it to the phone's storage and choose it again.");
      return;
    }
    final ext = f.name.contains('.') ? f.name.split('.').last.toLowerCase() : '';
    if (!_fileTypes.contains(ext)) {
      setState(() => _error = 'Choose an audio file (MP3, M4A, WAV, AAC, OGG) or a video (MP4, MOV, MKV, WEBM, 3GP).');
      return;
    }
    // Read the size from the file itself (works with every file_picker version).
    int size;
    try {
      size = await File(f.path!).length();
    } catch (_) {
      setState(() => _error = "This file can't be read. Copy it to the phone's storage and choose it again.");
      return;
    }
    if (!mounted) return;
    if (size == 0) {
      setState(() => _error = 'This file is empty. Choose another recording.');
      return;
    }
    if (size > _maxUploadBytes) {
      setState(() => _error = 'This file is ${_fileSize(size)}. Choose one under ${_fileSize(_maxUploadBytes)}, or record the feedback instead.');
      return;
    }

    setState(() {
      _checkingFile = true;
      _error = null;
    });
    final seconds = await _probeDuration(f.path!);
    if (!mounted) return;
    setState(() => _checkingFile = false);
    if (seconds != null && seconds < _minSeconds) {
      setState(() => _error = 'This recording is ${formatDuration(seconds)}. The feedback must be at least 30 seconds long.');
      return;
    }
    if (seconds != null && seconds > _maxSeconds) {
      setState(() => _error = 'This recording is ${formatDuration(seconds)}. The feedback can be at most 35 minutes long.');
      return;
    }
    setState(() {
      _media = RecordedMedia(
        path: f.path!,
        name: f.name,
        kind: _audioTypes.contains(ext) ? 'audio' : 'video',
        durationSeconds: seconds ?? 0, // 0 = couldn't be read here; the server checks it
        source: 'file',
        sizeBytes: size,
      );
      _silentConfirmed = false;
      _error = null;
    });
  }

  /// "Change": record again (audio or video) or choose another file.
  Future<void> _changeRecording() async {
    final choice = await showModalBottomSheet<String>(
      context: context,
      showDragHandle: true,
      builder: (ctx) => SafeArea(
        child: Padding(
          padding: const EdgeInsets.fromLTRB(16, 0, 16, 16),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              Text('Replace the recording', style: TextStyle(fontSize: 18, fontWeight: FontWeight.w800, color: AppColors.ink)),
              const SizedBox(height: 12),
              _SheetOption(icon: Icons.mic_rounded, color: AppColors.record, title: 'Record audio', onTap: () => Navigator.pop(ctx, 'audio')),
              _SheetOption(icon: Icons.videocam_rounded, color: AppColors.video, title: 'Record video with audio', onTap: () => Navigator.pop(ctx, 'video')),
              _SheetOption(icon: Icons.upload_file_rounded, color: AppColors.brand, title: 'Upload from device', onTap: () => Navigator.pop(ctx, 'file')),
            ],
          ),
        ),
      ),
    );
    if (!mounted || choice == null) return;
    if (choice == 'file') {
      await _pickFile();
    } else {
      await _record(choice == 'video');
    }
  }

  /* ---------------- Submit ---------------- */

  Future<void> _submit() async {
    final p = _product!;
    final media = _media!;
    setState(() => _submitting = true);
    try {
      AppProfile.executiveName.value = _executive.text.trim();
      AppProfile.lastSurveyLocation.value = _surveyLocation.text.trim();
      final recent = RecentProduct(id: p.id, name: p.name, categoryId: p.categoryId, categoryName: _categoryName(p));
      await AppProfile.rememberProduct(recent);

      await SurveyQueue.add(
        productId: p.id,
        productName: p.name,
        categoryName: _categoryName(p),
        respondent: {
          'name': _name.text.trim(),
          'mobile': _mobile.text.trim().isEmpty ? null : _mobile.text.trim(),
          'address': _address.text.trim().isEmpty ? null : _address.text.trim(),
          'gender': _gender,
          'age': int.tryParse(_age.text.trim()),
          'location': _city.text.trim(),
          'consent_given': true,
        },
        response: {
          'product_id': p.id,
          'executive_name': _executive.text.trim(),
          'usage_period': _usage,
          'location': _surveyLocation.text.trim(),
          'latitude': _lat,
          'longitude': _lng,
        },
        answers: [for (final q in _questions) {'question_id': q.id, 'value': (_answers[q.id] ?? '').trim()}],
        mediaSourcePath: media.path,
        mediaName: media.name,
        mediaKind: media.kind,
        durationSeconds: media.durationSeconds,
      );
      if (!mounted) return;
      Navigator.of(context).pushReplacement(
        MaterialPageRoute(builder: (_) => SurveySubmittedScreen(product: recent, usage: _usage)),
      );
    } catch (e) {
      if (mounted) {
        setState(() {
          _submitting = false;
          _error = "Couldn't save the survey on this phone: $e";
        });
      }
    }
  }

  String _categoryName(CatalogProduct p) {
    if (p.categoryName.isNotEmpty) return p.categoryName;
    return _categories.where((c) => c.id == p.categoryId).firstOrNull?.name ?? '';
  }

  /* ================================================================
     Build
     ================================================================ */

  String get _title {
    if (_step == 0) return 'Choose a category';
    if (_step == 1) return 'Choose a product';
    if (_step == 2) return 'Respondent';
    if (_isQuestion) return 'Questions';
    if (_step == _recordingStep) return "Customer's feedback";
    return 'Review and submit';
  }

  @override
  Widget build(BuildContext context) {
    const total = 6;
    return PopScope(
      canPop: false,
      onPopInvokedWithResult: (didPop, result) {
        if (!didPop) _back();
      },
      child: Scaffold(
        appBar: AppBar(
          backgroundColor: AppColors.bg,
          surfaceTintColor: Colors.transparent,
          leading: IconButton(
            icon: Icon(_step == 0 ? Icons.close_rounded : Icons.arrow_back_rounded),
            tooltip: _step == 0 ? 'Close' : 'Back',
            onPressed: _back,
          ),
          title: Text(_title, style: const TextStyle(fontSize: 18, fontWeight: FontWeight.w700)),
          bottom: PreferredSize(
            preferredSize: const Size.fromHeight(4),
            child: LinearProgressIndicator(
              value: (_step + 1) / total,
              minHeight: 4,
              color: AppColors.brand,
              backgroundColor: AppColors.brandSoft,
            ),
          ),
        ),
        body: SafeArea(child: _body()),
        bottomNavigationBar: _bottomBar(),
      ),
    );
  }

  Widget _body() {
    if (_loadingCatalog) return const Center(child: CircularProgressIndicator());
    if (_catalogError != null && _categories.isEmpty) {
      return _Message(
        icon: Icons.cloud_off_rounded,
        title: "Couldn't load products",
        text: '$_catalogError\nProducts are needed to start a survey.',
        action: FilledButton.icon(onPressed: _loadCatalog, icon: const Icon(Icons.refresh_rounded), label: const Text('Try again')),
      );
    }
    if (_step == 0) return _categoryStep();
    if (_step == 1) return _productStep();
    if (_step == 2) return _respondentStep();
    if (_isQuestion) return _questionsView();
    if (_step == _recordingStep) return _recordingStepView();
    return _reviewStepView();
  }

  Widget? _bottomBar() {
    if (_loadingCatalog || _step < 2) return null;
    final isReview = _step == _reviewStep;
    return SafeArea(
      top: false,
      child: Container(
        padding: const EdgeInsets.fromLTRB(16, 10, 16, 12),
        decoration: BoxDecoration(
          color: AppColors.surface,
          border: Border(top: BorderSide(color: AppColors.line)),
        ),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            if (_error != null) ...[
              Container(
                width: double.infinity,
                padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
                margin: const EdgeInsets.only(bottom: 10),
                decoration: BoxDecoration(color: AppColors.negSoft, borderRadius: BorderRadius.circular(12)),
                child: Text(_error!, style: TextStyle(color: AppColors.neg, fontSize: 14, height: 1.35)),
              ),
            ],
            SizedBox(
              width: double.infinity,
              child: FilledButton(
                onPressed: _submitting ? null : (isReview ? _submit : _next),
                child: Text(isReview ? (_submitting ? 'Saving…' : 'Submit survey') : 'Continue'),
              ),
            ),
          ],
        ),
      ),
    );
  }

  /* ---------------- 1. Category ---------------- */

  Widget _categoryStep() {
    final query = _search.trim().toLowerCase();
    final list = _categories.where((c) => query.isEmpty || c.name.toLowerCase().contains(query)).toList();
    int count(CatalogCategory c) => _products.where((p) => p.categoryId == c.id).length;
    return ListView(
      padding: const EdgeInsets.fromLTRB(16, 16, 16, 24),
      children: [
        TextField(
          decoration: _inputDecoration('Search categories').copyWith(prefixIcon: const Icon(Icons.search_rounded)),
          onChanged: (v) => setState(() => _search = v),
        ),
        const SizedBox(height: 14),
        if (list.isEmpty)
          const _Message(icon: Icons.category_outlined, title: 'No categories found', text: 'Try a different search.'),
        for (final c in list)
          _ChoiceCard(
            title: c.name,
            subtitle: '${count(c)} ${count(c) == 1 ? 'product' : 'products'}',
            icon: Icons.category_rounded,
            onTap: () {
              setState(() {
                _categoryId = c.id;
                _search = '';
              });
              _go(1);
            },
          ),
      ],
    );
  }

  /* ---------------- 2. Product ---------------- */

  Widget _productStep() {
    final list = _products.where((p) => p.categoryId == _categoryId).toList()..sort((a, b) => a.name.compareTo(b.name));
    return Stack(
      children: [
        ListView(
          padding: const EdgeInsets.fromLTRB(16, 16, 16, 24),
          children: [
            if (list.isEmpty)
              const _Message(icon: Icons.inventory_2_outlined, title: 'No products here yet', text: 'Choose another category.'),
            for (final p in list)
              _ChoiceCard(
                title: p.name,
                subtitle: p.surveyReady
                    ? '${p.questionCount} questions · ${p.responsesCollected} ${p.responsesCollected == 1 ? 'survey' : 'surveys'} so far'
                    : 'Not ready: needs 5–10 questions',
                icon: Icons.shopping_bag_rounded,
                enabled: p.surveyReady,
                onTap: () => _chooseProduct(p),
              ),
          ],
        ),
        if (_loadingQuestions)
          Positioned.fill(
            child: ColoredBox(color: AppColors.scrim, child: Center(child: CircularProgressIndicator())),
          ),
      ],
    );
  }

  /* ---------------- 3. Respondent ---------------- */

  Widget _respondentStep() {
    return ListView(
      padding: const EdgeInsets.fromLTRB(16, 12, 16, 24),
      children: [
        _ProductBanner(name: _product?.name ?? '', category: _product == null ? '' : _categoryName(_product!)),
        const _SectionLabel('Customer'),
        _field(_name, 'Full name *', capitalization: TextCapitalization.words, autofill: const [AutofillHints.name]),
        _field(_mobile, 'Mobile (optional)', keyboard: TextInputType.phone),
        Row(
          children: [
            Expanded(child: _field(_age, 'Age', keyboard: TextInputType.number)),
            const SizedBox(width: 12),
            Expanded(
              child: Padding(
                padding: const EdgeInsets.only(bottom: 12),
                child: DropdownButtonFormField<String>(
                  value: _gender,
                  decoration: _inputDecoration('Gender'),
                  items: [for (final g in _genders) DropdownMenuItem(value: g, child: Text(g))],
                  onChanged: (v) => setState(() => _gender = v),
                ),
              ),
            ),
          ],
        ),
        _field(_city, 'City or locality *', capitalization: TextCapitalization.words),
        _field(_address, 'Address (optional)', capitalization: TextCapitalization.sentences),
        const _SectionLabel('This survey'),
        if (Session.user.value != null)
          Padding(
            padding: const EdgeInsets.only(bottom: 12),
            child: Row(
              children: [
                Icon(Icons.badge_outlined, size: 20, color: AppColors.muted),
                const SizedBox(width: 8),
                Expanded(
                  child: Text.rich(TextSpan(children: [
                    TextSpan(text: 'Surveyed by ', style: TextStyle(color: AppColors.muted)),
                    TextSpan(text: _executive.text, style: TextStyle(fontWeight: FontWeight.w700, color: AppColors.ink)),
                  ])),
                ),
              ],
            ),
          )
        else
          _field(_executive, 'Your name (executive) *', capitalization: TextCapitalization.words),
        _field(_surveyLocation, 'Survey location *', capitalization: TextCapitalization.words, hint: 'e.g. D-Mart, Andheri West'),
        _locationRow(),
        const SizedBox(height: 12),
        Text('When did they use ${_product?.name ?? 'the product'}? *',
            style: const TextStyle(fontSize: 14, fontWeight: FontWeight.w600)),
        const SizedBox(height: 8),
        Wrap(
          spacing: 8,
          runSpacing: 8,
          children: [
            for (final u in _usagePeriods)
              ChoiceChip(
                label: Text(u.$2),
                selected: _usage == u.$1,
                onSelected: (_) => setState(() {
                  _usage = u.$1;
                  _error = null;
                }),
                selectedColor: AppColors.brandSoft,
                labelStyle: TextStyle(
                  fontWeight: _usage == u.$1 ? FontWeight.w700 : FontWeight.w500,
                  color: _usage == u.$1 ? AppColors.brand : AppColors.ink2,
                ),
              ),
          ],
        ),
        const SizedBox(height: 16),
        Material(
          color: _consent ? AppColors.posSoft : AppColors.surface,
          shape: RoundedRectangleBorder(
            borderRadius: BorderRadius.circular(14),
            side: BorderSide(color: _consent ? AppColors.pos : AppColors.line),
          ),
          child: CheckboxListTile(
            value: _consent,
            onChanged: (v) => setState(() {
              _consent = v ?? false;
              _error = null;
            }),
            controlAffinity: ListTileControlAffinity.leading,
            activeColor: AppColors.brand,
            contentPadding: const EdgeInsets.symmetric(horizontal: 6),
            title: const Text(
              'The respondent agrees to the use of their details, answers and recording to analyze feedback on this product. '
              'Name, mobile and address never appear in reports. *',
              style: TextStyle(fontSize: 13.5, height: 1.4),
            ),
          ),
        ),
      ],
    );
  }

  Widget _field(
    TextEditingController c,
    String label, {
    TextInputType? keyboard,
    TextCapitalization capitalization = TextCapitalization.none,
    String? hint,
    List<String>? autofill,
  }) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 12),
      child: TextField(
        controller: c,
        keyboardType: keyboard,
        textCapitalization: capitalization,
        autofillHints: autofill,
        textInputAction: TextInputAction.next,
        decoration: _inputDecoration(label).copyWith(hintText: hint),
        onChanged: (_) {
          if (_error != null) setState(() => _error = null);
        },
      ),
    );
  }

  Widget _locationRow() {
    final (IconData icon, Color color, String text, bool retry) = switch (_gps) {
      'ok' => (Icons.location_on_rounded, AppColors.pos, 'Location added (±${(_accuracy ?? 0).round()} m)', false),
      'locating' => (Icons.my_location_rounded, AppColors.muted, 'Finding location…', false),
      'off' => (Icons.location_off_rounded, AppColors.warn, 'Location is off on this phone', true),
      'denied' => (Icons.location_disabled_rounded, AppColors.warn, 'Location not allowed (optional)', true),
      _ => (Icons.location_searching_rounded, AppColors.warn, "Couldn't get the location (optional)", true),
    };
    return Container(
      padding: const EdgeInsets.fromLTRB(12, 6, 4, 6),
      decoration: BoxDecoration(color: AppColors.surface, borderRadius: BorderRadius.circular(12), border: Border.all(color: AppColors.line)),
      child: Row(
        children: [
          Icon(icon, size: 20, color: color),
          const SizedBox(width: 8),
          Expanded(child: Text(text, style: TextStyle(fontSize: 14, color: AppColors.ink2))),
          if (_gps == 'locating')
            const Padding(
              padding: EdgeInsets.all(10),
              child: SizedBox(width: 16, height: 16, child: CircularProgressIndicator(strokeWidth: 2)),
            )
          else if (retry)
            TextButton(onPressed: () => _captureLocation(ask: true), child: const Text('Try again'))
          else
            TextButton(onPressed: () => _captureLocation(), child: const Text('Update')),
        ],
      ),
    );
  }

  /* ---------------- 4. Questions: all on one page ---------------- */

  Widget _questionsView() {
    final done = _answeredCount;
    return ListView(
      padding: const EdgeInsets.fromLTRB(16, 12, 16, 24),
      children: [
        _ProductBanner(name: _product?.name ?? '', category: _product == null ? '' : _categoryName(_product!)),
        Padding(
          padding: const EdgeInsets.only(top: 4, bottom: 12),
          child: Row(
            children: [
              Text('$done of $_qCount answered', style: TextStyle(fontSize: 14, fontWeight: FontWeight.w700, color: AppColors.ink)),
              const SizedBox(width: 12),
              Expanded(
                child: ClipRRect(
                  borderRadius: BorderRadius.circular(999),
                  child: LinearProgressIndicator(
                    value: _qCount == 0 ? 0 : done / _qCount,
                    minHeight: 6,
                    color: AppColors.brand,
                    backgroundColor: AppColors.brandSoft,
                  ),
                ),
              ),
            ],
          ),
        ),
        // All questions on one sheet, separated by thin lines.
        Container(
          padding: const EdgeInsets.symmetric(horizontal: 16),
          decoration: BoxDecoration(
            color: AppColors.surface,
            borderRadius: BorderRadius.circular(18),
            border: Border.all(color: AppColors.line),
          ),
          child: Column(
            children: [
              for (var i = 0; i < _questions.length; i++) _questionCard(i, _questions[i], last: i == _questions.length - 1),
            ],
          ),
        ),
      ],
    );
  }

  Widget _questionCard(int index, SurveyQuestionItem q, {bool last = false}) {
    final key = _questionKeys.putIfAbsent(q.id, () => GlobalKey());
    final value = _answers[q.id] ?? '';
    final answered = value.trim().isNotEmpty;
    final missing = _missingQuestionId == q.id;

    Widget answer;
    switch (q.type) {
      case 'rating':
        answer = _RatingScale(value: value, onChanged: (v) => _answer(q, v));
      case 'yes_no':
        answer = Row(
          children: [
            for (final o in const ['Yes', 'No'])
              Expanded(child: _RadioOption(label: o, selected: value == o, onTap: () => _answer(q, o))),
          ],
        );
      case 'single_choice':
        answer = Column(
          children: [
            for (final o in q.options) _RadioOption(label: o, selected: value == o, onTap: () => _answer(q, o)),
          ],
        );
      default:
        final controller = _textControllers.putIfAbsent(q.id, () => TextEditingController(text: value));
        answer = TextField(
          controller: controller,
          minLines: 2,
          maxLines: 5,
          textCapitalization: TextCapitalization.sentences,
          decoration: const InputDecoration(hintText: 'Write down what the customer says'),
          onChanged: (v) => _answer(q, v),
        );
    }

    final numberColor = missing ? AppColors.neg : (answered ? AppColors.brand : AppColors.muted);
    return Container(
      key: key,
      padding: const EdgeInsets.symmetric(vertical: 18),
      decoration: BoxDecoration(
        border: last ? null : Border(bottom: BorderSide(color: AppColors.line)),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text.rich(
            TextSpan(
              children: [
                TextSpan(text: '${index + 1}.  ', style: TextStyle(color: numberColor, fontWeight: FontWeight.w800)),
                TextSpan(text: q.text),
                if (q.required)
                  TextSpan(text: ' *', style: TextStyle(color: AppColors.neg))
                else
                  TextSpan(
                    text: '  (optional)',
                    style: TextStyle(fontSize: 13, fontWeight: FontWeight.w400, color: AppColors.muted),
                  ),
              ],
            ),
            style: TextStyle(fontSize: 16, height: 1.4, fontWeight: FontWeight.w600, color: AppColors.ink),
          ),
          if (missing)
            Padding(
              padding: const EdgeInsets.only(top: 4),
              child: Text('Please answer this question', style: TextStyle(fontSize: 13, color: AppColors.neg)),
            ),
          const SizedBox(height: 10),
          answer,
        ],
      ),
    );
  }

  /* ---------------- 5. Recording ---------------- */

  Widget _recordingStepView() {
    final m = _media;
    return ListView(
      padding: const EdgeInsets.fromLTRB(16, 16, 16, 24),
      children: [
        Text(
          'Ask the customer for their overall experience: what they liked, what they didn\'t, and what they would change.',
          style: TextStyle(fontSize: 15.5, height: 1.45, color: AppColors.ink2),
        ),
        const SizedBox(height: 6),
        Text('30 seconds to 35 minutes.', style: TextStyle(fontSize: 13.5, color: AppColors.muted)),
        const SizedBox(height: 18),
        if (m == null && _checkingFile) ...[
          Container(
            padding: const EdgeInsets.all(18),
            decoration: BoxDecoration(color: AppColors.surface, borderRadius: BorderRadius.circular(18), border: Border.all(color: AppColors.line)),
            child: Row(
              children: [
                const SizedBox(width: 24, height: 24, child: CircularProgressIndicator(strokeWidth: 3)),
                const SizedBox(width: 14),
                Expanded(child: Text('Checking the file…', style: TextStyle(fontSize: 15.5, color: AppColors.ink))),
              ],
            ),
          ),
        ] else if (m == null) ...[
          Text('How would you like to add the feedback?',
              style: TextStyle(fontSize: 13, fontWeight: FontWeight.w800, letterSpacing: 0.3, color: AppColors.muted)),
          const SizedBox(height: 10),
          _BigAction(
            icon: Icons.mic_rounded,
            color: AppColors.record,
            title: 'Record audio',
            subtitle: 'Record the customer speaking · small file, uploads fast',
            badge: 'Recommended',
            onTap: () => _record(false),
          ),
          const SizedBox(height: 12),
          _BigAction(
            icon: Icons.videocam_rounded,
            color: AppColors.video,
            title: 'Record video with audio',
            subtitle: 'Film the product while the customer talks',
            onTap: () => _record(true),
          ),
          const SizedBox(height: 12),
          _BigAction(
            icon: Icons.upload_file_rounded,
            color: AppColors.brand,
            title: 'Upload from device',
            subtitle: 'Choose an audio or video file already on this phone · MP3, M4A, WAV, MP4…',
            onTap: _pickFile,
          ),
          const SizedBox(height: 10),
          Text('Any option: 30 seconds to 35 minutes${' · files up to ${_fileSize(_maxUploadBytes)}'}.',
              textAlign: TextAlign.center, style: TextStyle(fontSize: 12.5, color: AppColors.muted)),
        ] else ...[
          MediaPlayerCard(
            key: ValueKey(m.path),
            filePath: m.path,
            isVideo: m.isVideo,
            durationHint: m.durationSeconds,
          ),
          if (m.seemsSilent)
            Container(
              margin: const EdgeInsets.only(top: 12),
              padding: const EdgeInsets.all(12),
              decoration: BoxDecoration(
                color: AppColors.warnSoft,
                borderRadius: BorderRadius.circular(12),
                border: Border.all(color: AppColors.warnLine),
              ),
              child: Row(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Icon(Icons.mic_off_rounded, color: AppColors.warn),
                  const SizedBox(width: 10),
                  Expanded(
                    child: Text(
                      'No sound was picked up in this recording. Check the microphone and record again. '
                      'On an emulator: Extended controls > Microphone > "Virtual microphone uses host audio input".',
                      style: TextStyle(fontSize: 13.5, height: 1.4, color: AppColors.warnDeep),
                    ),
                  ),
                ],
              ),
            ),
          const SizedBox(height: 12),
          Row(
            children: [
              Icon(
                m.isUploadedFile ? Icons.upload_file_rounded : (m.isVideo ? Icons.videocam_rounded : Icons.mic_rounded),
                color: AppColors.muted,
                size: 20,
              ),
              const SizedBox(width: 8),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      m.isUploadedFile
                          ? 'Uploaded ${m.isVideo ? 'video' : 'audio'} file'
                          : (m.isVideo ? 'Recorded video with audio' : 'Recorded audio'),
                      style: TextStyle(fontSize: 15, fontWeight: FontWeight.w700, color: AppColors.ink),
                    ),
                    Text(
                      [
                        if (m.isUploadedFile) m.name,
                        if (m.durationSeconds > 0) formatDuration(m.durationSeconds),
                        if (m.sizeBytes != null) _fileSize(m.sizeBytes!),
                      ].join(' · '),
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: TextStyle(fontSize: 13, color: AppColors.muted),
                    ),
                  ],
                ),
              ),
              TextButton.icon(
                onPressed: _changeRecording,
                icon: const Icon(Icons.swap_horiz_rounded),
                label: const Text('Change'),
              ),
            ],
          ),
        ],
      ],
    );
  }

  /* ---------------- 6. Review ---------------- */

  Widget _reviewStepView() {
    final usage = _usagePeriods.where((u) => u.$1 == _usage).firstOrNull?.$2 ?? '—';
    Widget row(String label, String value) => Padding(
          padding: const EdgeInsets.symmetric(vertical: 6),
          child: Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              SizedBox(width: 118, child: Text(label, style: TextStyle(fontSize: 14, color: AppColors.muted))),
              Expanded(child: Text(value, style: const TextStyle(fontSize: 15, fontWeight: FontWeight.w600))),
            ],
          ),
        );

    return ListView(
      padding: const EdgeInsets.fromLTRB(16, 16, 16, 24),
      children: [
        _ReviewCard(
          title: 'Survey',
          onEdit: () => _go(2),
          children: [
            row('Product', _product?.name ?? ''),
            row('Respondent', [
              _name.text.trim(),
              if (_gender != null) _gender!,
              if (_age.text.trim().isNotEmpty) '${_age.text.trim()} yrs',
            ].join(' · ')),
            row('City', _city.text.trim()),
            row('Used', usage),
            row('Location', _surveyLocation.text.trim()),
          ],
        ),
        _ReviewCard(
          title: 'Answers',
          onEdit: () => _go(_questionsStep),
          children: [
            for (final q in _questions)
              Padding(
                padding: const EdgeInsets.symmetric(vertical: 6),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(q.text, style: TextStyle(fontSize: 13.5, color: AppColors.muted)),
                    const SizedBox(height: 2),
                    Text(
                      (_answers[q.id] ?? '').trim().isEmpty
                          ? '—'
                          : q.type == 'rating'
                              ? '${_answers[q.id]} / 5'
                              : _answers[q.id]!,
                      style: const TextStyle(fontSize: 15, fontWeight: FontWeight.w600),
                    ),
                  ],
                ),
              ),
          ],
        ),
        _ReviewCard(
          title: 'Recording',
          onEdit: () => _go(_recordingStep),
          children: [
            row(
              _media?.isVideo == true ? 'Video' : 'Voice',
              _media == null
                  ? '—'
                  : (_media!.durationSeconds > 0 ? formatDuration(_media!.durationSeconds) : _media!.name),
            ),
          ],
        ),
        Padding(
          padding: EdgeInsets.only(top: 4),
          child: Text(
            "Submitting saves the survey on this phone. It uploads and is analyzed in the background, "
            "even if the connection is weak; you can start the next respondent straight away.",
            style: TextStyle(fontSize: 13.5, height: 1.45, color: AppColors.muted),
          ),
        ),
      ],
    );
  }
}

/* ================================================================
   After submitting
   ================================================================ */

class SurveySubmittedScreen extends StatelessWidget {
  const SurveySubmittedScreen({super.key, required this.product, this.usage});

  final RecentProduct product;
  final String? usage;

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: SafeArea(
        child: Padding(
          padding: const EdgeInsets.fromLTRB(24, 24, 24, 16),
          child: Column(
            children: [
              const Spacer(),
              Container(
                width: 84,
                height: 84,
                decoration: BoxDecoration(color: AppColors.posSoft, shape: BoxShape.circle),
                child: Icon(Icons.check_rounded, size: 46, color: AppColors.pos),
              ),
              const SizedBox(height: 20),
              const Text('Survey saved', style: TextStyle(fontSize: 26, fontWeight: FontWeight.w800)),
              const SizedBox(height: 10),
              Text(
                'It\'s uploading and will be analyzed in the background. Follow its progress in History.',
                textAlign: TextAlign.center,
                style: TextStyle(fontSize: 15.5, height: 1.45, color: AppColors.muted),
              ),
              const Spacer(),
              SizedBox(
                width: double.infinity,
                child: FilledButton.icon(
                  onPressed: () => Navigator.of(context).pushReplacement(
                    MaterialPageRoute(builder: (_) => SurveyFlowScreen(startProduct: product, keepUsage: usage)),
                  ),
                  icon: const Icon(Icons.person_add_alt_1_rounded),
                  label: Text('Next respondent · ${product.name}', overflow: TextOverflow.ellipsis),
                ),
              ),
              const SizedBox(height: 10),
              SizedBox(
                width: double.infinity,
                child: OutlinedButton(
                  onPressed: () => Navigator.of(context).pop(),
                  child: const Text('Back to home'),
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

/* ================================================================
   Small widgets
   ================================================================ */

InputDecoration _inputDecoration(String label) => InputDecoration(
      labelText: label,
      filled: true,
      fillColor: AppColors.surface,
      contentPadding: const EdgeInsets.symmetric(horizontal: 14, vertical: 14),
      border: OutlineInputBorder(borderRadius: BorderRadius.circular(14), borderSide: BorderSide(color: AppColors.line)),
      enabledBorder:
          OutlineInputBorder(borderRadius: BorderRadius.circular(14), borderSide: BorderSide(color: AppColors.line)),
      focusedBorder:
          OutlineInputBorder(borderRadius: BorderRadius.circular(14), borderSide: BorderSide(color: AppColors.brand, width: 2)),
    );

class _SectionLabel extends StatelessWidget {
  const _SectionLabel(this.text);

  final String text;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(top: 8, bottom: 10),
      child: Text(
        text.toUpperCase(),
        style: TextStyle(fontSize: 12, fontWeight: FontWeight.w800, letterSpacing: 0.8, color: AppColors.brand),
      ),
    );
  }
}

class _ProductBanner extends StatelessWidget {
  const _ProductBanner({required this.name, required this.category});

  final String name;
  final String category;

  @override
  Widget build(BuildContext context) {
    return Container(
      margin: const EdgeInsets.only(bottom: 8),
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(color: AppColors.brandSoft, borderRadius: BorderRadius.circular(14)),
      child: Row(
        children: [
          Icon(Icons.shopping_bag_rounded, color: AppColors.brand),
          const SizedBox(width: 10),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(name, style: const TextStyle(fontSize: 15.5, fontWeight: FontWeight.w700)),
                if (category.isNotEmpty) Text(category, style: TextStyle(fontSize: 13, color: AppColors.muted)),
              ],
            ),
          ),
        ],
      ),
    );
  }
}

/// One choice: a radio circle and its label (also used for Yes / No).
class _RadioOption extends StatelessWidget {
  const _RadioOption({required this.label, required this.selected, required this.onTap});

  final String label;
  final bool selected;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return Semantics(
      inMutuallyExclusiveGroup: true,
      checked: selected,
      button: true,
      child: InkWell(
        onTap: onTap,
        borderRadius: BorderRadius.circular(10),
        child: Padding(
          padding: const EdgeInsets.symmetric(vertical: 9, horizontal: 2),
          child: Row(
            children: [
              AnimatedContainer(
                duration: const Duration(milliseconds: 150),
                width: 22,
                height: 22,
                decoration: BoxDecoration(
                  shape: BoxShape.circle,
                  border: Border.all(color: selected ? AppColors.brand : AppColors.muted, width: 2),
                ),
                alignment: Alignment.center,
                child: AnimatedContainer(
                  duration: const Duration(milliseconds: 150),
                  width: selected ? 11 : 0,
                  height: selected ? 11 : 0,
                  decoration: BoxDecoration(shape: BoxShape.circle, color: AppColors.brand),
                ),
              ),
              const SizedBox(width: 14),
              Expanded(
                child: Text(
                  label,
                  style: TextStyle(
                    fontSize: 15.5,
                    fontWeight: selected ? FontWeight.w700 : FontWeight.w400,
                    color: selected ? AppColors.ink : AppColors.ink2,
                  ),
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

/// 1-5 rating: five round buttons with "Poor" and "Excellent" under the ends.
class _RatingScale extends StatelessWidget {
  const _RatingScale({required this.value, required this.onChanged});

  final String value;
  final ValueChanged<String> onChanged;

  @override
  Widget build(BuildContext context) {
    return Column(
      children: [
        Row(
          mainAxisAlignment: MainAxisAlignment.spaceBetween,
          children: [
            for (var n = 1; n <= 5; n++)
              Semantics(
                button: true,
                selected: value == '$n',
                label: '$n out of 5',
                child: InkWell(
                  customBorder: const CircleBorder(),
                  onTap: () => onChanged('$n'),
                  child: AnimatedContainer(
                    duration: const Duration(milliseconds: 150),
                    width: 46,
                    height: 46,
                    alignment: Alignment.center,
                    decoration: BoxDecoration(
                      shape: BoxShape.circle,
                      color: value == '$n' ? AppColors.brand : Colors.transparent,
                      border: Border.all(color: value == '$n' ? AppColors.brand : AppColors.line, width: 1.5),
                    ),
                    child: Text(
                      '$n',
                      style: TextStyle(
                        fontSize: 17,
                        fontWeight: FontWeight.w700,
                        color: value == '$n' ? AppColors.onBrand : AppColors.ink2,
                      ),
                    ),
                  ),
                ),
              ),
          ],
        ),
        Padding(
          padding: const EdgeInsets.only(top: 6),
          child: Row(
            mainAxisAlignment: MainAxisAlignment.spaceBetween,
            children: [
              Text('Poor', style: TextStyle(fontSize: 12, color: AppColors.muted)),
              Text('Excellent', style: TextStyle(fontSize: 12, color: AppColors.muted)),
            ],
          ),
        ),
      ],
    );
  }
}

class _ChoiceCard extends StatelessWidget {
  const _ChoiceCard({
    required this.title,
    required this.subtitle,
    required this.icon,
    required this.onTap,
    this.enabled = true,
  });

  final String title;
  final String subtitle;
  final IconData icon;
  final VoidCallback onTap;
  final bool enabled;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 10),
      child: Opacity(
        opacity: enabled ? 1 : 0.5,
        child: Material(
          color: AppColors.surface,
          shape: RoundedRectangleBorder(
            borderRadius: BorderRadius.circular(16),
            side: BorderSide(color: AppColors.line),
          ),
          child: InkWell(
            borderRadius: BorderRadius.circular(16),
            onTap: enabled ? onTap : null,
            child: Padding(
              padding: const EdgeInsets.all(14),
              child: Row(
                children: [
                  Container(
                    width: 44,
                    height: 44,
                    decoration: BoxDecoration(color: AppColors.brandSoft, borderRadius: BorderRadius.circular(12)),
                    child: Icon(icon, color: AppColors.brand),
                  ),
                  const SizedBox(width: 14),
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(title, style: const TextStyle(fontSize: 16, fontWeight: FontWeight.w700)),
                        const SizedBox(height: 2),
                        Text(subtitle, style: TextStyle(fontSize: 13, color: AppColors.muted)),
                      ],
                    ),
                  ),
                  if (enabled) Icon(Icons.chevron_right_rounded, color: AppColors.muted),
                ],
              ),
            ),
          ),
        ),
      ),
    );
  }
}

class _SheetOption extends StatelessWidget {
  const _SheetOption({required this.icon, required this.color, required this.title, required this.onTap});

  final IconData icon;
  final Color color;
  final String title;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return ListTile(
      contentPadding: const EdgeInsets.symmetric(horizontal: 4),
      leading: CircleAvatar(backgroundColor: color, child: Icon(icon, color: Colors.white)),
      title: Text(title, style: TextStyle(fontSize: 16, fontWeight: FontWeight.w700, color: AppColors.ink)),
      trailing: Icon(Icons.chevron_right_rounded, color: AppColors.muted),
      onTap: onTap,
    );
  }
}

class _BigAction extends StatelessWidget {
  const _BigAction({
    required this.icon,
    required this.color,
    required this.title,
    required this.subtitle,
    required this.onTap,
    this.badge,
  });

  final IconData icon;
  final Color color;
  final String title;
  final String subtitle;
  final VoidCallback onTap;
  final String? badge;

  @override
  Widget build(BuildContext context) {
    return Material(
      color: AppColors.surface,
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(18), side: BorderSide(color: AppColors.line)),
      child: InkWell(
        borderRadius: BorderRadius.circular(18),
        onTap: onTap,
        child: Padding(
          padding: const EdgeInsets.all(18),
          child: Row(
            children: [
              Container(
                width: 56,
                height: 56,
                decoration: BoxDecoration(color: color, shape: BoxShape.circle),
                child: Icon(icon, color: Colors.white, size: 28),
              ),
              const SizedBox(width: 16),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Row(
                      children: [
                        Flexible(child: Text(title, style: const TextStyle(fontSize: 18, fontWeight: FontWeight.w700))),
                        if (badge != null) ...[
                          const SizedBox(width: 8),
                          Container(
                            padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 2),
                            decoration: BoxDecoration(color: AppColors.posSoft, borderRadius: BorderRadius.circular(999)),
                            child: Text(badge!, style: TextStyle(fontSize: 11, fontWeight: FontWeight.w800, color: AppColors.pos)),
                          ),
                        ],
                      ],
                    ),
                    const SizedBox(height: 2),
                    Text(subtitle, style: TextStyle(fontSize: 13.5, color: AppColors.muted)),
                  ],
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

class _ReviewCard extends StatelessWidget {
  const _ReviewCard({required this.title, required this.onEdit, required this.children});

  final String title;
  final VoidCallback onEdit;
  final List<Widget> children;

  @override
  Widget build(BuildContext context) {
    return Container(
      margin: const EdgeInsets.only(bottom: 12),
      padding: const EdgeInsets.fromLTRB(16, 8, 8, 12),
      decoration: BoxDecoration(
        color: AppColors.surface,
        borderRadius: BorderRadius.circular(16),
        border: Border.all(color: AppColors.line),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Expanded(child: Text(title, style: const TextStyle(fontSize: 16, fontWeight: FontWeight.w800))),
              TextButton(onPressed: onEdit, child: const Text('Edit')),
            ],
          ),
          Padding(padding: const EdgeInsets.only(right: 8), child: Column(children: children)),
        ],
      ),
    );
  }
}

class _Message extends StatelessWidget {
  const _Message({required this.icon, required this.title, required this.text, this.action});

  final IconData icon;
  final String title;
  final String text;
  final Widget? action;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.all(28),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(icon, size: 40, color: AppColors.muted),
          const SizedBox(height: 12),
          Text(title, textAlign: TextAlign.center, style: const TextStyle(fontSize: 17, fontWeight: FontWeight.w700)),
          const SizedBox(height: 6),
          Text(text, textAlign: TextAlign.center, style: TextStyle(fontSize: 14, height: 1.45, color: AppColors.muted)),
          if (action != null) ...[const SizedBox(height: 16), action!],
        ],
      ),
    );
  }
}