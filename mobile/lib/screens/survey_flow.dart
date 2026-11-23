import 'dart:io';
import '../widgets/design.dart';

import 'package:file_picker/file_picker.dart';
import 'package:path_provider/path_provider.dart';
import 'package:video_player/video_player.dart';
import 'package:flutter/material.dart';

import '../main.dart';
import '../services/org_api.dart' show Json, intOf;
import '../services/session.dart';
import '../services/connectivity_service.dart';
import '../services/gps_service.dart';
import '../services/lov.dart';
import '../services/offline_catalog.dart';
import '../services/survey_api.dart';
import '../services/recording_vault.dart';
import '../services/survey_queue.dart';
import 'capture_screens.dart';
import '../widgets/search_dropdown.dart';

/*
  Field survey on the phone, one step per screen:
    category -> product -> respondent -> questions (one per screen) -> recording -> review
  Submit saves the survey on the phone; it uploads and is analyzed in the background.
*/

// "How long have you been consuming / using <product>?" (the stored codes are unchanged)
const _usagePeriods = [
  ('last_week', 'Less than 7 days'),
  ('last_month', 'For the past 1 month'),
  ('last_3_months', 'For the last 3 months'),
  ('more_than_3_months', 'For more than 3 months'),
];

// "consuming" for food and drink, "using" for everything else (shampoo, soap, detergent ...).
// The project (category) name decides first, then the product name; unclear = "using".
// Same rule as the web (SurveyWorkflow.jsx usageVerb).
final _edible = RegExp(
    r'\b(food|beverage|drink|dairy|milk|cereal|snack|confection|chocolate|juice|tea|coffee|water|biscuit|bakery|bread|jelly|jam|'
    r'sauce|spice|masala|ghee|butter|cheese|curd|yogh?urt|lassi|noodle|pasta|granola|flakes|oats|candy|nutrition|edible|ice ?cream|'
    r'frozen|rice|atta|flour|dal|honey|pickle|sweets?)',
    caseSensitive: false);
final _nonEdible = RegExp(
    r'\b(soap|shampoo|detergent|lotion|cosmetic|personal care|home care|cleaner|cleaning|toothpaste|deodorant|perfume|fragrance|'
    r'hair|skin|laundry|dishwash|tissue|diaper|sanitary|repellent|polish|insecticide|household|beauty|make ?up)',
    caseSensitive: false);

String usageVerb(String? productName, String? categoryName) {
  if (_edible.hasMatch(categoryName ?? '')) return 'consuming';
  if (_nonEdible.hasMatch('${productName ?? ''} ${categoryName ?? ''}')) return 'using';
  return _edible.hasMatch(productName ?? '') ? 'consuming' : 'using';
}

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
  // The product version the customer tasted; null when the product has no version in
  // fieldwork yet (the server then files the survey under its first round).
  ProductVersion? _version;
  List<SurveyQuestionItem> _questions = [];
  bool _loadingQuestions = false;

  // Respondent
  final _name = TextEditingController();
  final _mobile = TextEditingController();
  final _age = TextEditingController();
  // The customer's place (web: CustomerPlace): Country -> State -> City from the LOV
  // lists, then the current area. [_city] holds the area ("location" on the server).
  final _city = TextEditingController();
  final _state = TextEditingController();
  final _cityName = TextEditingController();
  bool _otherCity = false; // a city that isn't in the state's list, typed by hand
  Lov _lov = Lov.empty;
  String _placeGps = 'idle'; // idle | locating | resolving | warn | error
  String? _placeMsg;
  final _address = TextEditingController();
  final _executive = TextEditingController();
  final _surveyLocation = TextEditingController();
  String? _gender;
  String? _usage;
  // Next update: country, respondent type, occupation, class (SEC) and Internal / External
  final _country = TextEditingController(text: 'India');
  String? _respondentType, _occupation, _secClass;
  String _audience = 'external';
  Map<String, List<String>> _masters = const {};
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
  String _gps = 'locating'; // locating | resolving | ok | error
  GpsFix? _fix; // lat/lng, accuracy, capture time and platform (services/gps_service.dart)
  GpsException? _gpsError;
  String? _place; // readable address from GPS (GET /geo/reverse)
  Map<String, dynamic>? _placeInfo; // country, state, city of the survey's GPS
  bool _fixOffline = false; // the fix was taken without a connection
  String? _placeError;
  // Survey location text that came from GPS or the previous survey: a new GPS
  // address may replace it, but text the survey person typed is never replaced.
  String? _autoLocationText;

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

  // Action plans: the cities this person may survey, per product (empty = anywhere)
  List<Json> _myPlans = [];

  Json? get _myPlan {
    for (final x in _myPlans) {
      if (intOf(x['product_id']) == _product?.id) return x;
    }
    return null;
  }

  List<String> get _myCities => [for (final c in (_myPlan?['cities'] as List? ?? const [])) '$c'];

  Future<void> _loadMyPlans() async {
    try {
      final plans = await OfflineCatalog.myActionPlans(); // works offline (downloaded copy)
      if (mounted) setState(() => _myPlans = plans);
    } catch (_) {
      // offline or an older server: no city limit is shown here (the server still checks)
    }
  }

  @override
  void initState() {
    super.initState();
    _loadMyPlans();
    OfflineCatalog.lov().then((l) {
      if (mounted) setState(() => _lov = l);
    }).catchError((_) {}); // not loaded: the place is typed instead
    // Signed in: the survey is done by this account's executive.
    _executive.text = Session.user.value?.name ?? AppProfile.executiveName.value;
    _surveyLocation.text = AppProfile.lastSurveyLocation.value;
    OfflineCatalog.masters().then((m) {
      if (!mounted) return;
      setState(() => _masters = {
            for (final k in const ['respondent_type', 'occupation', 'sec_class'])
              k: [for (final v in (m[k] is List ? m[k] as List : const [])) if (v is Map) '${v['name']}'],
          });
    }).catchError((_) {});
    _autoLocationText = _surveyLocation.text;
    _usage = widget.keepUsage;
    _loadCatalog();
    _captureLocation();
    ConnectivityService.online.addListener(_onConnectivity);
  }

  /// GPS fix of this survey (optional, never blocks it). Works offline on Android
  /// (GNSS) and iOS (Core Location); the fix is saved with the survey and sent with it,
  /// keeping its capture time. The address lookup needs the server and runs again
  /// when the connection comes back.
  Future<void> _captureLocation({bool ask = false}) async {
    if (mounted) {
      setState(() {
        _gps = 'locating';
        _gpsError = null;
      });
    }
    try {
      final fix = await GpsService.capture(ask: ask);
      if (!mounted) return;
      setState(() {
        _fix = fix;
        _fixOffline = !ConnectivityService.online.value;
        _gps = 'ok';
        _place = null;
        _placeInfo = null;
        _placeError = null;
      });
    } on GpsException catch (e) {
      if (mounted) {
        setState(() {
          _gps = 'error';
          _gpsError = e;
        });
      }
      return;
    }
    await _resolvePlace();
  }

  /// Coordinates -> readable address, filled into Survey location (needs the server).
  Future<void> _resolvePlace() async {
    final fix = _fix;
    if (fix == null || !mounted) return;
    if (!ConnectivityService.online.value || !Session.hasServerSession) {
      setState(() {
        _place = null;
        _fillSurveyLocationOffline(fix);
        _placeError = 'Offline: saved with the survey and used as the survey location. '
            'It becomes the address when you are back online or when the survey uploads.';
      });
      return;
    }
    setState(() => _gps = 'resolving');
    try {
      final place = await _api.reverseGeocode(fix.latitude, fix.longitude);
      final label = '${place['label'] ?? ''}'.trim();
      if (!mounted || _fix != fix) return;
      setState(() {
        _gps = 'ok';
        _place = label.isEmpty ? null : label;
        _placeInfo = Map<String, dynamic>.from(place);
        _placeError = null;
        final current = _surveyLocation.text.trim();
        if (label.isNotEmpty && (current.isEmpty || current == (_autoLocationText ?? '').trim())) {
          _surveyLocation.text = label;
          _autoLocationText = label;
        }
      });
    } catch (e) {
      if (mounted) {
        setState(() {
          _gps = 'ok'; // the coordinates are still saved with the survey
          _place = null;
          _placeError = '$e';
        });
      }
    }
  }

  /// Offline: the survey location becomes the GPS position, unless the survey person typed one.
  void _fillSurveyLocationOffline(GpsFix fix) {
    final current = _surveyLocation.text.trim();
    if (current.isEmpty || current == (_autoLocationText ?? '').trim() || GpsFix.isPlaceholder(current)) {
      _surveyLocation.text = fix.placeholder;
      _autoLocationText = fix.placeholder;
    }
  }

  /// Back online with an offline fix and no address yet: look it up now.
  void _onConnectivity() {
    if (!ConnectivityService.online.value || !Session.hasServerSession) return;
    final customerFix = _customerFixToResolve;
    if (customerFix != null && _placeGps != 'resolving') {
      _fillCustomerPlace(customerFix, onlyEmpty: true); // also fills the survey's address
    } else if (_fix != null && _place == null && _gps == 'ok') {
      _resolvePlace();
    }
  }

  @override
  void dispose() {
    ConnectivityService.online.removeListener(_onConnectivity);
    if (!_saved) {
      _discard(_media); // the survey was abandoned: its (encrypted) recording goes too
      _discard(_unsealed);
    }
    for (final c in [_name, _mobile, _age, _city, _state, _cityName, _address, _country, _executive, _surveyLocation, ..._textControllers.values]) {
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
      final results = await Future.wait([OfflineCatalog.categories(), OfflineCatalog.products()]);
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
    // Several versions in fieldwork: ask which one the customer tasted (the one
    // collecting now is suggested). One version: used without asking. When the product
    // comes from "survey again" (jump: false) the suggestion is used; it can be changed
    // from the product banner.
    ProductVersion? version = p.defaultVersion;
    if (jump && p.surveyVersions.length > 1) {
      version = await _askVersion(p, current: version);
      if (version == null || !mounted) return; // closed without choosing
    }
    setState(() {
      _product = p;
      _version = version;
      _loadingQuestions = true;
      _error = null;
    });
    try {
      final qs = await OfflineCatalog.questions(p.id);
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

  /// Closes the keyboard wherever the focus is (FocusScope.of(context) only clears this
  /// screen's own scope, which left the keyboard open on the next step).
  void _hideKeyboard() => FocusManager.instance.primaryFocus?.unfocus();

  void _go(int step) {
    _hideKeyboard();
    setState(() {
      _error = null;
      _missingQuestionId = null;
      _step = step;
    });
  }

  Future<void> _back() async {
    _hideKeyboard();
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
    // Offline with a GPS position: the place is filled in from it when back online, so it isn't asked now.
    final placeFromGps = GpsFix.isPlaceholder(_city.text.trim()) && _customerFixToResolve != null;
    if (!placeFromGps && _country.text.trim().isEmpty) return "Choose the customer's country.";
    if (!placeFromGps && _state.text.trim().isEmpty) return "Choose the customer's state.";
    final city = _cityName.text.trim();
    if (city.isEmpty && !placeFromGps) {
      return _myCities.isEmpty
          ? "Choose the customer's city."
          : "Choose the customer's city: you survey in ${_myCities.join(', ')} only.";
    }
    if (_myCities.isNotEmpty && !_myCities.any((c) => c.toLowerCase() == city.toLowerCase())) {
      return 'You survey customers in ${_myCities.join(', ')} only (action plan “${_myPlan?['plan_name'] ?? ''}”).';
    }
    if (_city.text.trim().isEmpty) return "Enter the customer's current location (area or locality), or use the location button.";
    if (_executive.text.trim().isEmpty) return 'Enter your name as the survey executive.';
    if (_surveyLocation.text.trim().isEmpty) return 'Enter where this survey is taking place.';
    if (_usage == null) return 'Choose how long the customer has been ${_usageVerb()} the product.';
    if (!_consent) return "The respondent's consent is needed to continue.";
    return null;
  }

  void _next() {
    _hideKeyboard(); // Continue works with the keyboard open: close it, then validate as before
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
        setState(() => _error = _unsealed != null
            ? 'The recording must be secured first: connect to the internet once, then tap "Secure recording".'
            : "Record or choose the customer's feedback first.");
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
      _discard(_media); // "record again": the silent recording is deleted
      setState(() => _media = null);
    }
  }

  Future<void> _record(bool video) async {
    final result = await Navigator.of(context).push<RecordedMedia>(
      MaterialPageRoute(
        builder: (_) => video ? const RecordVideoScreen(returnResult: true) : const RecordAudioScreen(returnResult: true),
      ),
    );
    if (result != null && mounted) await _useMedia(result, ownFile: true);
  }

  /// A recording (or chosen file) is sealed at once: encrypted into private storage with its
  /// own key, wrapped for the server; the plaintext is deleted (services/recording_vault.dart).
  /// The survey person can't play it back from here on.
  RecordedMedia? _unsealed; // waiting for the security key (never signed in online on this phone)
  bool _sealing = false;
  bool _saved = false; // handed to the upload queue

  Future<void> _useMedia(RecordedMedia media, {required bool ownFile}) async {
    final previous = _media;
    setState(() {
      _sealing = true;
      _error = null;
    });
    try {
      final sealed = await RecordingVault.seal(media.path, deleteSource: ownFile);
      _discard(previous);
      _discard(_unsealed);
      if (!mounted) return;
      setState(() {
        _media = media.sealedAs(path: sealed.path, keyId: sealed.keyId, wrappedKey: sealed.wrappedKey, bytes: sealed.bytes);
        _unsealed = null;
        _silentConfirmed = false;
      });
    } on RecordingKeyMissing catch (e) {
      if (!mounted) return;
      setState(() {
        _unsealed = media;
        _error = '$e';
      });
    } catch (e) {
      if (mounted) setState(() => _error = "The recording couldn't be secured on this phone: $e");
    } finally {
      if (mounted) setState(() => _sealing = false);
    }
  }

  /// Deletes a recording the app made (sealed file or our own plaintext copy).
  void _discard(RecordedMedia? m) {
    if (m == null) return;
    if (m.isSealed || m.source == 'recorded') {
      try {
        File(m.path).deleteSync();
      } catch (_) {}
    }
  }

  /// A chosen file is copied by the file picker into the app's temporary folder: that copy is
  /// the app's own (deleted after sealing); the person's original file is never touched.
  Future<bool> _isTempCopy(String path) async {
    try {
      final tmp = (await getTemporaryDirectory()).path;
      return File(path).absolute.path.startsWith(tmp) || path.contains('/file_picker/');
    } catch (_) {
      return false;
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
    await _useMedia(
      RecordedMedia(
        path: f.path!,
        name: f.name,
        kind: _audioTypes.contains(ext) ? 'audio' : 'video',
        durationSeconds: seconds ?? 0, // 0 = couldn't be read here; the server checks it
        source: 'file',
        sizeBytes: size,
      ),
      ownFile: await _isTempCopy(f.path!),
    );
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
              Text('Replace the recording', style: TextStyle(fontFamily: kDisplayFont, fontSize: 18, fontWeight: FontWeight.w800, color: AppColors.ink)),
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
    _hideKeyboard();
    final p = _product!;
    final media = _media!;
    setState(() => _submitting = true);
    try {
      AppProfile.executiveName.value = _executive.text.trim();
      // A GPS position (offline) isn't remembered as "the last survey location".
      if (!GpsFix.isPlaceholder(_surveyLocation.text)) AppProfile.lastSurveyLocation.value = _surveyLocation.text.trim();
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
          'country': _country.text.trim().isEmpty ? null : _country.text.trim(),
          'state': _state.text.trim().isEmpty ? null : _state.text.trim(),
          'city': _cityName.text.trim().isEmpty ? null : _cityName.text.trim(), // checked against the action plan
          'respondent_type': _respondentType,
          'occupation': _occupation,
          'sec_class': _secClass,
          'consent_given': true,
        },
        response: {
          'product_id': p.id,
          // Saved with the survey (kept in the offline queue until it is sent).
          if (_version != null) 'recipe_version': _version!.version,
          'executive_name': _executive.text.trim(),
          'usage_period': _usage,
          'audience': _audience,
          'location': _surveyLocation.text.trim(),
          // GPS fix with its accuracy, original capture time and platform (kept in the offline queue).
          ...?_fix?.toJson(),
        },
        answers: [for (final q in _questions) {'question_id': q.id, 'value': (_answers[q.id] ?? '').trim()}],
        mediaSourcePath: media.path,
        mediaName: media.name,
        mediaKind: media.kind,
        durationSeconds: media.durationSeconds,
        encKeyId: media.keyId,
        encWrappedKey: media.wrappedKey,
      );
      _saved = true;
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

  /// "Which version did the customer taste?": the product's versions in fieldwork,
  /// newest first. Returns the chosen version, or null if the sheet was closed.
  Future<ProductVersion?> _askVersion(CatalogProduct p, {ProductVersion? current}) {
    return showModalBottomSheet<ProductVersion>(
      context: context,
      showDragHandle: true,
      isScrollControlled: true,
      builder: (ctx) => SafeArea(
        child: SingleChildScrollView(
          padding: const EdgeInsets.fromLTRB(20, 0, 20, 16),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              const Text('Which version did the customer taste?',
                  style: TextStyle(fontSize: 17, fontWeight: FontWeight.w700)),
              const SizedBox(height: 4),
              Text(
                '${p.name} has ${p.surveyVersions.length} versions. The survey is saved with the version you choose.',
                style: TextStyle(fontSize: 13.5, color: AppColors.muted),
              ),
              const SizedBox(height: 8),
              for (final v in p.surveyVersions.reversed)
                ListTile(
                  contentPadding: EdgeInsets.zero,
                  leading: Icon(
                    v.version == current?.version ? Icons.radio_button_checked : Icons.radio_button_unchecked,
                    color: AppColors.brand,
                  ),
                  title: Text('Version ${v.version}', style: const TextStyle(fontWeight: FontWeight.w600)),
                  subtitle: Text(v.detail),
                  onTap: () => Navigator.of(ctx).pop(v),
                ),
            ],
          ),
        ),
      ),
    );
  }

  Future<void> _changeVersion() async {
    final p = _product;
    if (p == null) return;
    final v = await _askVersion(p, current: _version);
    if (v != null && mounted) setState(() => _version = v);
  }

  /// The product banner on later steps: the chosen version beside the product name.
  Widget _banner() {
    final p = _product;
    return _ProductBanner(
      name: p?.name ?? '',
      category: p == null ? '' : _categoryName(p),
      version: _version == null ? null : 'Version ${_version!.version}',
      onChangeVersion: (p != null && p.surveyVersions.length > 1) ? _changeVersion : null,
    );
  }

  String _usageVerb() => usageVerb(_product?.name, _product == null ? null : _categoryName(_product!));

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
          title: Text(_title, style: const TextStyle(fontFamily: kDisplayFont, fontSize: 18, fontWeight: FontWeight.w700)),
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
        // The Continue bar is part of the body (not bottomNavigationBar): with
        // resizeToAvoidBottomInset the body shrinks above the keyboard, so the bar
        // stays visible right on top of it instead of hidden behind it.
        body: Builder(builder: (context) {
          final bar = _bottomBar();
          return SafeArea(
            bottom: bar == null, // the bar handles the bottom safe area itself
            child: Column(children: [Expanded(child: _body()), if (bar != null) bar]),
          );
        }),
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
    // Keyboard open: the bar sits on top of it, so a slimmer bar and no home-indicator padding.
    final keyboardOpen = MediaQuery.viewInsetsOf(context).bottom > 0;
    return SafeArea(
      top: false,
      bottom: !keyboardOpen,
      child: Container(
        padding: EdgeInsets.fromLTRB(16, keyboardOpen ? 8 : 10, 16, keyboardOpen ? 8 : 12),
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
      keyboardDismissBehavior: ScrollViewKeyboardDismissBehavior.onDrag,
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
                // Only a problem is worth a line here.
                subtitle: p.surveyReady ? '' : 'Not ready: needs 5–10 questions',
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
      keyboardDismissBehavior: ScrollViewKeyboardDismissBehavior.onDrag,
      padding: const EdgeInsets.fromLTRB(16, 12, 16, 24),
      children: [
        _banner(),
        const _SectionLabel('Customer'),
        _field(_name, 'Full name *', capitalization: TextCapitalization.words, autofill: const [AutofillHints.name]),
        _field(_mobile, 'Mobile (optional)', keyboard: TextInputType.phone, hint: '98765 43210'),
        Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Expanded(
              child: Padding(
                padding: const EdgeInsets.only(bottom: 12),
                child: SearchDropdownField<String>(
                  initialValue: _gender,
                  isExpanded: true,
                  decoration: _inputDecoration('Gender'),
                  items: [for (final g in _genders) DropdownMenuItem(value: g, child: Text(g))],
                  onChanged: (v) => setState(() => _gender = v),
                ),
              ),
            ),
            const SizedBox(width: 12),
            Expanded(child: _field(_age, 'Age', keyboard: TextInputType.number)),
          ],
        ),
        if (intOf(_myPlan?['my_target']) > 0)
          Container(
            margin: const EdgeInsets.only(bottom: 8),
            padding: const EdgeInsets.fromLTRB(12, 10, 12, 10),
            decoration: BoxDecoration(
              color: intOf(_myPlan?['my_done']) >= intOf(_myPlan?['my_target']) ? AppColors.warnSoft : AppColors.brandSoft,
              borderRadius: BorderRadius.circular(10),
              border: Border.all(color: AppColors.line),
            ),
            child: Text.rich(
              TextSpan(children: [
                const TextSpan(text: 'Assigned responses '),
                TextSpan(text: '${intOf(_myPlan?['my_target'])}', style: const TextStyle(fontWeight: FontWeight.w800)),
                const TextSpan(text: ' · Submitted '),
                TextSpan(text: '${intOf(_myPlan?['my_done'])}', style: const TextStyle(fontWeight: FontWeight.w800)),
                const TextSpan(text: ' · Remaining '),
                TextSpan(text: '${(intOf(_myPlan?['my_target']) - intOf(_myPlan?['my_done'])).clamp(0, 1 << 30)}', style: const TextStyle(fontWeight: FontWeight.w800)),
                if (intOf(_myPlan?['my_done']) >= intOf(_myPlan?['my_target'])) const TextSpan(text: ' · all done (ask your Admin if more are needed)'),
              ]),
              style: TextStyle(fontSize: 13.5, color: AppColors.ink, height: 1.35),
            ),
          ),
        if (_myCities.isNotEmpty)
          Container(
            margin: const EdgeInsets.only(bottom: 12),
            padding: const EdgeInsets.fromLTRB(12, 10, 12, 10),
            decoration: BoxDecoration(
              color: AppColors.brandSoft,
              borderRadius: BorderRadius.circular(10),
              border: Border.all(color: AppColors.line),
            ),
            child: Text.rich(
              TextSpan(children: [
                const TextSpan(text: 'You survey in '),
                TextSpan(text: _myCities.join(', '), style: const TextStyle(fontWeight: FontWeight.w800)),
                TextSpan(text: ' only · action plan “${_myPlan?['plan_name'] ?? ''}”'),
              ]),
              style: TextStyle(fontSize: 13.5, color: AppColors.ink, height: 1.35),
            ),
          ),
        ..._customerPlace(),
        _field(_address, 'Address (optional)', capitalization: TextCapitalization.sentences),
        for (final (key, label) in const [('respondent_type', 'Respondent type'), ('occupation', 'Occupation'), ('sec_class', 'Class (SEC)')])
          Padding(
            padding: const EdgeInsets.only(bottom: 12),
            child: SearchDropdownField<String>(
              initialValue: key == 'respondent_type' ? _respondentType : key == 'occupation' ? _occupation : _secClass,
              isExpanded: true,
              decoration: _inputDecoration(label),
              items: [for (final v in _masters[key] ?? const <String>[]) DropdownMenuItem(value: v, child: Text(v))],
              onChanged: (v) => setState(() {
                if (key == 'respondent_type') {
                  _respondentType = v;
                } else if (key == 'occupation') {
                  _occupation = v;
                } else {
                  _secClass = v;
                }
              }),
            ),
          ),
        const _SectionLabel('This survey'),
        Padding(
          padding: const EdgeInsets.only(bottom: 12),
          child: SegmentedButton<String>(
            segments: const [
              ButtonSegment(value: 'external', label: Text('External')),
              ButtonSegment(value: 'internal', label: Text('Internal')),
            ],
            selected: {_audience},
            onSelectionChanged: (s) => setState(() => _audience = s.first),
          ),
        ),
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
        _field(_surveyLocation, 'Survey location *', capitalization: TextCapitalization.words, hint: 'e.g. D-Mart, Andheri West',
            action: TextInputAction.done),
        const SizedBox(height: 12),
        Text('How long have you been ${_usageVerb()} ${_product?.name ?? 'the product'}? *',
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

  /* The customer's place: Country -> State -> City (lists), then the area. */

  List<LovValue> get _countries => _lov.values('COUNTRY');
  LovValue? get _countryValue => Lov.match(_countries, _country.text);
  List<LovValue> get _states => _lov.children('STATE', _countryValue?.id);
  LovValue? get _stateValue => Lov.match(_states, _state.text);
  List<LovValue> get _cities => _lov.children('CITY', _stateValue?.id);
  LovValue? get _cityValue => Lov.match(_cities, _cityName.text);
  bool get _typingCity => _cities.isNotEmpty && (_otherCity || (_cityName.text.trim().isNotEmpty && _cityValue == null));

  static const _otherCityId = '__other__';

  Widget _pick(String label, List<LovValue> options, LovValue? value, ValueChanged<LovValue> onPick,
      {bool withOther = false, String? hint}) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 12),
      child: SearchDropdownField<String>(
        // A new key when the value changes from outside (GPS), so the field shows it.
        key: ValueKey('$label:${value?.id}:${options.length}'),
        initialValue: value == null ? null : '${value.id}',
        isExpanded: true,
        menuMaxHeight: 420,
        decoration: _inputDecoration(label).copyWith(hintText: hint),
        items: [
          for (final o in options) DropdownMenuItem(value: '${o.id}', child: Text(o.label, overflow: TextOverflow.ellipsis)),
          if (withOther) const DropdownMenuItem(value: _otherCityId, child: Text('Other city (type it)')),
        ],
        onChanged: (id) {
          if (id == _otherCityId) {
            setState(() {
              _otherCity = true;
              _cityName.clear();
              _error = null;
            });
            return;
          }
          final picked = options.where((o) => '${o.id}' == id).firstOrNull;
          if (picked != null) onPick(picked);
        },
      ),
    );
  }

  bool _placeManual = false; // Change: the country / state / city lists
  bool _placeAutoTried = false;

  List<Widget> _customerPlace() {
    final busy = _placeGps == 'locating' || _placeGps == 'resolving';
    // Captured automatically from the phone's location when the step opens.
    if (!_placeAutoTried && _cityName.text.trim().isEmpty && _city.text.trim().isEmpty) {
      _placeAutoTried = true;
      WidgetsBinding.instance.addPostFrameCallback((_) {
        if (mounted) _locateCustomer();
      });
    }
    final captured = (_country.text.trim().isNotEmpty && _state.text.trim().isNotEmpty && _cityName.text.trim().isNotEmpty) ||
        GpsFix.isPlaceholder(_city.text.trim());
    if (captured && !_placeManual) {
      final where = [_cityName.text.trim(), _state.text.trim(), _country.text.trim()].where((x) => x.isNotEmpty).join(', ');
      final area = GpsFix.isPlaceholder(_city.text.trim()) ? '' : _city.text.trim();
      return [
        Container(
          margin: const EdgeInsets.only(bottom: 12),
          padding: const EdgeInsets.all(12),
          decoration: BoxDecoration(color: AppColors.brandSoft, borderRadius: BorderRadius.circular(12), border: Border.all(color: AppColors.line)),
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Text("Customer's place (from this phone's location)", style: TextStyle(fontSize: 12, color: AppColors.muted, fontWeight: FontWeight.w600)),
            const SizedBox(height: 4),
            Text(
              where.isEmpty ? 'GPS position kept: the address is added when you are back online.' : [area, where].where((x) => x.isNotEmpty).join(', '),
              style: TextStyle(fontWeight: FontWeight.w800, color: AppColors.ink),
            ),
            if (_placeMsg != null) ...[const SizedBox(height: 4), Text(_placeMsg!, style: TextStyle(fontSize: 12.5, color: AppColors.muted))],
            Row(children: [
              TextButton.icon(onPressed: busy ? null : _locateCustomer, icon: const Icon(Icons.my_location_rounded, size: 18), label: Text(busy ? 'Finding…' : 'Update')),
              TextButton(onPressed: () => setState(() => _placeManual = true), child: const Text('Change')),
            ]),
          ]),
        ),
      ];
    }
    return [
      if (_countries.isNotEmpty)
        _pick('Country *', _countries, _countryValue, (v) => setState(() {
              _country.text = v.label;
              _state.clear();
              _cityName.clear();
              _otherCity = false;
              _error = null;
            }))
      else
        _field(_country, 'Country *', capitalization: TextCapitalization.words, hint: 'e.g. India'),
      if (_states.isNotEmpty)
        _pick('State *', _states, _stateValue, (v) => setState(() {
              _state.text = v.label;
              _cityName.clear();
              _otherCity = false;
              _error = null;
            }))
      else
        _field(_state, 'State *', capitalization: TextCapitalization.words,
            hint: _country.text.trim().isEmpty ? 'Choose the country first' : 'Type the state'),
      if (_cities.isEmpty)
        _field(_cityName, 'City *', capitalization: TextCapitalization.words,
            hint: _state.text.trim().isNotEmpty || _states.isEmpty ? 'Type the city' : 'Choose the state first')
      else if (_typingCity) ...[
        _field(_cityName, 'City *', capitalization: TextCapitalization.words, hint: 'Type the city'),
        Padding(
          padding: const EdgeInsets.only(bottom: 12),
          child: Row(children: [
            Expanded(
              child: Text('Not in the city list. An admin can add it in Categories > Master lists.',
                  style: TextStyle(fontSize: 12.5, color: AppColors.muted)),
            ),
            TextButton(
              onPressed: () => setState(() {
                _otherCity = false;
                _cityName.clear();
              }),
              child: const Text('Choose from list'),
            ),
          ]),
        ),
      ] else
        _pick('City *', _cities, _cityValue, (v) => setState(() {
              _cityName.text = v.label;
              _otherCity = false;
              _error = null;
            }), withOther: true),
      _field(_city, 'Current location (area or locality) *', capitalization: TextCapitalization.words,
          hint: 'e.g. Andheri West, or use the button'),
      Padding(
        padding: const EdgeInsets.only(bottom: 14),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          OutlinedButton.icon(
            onPressed: busy ? null : _locateCustomer,
            icon: busy
                ? const SizedBox(width: 16, height: 16, child: CircularProgressIndicator(strokeWidth: 2))
                : const Icon(Icons.my_location_rounded, size: 18),
            label: Text(_placeGps == 'locating'
                ? 'Finding location…'
                : _placeGps == 'resolving'
                    ? 'Finding the address…'
                    : 'Use current location'),
          ),
          const SizedBox(height: 4),
          Text(
            _placeMsg ?? "Fills in the country, state, city and area from this phone's location.",
            style: TextStyle(
              fontSize: 12.5,
              height: 1.35,
              color: _placeGps == 'error' ? AppColors.neg : _placeGps == 'warn' ? AppColors.warn : AppColors.muted,
            ),
          ),
        ]),
      ),
    ];
  }

  /// "Use current location" for the customer: the GPS works without internet; the
  /// address lookup (country, state, city, area) needs the server. Offline, the fix is
  /// saved with the survey and the place is filled in from it once back online
  /// (only the fields still empty, so nothing typed meanwhile is replaced).
  GpsFix? _customerFixToResolve;

  Future<void> _locateCustomer() async {
    _hideKeyboard();
    setState(() {
      _placeGps = 'locating';
      _placeMsg = null;
    });
    final GpsFix fix;
    try {
      fix = await GpsService.capture(ask: true);
    } on GpsException catch (e) {
      if (mounted) {
        setState(() {
          _placeGps = 'error';
          _placeMsg = e.message.replaceFirst('type the location', 'choose the place by hand');
        });
      }
      return;
    }
    if (!mounted) return;
    setState(() {
      // The survey usually happens where the customer is: this is also the survey's GPS
      // (kept with the survey, offline too) unless one was taken already.
      if (_fix == null || _gps == 'error') {
        _fix = fix;
        _gps = 'ok';
        _gpsError = null;
        _place = null;
      }
    });
    if (!ConnectivityService.online.value || !Session.hasServerSession) {
      setState(() {
        _customerFixToResolve = fix;
        // No address offline: the position goes into the place fields, and is replaced by
        // the area's name when back online (here) or when the survey uploads (server).
        if (_city.text.trim().isEmpty || GpsFix.isPlaceholder(_city.text)) _city.text = fix.placeholder;
        _fillSurveyLocationOffline(fix);
        _placeGps = 'warn';
        _placeMsg = 'Offline: the GPS position is used as the location. It is replaced by the area name when '
            'you are back online or when the survey uploads. Choose the state and city if they are empty.';
        _error = null;
      });
      return;
    }
    await _fillCustomerPlace(fix);
  }

  /// Address of [fix] -> country, state, city and area (web: CustomerPlace.locate).
  Future<void> _fillCustomerPlace(GpsFix fix, {bool onlyEmpty = false}) async {
    setState(() => _placeGps = 'resolving');
    try {
      final place = await _api.reverseGeocode(fix.latitude, fix.longitude);
      if (!mounted) return;
      String s(String k) => '${place[k] ?? ''}'.trim();
      final label = s('label');
      final c = Lov.match(_countries, s('country'));
      final stateList = _lov.children('STATE', c?.id);
      final st = Lov.match(stateList, s('state'));
      // The lookup gives several names (city, town, district ...): the first one in the state's city list.
      final cityList = _lov.children('CITY', st?.id);
      final names = [s('city'), for (final n in (place['places'] as List? ?? const [])) '$n'];
      final ci = names.map((n) => Lov.match(cityList, n)).whereType<LovValue>().firstOrNull;
      final missing = st != null && ci == null && s('city').isNotEmpty
          ? ' "${s('city')}" isn\'t in the city list of ${st.label}, so it\'s filled in as typed. Pick a listed city if one fits, or ask an admin to add it.'
          : c != null && st == null && s('state').isNotEmpty
              ? ' Choose the state.'
              : '';
      final fromDistrict = ci != null && const {'state_district', 'county'}.contains(s('city_from'))
          ? ' The city was taken from the district (${s('city')}): check it with the customer.'
          : '';
      // Typed while offline: kept. A place chosen by hand is never replaced.
      final placeChosen = onlyEmpty && _state.text.trim().isNotEmpty && _cityName.text.trim().isNotEmpty;
      setState(() {
        _customerFixToResolve = null;
        if (!placeChosen) {
          _country.text = c?.label ?? s('country');
          _state.text = st?.label ?? (stateList.isNotEmpty ? '' : s('state'));
          _cityName.text = ci?.label ?? s('city');
          _otherCity = false;
        }
        if (!onlyEmpty || _city.text.trim().isEmpty || GpsFix.isPlaceholder(_city.text)) {
          _city.text = s('locality').isNotEmpty ? s('locality') : label;
        }
        _placeGps = missing.isNotEmpty ? 'warn' : 'idle';
        _placeMsg = 'Found: ${label.isEmpty ? 'your location' : label}.$missing$fromDistrict';
        _error = null;
        if (_fix == fix) _place = label.isEmpty ? null : label;
        final current = _surveyLocation.text.trim();
        if (label.isNotEmpty && (current.isEmpty || current == (_autoLocationText ?? '').trim())) {
          _surveyLocation.text = label;
          _autoLocationText = label;
        }
      });
    } catch (e) {
      if (mounted) {
        setState(() {
          _placeGps = 'warn'; // the GPS position itself is saved with the survey
          _placeMsg = "Couldn't find the address right now (the GPS position is saved). Choose the place and type the area.";
        });
      }
    }
  }

  Widget _field(
    TextEditingController c,
    String label, {
    TextInputType? keyboard,
    TextCapitalization capitalization = TextCapitalization.none,
    String? hint,
    List<String>? autofill,
    TextInputAction action = TextInputAction.next,
  }) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 12),
      child: TextField(
        controller: c,
        keyboardType: keyboard,
        textCapitalization: capitalization,
        autofillHints: autofill,
        textInputAction: action,
        onSubmitted: action == TextInputAction.done ? (_) => _hideKeyboard() : null,
        decoration: _inputDecoration(label).copyWith(hintText: hint),
        onChanged: (_) {
          if (_error != null) setState(() => _error = null);
        },
      ),
    );
  }

  String _ago(DateTime t) {
    final d = DateTime.now().toUtc().difference(t.toUtc());
    if (d.inSeconds < 60) return 'just now';
    if (d.inMinutes < 60) return '${d.inMinutes} min ago';
    final local = t.toLocal();
    return '${local.hour.toString().padLeft(2, '0')}:${local.minute.toString().padLeft(2, '0')}';
  }

  /// The Location step: this survey's GPS (latitude, longitude, accuracy, when, online or offline)
  /// and the place it resolves to (country, state, city), with the usual update / settings row.
  /// "Current location" on the feedback step: the place found from the survey's GPS, then the GPS row.
  Widget _locationCard() {
    final info = _placeInfo ?? const <String, dynamic>{};
    final parts = [info['city'], info['state'], info['country']].where((x) => x != null && '$x'.trim().isNotEmpty).join(', ');
    final place = parts.isNotEmpty
        ? parts
        : (_fix == null ? 'Not captured' : (_fixOffline ? 'Address added when back online' : (_place ?? 'Finding the address…')));
    return Container(
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(color: AppColors.surface, borderRadius: BorderRadius.circular(14), border: Border.all(color: AppColors.line)),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Text('Current location', style: TextStyle(fontSize: 12.5, color: AppColors.muted)),
        const SizedBox(height: 2),
        Text(place, style: TextStyle(fontWeight: FontWeight.w800, fontSize: 15.5, color: AppColors.ink)),
        const SizedBox(height: 8),
        _locationRow(),
        if (_fix == null && _gps == 'error')
          Padding(
            padding: const EdgeInsets.only(top: 6),
            child: Text('The survey can still be submitted without it.', style: TextStyle(fontSize: 12.5, color: AppColors.muted)),
          ),
      ]),
    );
  }

  /// GPS of this survey: coordinates, accuracy and capture time, or what to do.
  Widget _locationRow() {
    final fix = _fix;
    final busy = _gps == 'locating' || _gps == 'resolving';
    final IconData icon;
    final Color color;
    final List<String> lines;
    if (_gps == 'locating') {
      (icon, color, lines) = (Icons.my_location_rounded, AppColors.muted, ['Finding your GPS position…']);
    } else if (_gps == 'error' || fix == null) {
      final e = _gpsError ?? const GpsException(GpsProblem.unavailable);
      (icon, color, lines) = (
        e.problem == GpsProblem.serviceOff ? Icons.location_off_rounded : Icons.location_disabled_rounded,
        AppColors.warn,
        [e.message],
      );
    } else {
      final notes = <String>[
        if (fix.reducedAccuracy) 'Precise location is off for this app: turn it on in Settings for an exact position.',
        if (fix.lastKnown) "No fresh GPS signal: the phone's last position (${_ago(fix.capturedAt)}) is used.",
        if (fix.isPoor && !fix.reducedAccuracy) 'Low accuracy. Move to an open area and tap Update for a better fix.',
        if (_gps == 'resolving') 'Finding the address…' else if (_place != null) _place! else if (_placeError != null) _placeError!,
      ];
      (icon, color, lines) = (
        Icons.location_on_rounded,
        fix.isPoor || fix.reducedAccuracy || fix.lastKnown ? AppColors.warn : AppColors.pos,
        ['${fix.coordinates}  ·  ±${fix.accuracy.round()} m  ·  ${_ago(fix.capturedAt)}', ...notes],
      );
    }
    final e = _gpsError;
    return Container(
      padding: const EdgeInsets.fromLTRB(12, 8, 4, 8),
      decoration: BoxDecoration(color: AppColors.surface, borderRadius: BorderRadius.circular(12), border: Border.all(color: AppColors.line)),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.center,
        children: [
          Icon(icon, size: 20, color: color),
          const SizedBox(width: 8),
          Expanded(
            child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              for (var i = 0; i < lines.length; i++)
                Padding(
                  padding: EdgeInsets.only(top: i == 0 ? 0 : 3),
                  child: Text(
                    lines[i],
                    style: i == 0 && fix != null && _gps != 'error'
                        ? TextStyle(fontSize: 13.5, fontWeight: FontWeight.w700, color: AppColors.ink, fontFeatures: const [FontFeature.tabularFigures()])
                        : TextStyle(fontSize: 13, height: 1.35, color: AppColors.ink2),
                  ),
                ),
            ]),
          ),
          if (busy)
            const Padding(
              padding: EdgeInsets.all(10),
              child: SizedBox(width: 16, height: 16, child: CircularProgressIndicator(strokeWidth: 2)),
            )
          else if (_gps == 'error' && e != null && e.needsSettings)
            TextButton(
              onPressed: () => GpsService.openSettingsFor(e),
              child: Text(e.problem == GpsProblem.serviceOff ? 'Turn on' : 'Settings'),
            )
          else if (_gps == 'error')
            TextButton(onPressed: () => _captureLocation(ask: true), child: const Text('Try again'))
          else
            TextButton(onPressed: () => _captureLocation(ask: true), child: const Text('Update')),
        ],
      ),
    );
  }

  /* ---------------- 4. Questions: all on one page ---------------- */

  Widget _questionsView() {
    final done = _answeredCount;
    return ListView(
      keyboardDismissBehavior: ScrollViewKeyboardDismissBehavior.onDrag,
      padding: const EdgeInsets.fromLTRB(16, 12, 16, 24),
      children: [
        _banner(),
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
      case 'preference': // Sample A vs Sample B
        answer = Column(
          children: [
            for (final o in (q.options.isEmpty ? const ['Sample A', 'Sample B'] : q.options))
              _RadioOption(label: o, selected: value == o, onTap: () => _answer(q, o)),
          ],
        );
      case 'multiple_choice': // any number of options, saved as "A | B"
        final picked = value.split('|').map((v) => v.trim()).where((v) => v.isNotEmpty).toSet();
        answer = Wrap(spacing: 8, runSpacing: 8, children: [
          for (final o in q.options)
            FilterChip(
              label: Text(o),
              selected: picked.contains(o),
              onSelected: (on) {
                final next = {...picked};
                on ? next.add(o) : next.remove(o);
                _answer(q, q.options.where(next.contains).join(' | '));
              },
            ),
        ]);
      default:
        final controller = _textControllers.putIfAbsent(q.id, () => TextEditingController(text: value));
        // The last text question's return key is "Done" (closes the keyboard); earlier
        // ones are "Next" (moves to the next text field).
        final lastText = _questions.lastWhere((x) => !const {'rating', 'yes_no', 'single_choice', 'multiple_choice', 'preference'}.contains(x.type), orElse: () => q).id == q.id;
        answer = TextField(
          controller: controller,
          minLines: 2,
          maxLines: 5,
          keyboardType: TextInputType.text, // single-line keyboard: the return key acts as Done / Next
          textInputAction: lastText ? TextInputAction.done : TextInputAction.next,
          onSubmitted: lastText ? (_) => _hideKeyboard() : null,
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
      keyboardDismissBehavior: ScrollViewKeyboardDismissBehavior.onDrag,
      padding: const EdgeInsets.fromLTRB(16, 16, 16, 24),
      children: [
        Text(
          'Ask the customer for their overall experience: what they liked, what they didn\'t, and what they would change.',
          style: TextStyle(fontSize: 15.5, height: 1.45, color: AppColors.ink2),
        ),
        const SizedBox(height: 6),
        Text('30 seconds to 35 minutes.', style: TextStyle(fontSize: 13.5, color: AppColors.muted)),
        const SizedBox(height: 18),
        _locationCard(),
        const SizedBox(height: 18),
        if (_sealing) ...[
          Container(
            padding: const EdgeInsets.all(18),
            decoration: BoxDecoration(color: AppColors.surface, borderRadius: BorderRadius.circular(18), border: Border.all(color: AppColors.line)),
            child: Row(
              children: [
                const SizedBox(width: 24, height: 24, child: CircularProgressIndicator(strokeWidth: 3)),
                const SizedBox(width: 14),
                Expanded(child: Text('Securing the recording…', style: TextStyle(fontSize: 15.5, color: AppColors.ink))),
              ],
            ),
          ),
        ] else if (m == null && _unsealed != null) ...[
          Container(
            padding: const EdgeInsets.all(16),
            decoration: BoxDecoration(color: AppColors.warnSoft, borderRadius: BorderRadius.circular(16), border: Border.all(color: AppColors.warnLine)),
            child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Text('The recording is waiting to be secured', style: TextStyle(fontWeight: FontWeight.w800, color: AppColors.warnDeep)),
              const SizedBox(height: 4),
              Text('This phone needs the security key once. Connect to the internet, then tap Secure recording.',
                  style: TextStyle(fontSize: 13.5, height: 1.4, color: AppColors.warnDeep)),
              const SizedBox(height: 10),
              FilledButton.icon(
                onPressed: () async {
                  await RecordingVault.refresh((p) => SurveyApi().rawGet(p));
                  if (_unsealed != null) await _useMedia(_unsealed!, ownFile: _unsealed!.source == 'recorded');
                },
                icon: const Icon(Icons.lock_rounded),
                label: const Text('Secure recording'),
              ),
            ]),
          ),
        ] else if (m == null && _checkingFile) ...[
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
          _SealedRecordingCard(media: m),
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
      keyboardDismissBehavior: ScrollViewKeyboardDismissBehavior.onDrag,
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
            row('City', [_cityName.text.trim(), _state.text.trim()].where((s) => s.isNotEmpty).join(', ')),
            row('Area', _city.text.trim()),
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
              const Text('Survey saved', style: TextStyle(fontFamily: kDisplayFont, fontSize: 26, fontWeight: FontWeight.w800)),
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
      label: fieldLabel(label), // "Full name *": the asterisk in red
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
  const _ProductBanner({required this.name, required this.category, this.version, this.onChangeVersion});

  final String name;
  final String category;
  final String? version; // "Version 2": the version the customer tasted
  final VoidCallback? onChangeVersion; // shown when the product has several versions

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
                if (version != null)
                  Text(version!, style: TextStyle(fontSize: 13, fontWeight: FontWeight.w600, color: AppColors.brand)),
              ],
            ),
          ),
          if (onChangeVersion != null)
            TextButton(onPressed: onChangeVersion, child: const Text('Change')),
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
                        if (subtitle.isNotEmpty) ...[
                          const SizedBox(height: 2),
                          Text(subtitle, style: TextStyle(fontSize: 13, color: AppColors.muted)),
                        ],
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
                        Flexible(child: Text(title, style: const TextStyle(fontFamily: kDisplayFont, fontSize: 18, fontWeight: FontWeight.w700))),
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

/// The finished recording, encrypted on this phone: no player (survey persons can't listen to
/// or watch it); only an authorised Admin plays it, with the product passkey.
class _SealedRecordingCard extends StatelessWidget {
  const _SealedRecordingCard({required this.media});

  final RecordedMedia media;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: AppColors.posSoft,
        borderRadius: BorderRadius.circular(18),
        border: Border.all(color: AppColors.pos.withValues(alpha: 0.35)),
      ),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Container(
            width: 44,
            height: 44,
            decoration: BoxDecoration(color: AppColors.surface, borderRadius: BorderRadius.circular(12)),
            child: Icon(Icons.lock_rounded, color: AppColors.pos),
          ),
          const SizedBox(width: 12),
          Expanded(
            child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Text('Recording saved and encrypted',
                  style: TextStyle(fontFamily: kDisplayFont, fontSize: 16, fontWeight: FontWeight.w800, color: AppColors.ink)),
              const SizedBox(height: 4),
              Text(
                'It is locked on this phone and uploads with the survey. To protect the customer\'s privacy it can\'t be '
                'played here; only an authorised admin can listen to it.',
                style: TextStyle(fontSize: 13.5, height: 1.4, color: AppColors.ink2),
              ),
            ]),
          ),
        ],
      ),
    );
  }
}