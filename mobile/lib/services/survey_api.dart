import 'dart:convert';

import 'package:dio/dio.dart';

import 'api_service.dart';
import 'session.dart';

/* ============================================================
   Catalog models
   ============================================================ */

class CatalogCategory {
  const CatalogCategory({required this.id, required this.name, required this.productCount});

  final int id;
  final String name;
  final int productCount;

  factory CatalogCategory.fromJson(Map<String, dynamic> j) => CatalogCategory(
        id: _int(j['id']),
        name: '${j['name'] ?? ''}',
        productCount: _int(j['product_count']),
      );
}

/// One version of a product (= recipe version) that is or was in fieldwork: the
/// survey person picks it when a product has several (GET /products: survey_versions).
class ProductVersion {
  const ProductVersion({required this.version, required this.rounds, required this.live});

  final int version;
  final List<int> rounds; // the survey rounds that tested it, e.g. [2]
  final bool live; // a round of this version is collecting surveys now

  /// "Round 2 · collecting now" / "Round 1 · complete"
  String get detail {
    final which = rounds.isEmpty ? '' : 'Round${rounds.length > 1 ? 's' : ''} ${rounds.join(', ')} · ';
    return '$which${live ? 'collecting now' : 'complete'}';
  }

  factory ProductVersion.fromJson(Map<String, dynamic> j) => ProductVersion(
        version: _int(j['version']),
        rounds: [for (final r in (j['rounds'] is List ? j['rounds'] as List : const [])) _int(r)],
        live: j['live'] == true,
      );
}

class CatalogProduct {
  const CatalogProduct({
    required this.id,
    required this.categoryId,
    required this.name,
    required this.categoryName,
    required this.questionCount,
    required this.surveyReady,
    required this.responsesCollected,
    required this.minResponses,
    this.surveyVersions = const [],
  });

  final int id;
  final int categoryId;
  final String name;
  final String categoryName;
  final int questionCount;
  final bool surveyReady;
  final int responsesCollected;
  final int minResponses;
  // Versions in fieldwork, oldest first. Empty for a product never surveyed (the
  // server then files the survey under its first round).
  final List<ProductVersion> surveyVersions;

  /// The version used without asking: the only one, else the one collecting now.
  ProductVersion? get defaultVersion {
    if (surveyVersions.length == 1) return surveyVersions.first;
    for (final v in surveyVersions) {
      if (v.live) return v;
    }
    return null;
  }

  factory CatalogProduct.fromJson(Map<String, dynamic> j) => CatalogProduct(
        id: _int(j['id']),
        categoryId: _int(j['category_id']),
        name: '${j['name'] ?? ''}',
        categoryName: '${j['category_name'] ?? ''}',
        questionCount: _int(j['question_count']),
        surveyReady: j['survey_ready'] == true,
        responsesCollected: _int(j['responses_collected']),
        minResponses: _int(j['min_responses'], 10),
        surveyVersions: [
          for (final v in (j['survey_versions'] is List ? j['survey_versions'] as List : const []))
            if (v is Map) ProductVersion.fromJson(Map<String, dynamic>.from(v)),
        ],
      );
}

class SurveyQuestionItem {
  const SurveyQuestionItem({
    required this.id,
    required this.position,
    required this.text,
    required this.type,
    required this.options,
    required this.required,
  });

  final int id;
  final int position;
  final String text;
  final String type; // rating | yes_no | single_choice | text
  final List<String> options;
  final bool required;

  factory SurveyQuestionItem.fromJson(Map<String, dynamic> j) => SurveyQuestionItem(
        id: _int(j['id']),
        position: _int(j['position']),
        text: '${j['text'] ?? ''}',
        type: '${j['question_type'] ?? 'text'}',
        options: (j['options'] is List) ? (j['options'] as List).map((e) => '$e').toList() : const [],
        required: j['required'] != false,
      );
}

/// A survey stored on the server (made on this phone, another phone or the web).
class ServerSurvey {
  const ServerSurvey({
    required this.responseId,
    required this.createdAt,
    required this.status,
    required this.executiveName,
    required this.productId,
    required this.productName,
    required this.categoryName,
    required this.respondentLabel,
    required this.area,
    this.feedbackId,
    this.rating,
    this.sentiment,
    this.tone,
    this.summary,
    this.analysisStatus,
    this.queuePosition,
  });

  final int responseId;
  final DateTime createdAt;
  final String status; // analyzed | submitted | draft
  final String executiveName;
  final int productId;
  final String productName;
  final String categoryName;
  final String respondentLabel; // short name only, e.g. "Priya S."
  final String area;
  final int? feedbackId;
  final double? rating;
  final String? sentiment;
  final String? tone; // positive | mixed | negative | neutral
  final String? summary;

  /// Analysis still running on the server: queued | processing | failed (null when none).
  final String? analysisStatus;
  final int? queuePosition;

  bool get isAnalyzed => status == 'analyzed' && feedbackId != null;
  bool get isAnalyzing => !isAnalyzed && (analysisStatus == 'queued' || analysisStatus == 'processing');

  factory ServerSurvey.fromJson(Map<String, dynamic> j) {
    final fb = j['feedback'] is Map ? Map<String, dynamic>.from(j['feedback'] as Map) : null;
    final an = j['analysis'] is Map ? Map<String, dynamic>.from(j['analysis'] as Map) : null;
    return ServerSurvey(
      responseId: _int(j['response_id']),
      createdAt: parseServerDate(j['created_at']) ?? DateTime.now(),
      status: '${j['status'] ?? ''}',
      executiveName: '${j['executive_name'] ?? ''}',
      productId: _int(j['product_id']),
      productName: '${j['product_name'] ?? ''}',
      categoryName: '${j['category_name'] ?? ''}',
      respondentLabel: '${j['respondent_label'] ?? 'Respondent'}',
      area: '${j['area'] ?? ''}',
      feedbackId: fb == null ? null : _int(fb['id']),
      rating: fb == null ? null : (fb['overall_rating'] as num?)?.toDouble(),
      sentiment: fb?['sentiment'] as String?,
      tone: fb?['tone'] as String?,
      summary: fb?['summary'] as String?,
      analysisStatus: an?['status'] as String?,
      queuePosition: (an?['position'] as num?)?.toInt(),
    );
  }
}

/// The backend stores UTC times without a zone marker; read them as UTC.
DateTime? parseServerDate(dynamic v) {
  if (v == null) return null;
  var s = '$v'.trim();
  if (s.isEmpty) return null;
  if (!RegExp(r'([zZ]|[+-]\d{2}:?\d{2})$').hasMatch(s)) s = '${s.replaceFirst(' ', 'T')}Z';
  return DateTime.tryParse(s)?.toLocal();
}

int _int(dynamic v, [int fallback = 0]) {
  if (v is int) return v;
  if (v is num) return v.toInt();
  return int.tryParse('$v') ?? fallback;
}

List<dynamic> _list(dynamic data, String key) {
  if (data is List) return data;
  if (data is Map && data[key] is List) return data[key] as List;
  return const [];
}

/* ============================================================
   API
   ============================================================ */

/// True when the failure means "no connection", so the survey should wait
/// on the phone and retry later rather than be marked as failed.
bool isConnectionProblem(Object e) {
  if (e is DioException) {
    return e.type == DioExceptionType.connectionError ||
        e.type.name.toLowerCase().contains('timeout') ||
        e.response == null;
  }
  return false;
}

/// Readable message from any API error.
String apiErrorText(Object e) {
  if (e is DioException) {
    final data = e.response?.data;
    final detail = data is Map ? data['detail'] : null;
    if (detail is String && detail.trim().isNotEmpty) return detail;
    if (detail is List && detail.isNotEmpty && detail.first is Map) {
      return '${(detail.first as Map)['msg'] ?? 'The server rejected the data.'}'.replaceFirst('Value error, ', '');
    }
    if (isConnectionProblem(e)) {
      return "No connection to the server (${ApiConfig.baseUrl}). It will upload when you're back online.";
    }
    return 'The server replied with an error (${e.response?.statusCode ?? '?'}).';
  }
  return '$e';
}

class SurveyApi {
  SurveyApi()
      : _dio = Dio(BaseOptions(
          baseUrl: ApiConfig.baseUrl,
          connectTimeout: const Duration(seconds: 10),
          receiveTimeout: const Duration(seconds: 30),
          sendTimeout: const Duration(seconds: 30),
          headers: {'Accept': 'application/json'},
        )) {
    // Identify the signed-in executive on every request.
    _dio.interceptors.add(InterceptorsWrapper(onRequest: (options, handler) {
      final token = Session.token;
      if (token != null) options.headers['Authorization'] = 'Bearer $token';
      handler.next(options);
    }));
  }

  final Dio _dio;

  dynamic _decode(dynamic data) => data is String && data.isNotEmpty ? jsonDecode(data) : data;

  Future<List<CatalogCategory>> categories() async {
    final r = await _dio.get('/categories');
    return _list(_decode(r.data), 'categories')
        .whereType<Map>()
        .map((m) => CatalogCategory.fromJson(Map<String, dynamic>.from(m)))
        .toList();
  }

  Future<List<CatalogProduct>> products() async {
    final r = await _dio.get('/products');
    return _list(_decode(r.data), 'products')
        .whereType<Map>()
        .map((m) => CatalogProduct.fromJson(Map<String, dynamic>.from(m)))
        .toList();
  }

  Future<List<SurveyQuestionItem>> questions(int productId) async {
    final r = await _dio.get('/products/$productId/questions');
    final list = _list(_decode(r.data), 'questions')
        .whereType<Map>()
        .map((m) => SurveyQuestionItem.fromJson(Map<String, dynamic>.from(m)))
        .toList();
    list.sort((a, b) => a.position.compareTo(b.position));
    return list;
  }

  /// Recent surveys from the server, newest first (all products),
  /// with the overall number of surveys on the server.
  Future<(List<ServerSurvey>, int)> recentSurveys({String? executive, int limit = 200}) async {
    final r = await _dio.get('/product-feedback/surveys', queryParameters: {
      'limit': limit,
      if (executive != null && executive.trim().isNotEmpty) 'executive': executive.trim(),
    });
    final data = _decode(r.data);
    final list = _list(data, 'surveys')
        .whereType<Map>()
        .map((m) => ServerSurvey.fromJson(Map<String, dynamic>.from(m)))
        .toList();
    final total = data is Map ? _int(data['total'], list.length) : list.length;
    return (list, total);
  }

  /// Products with their feedback totals and report status (same as the web).
  Future<List<Map<String, dynamic>>> productFeedbackList() async {
    final r = await _dio.get('/product-feedback');
    return _list(_decode(r.data), 'products').whereType<Map>().map((m) => Map<String, dynamic>.from(m)).toList();
  }

  /// One product: summary, responses and the latest consolidated report.
  Future<Map<String, dynamic>> productFeedback(int productId) async {
    final r = await _dio.get('/product-feedback/$productId');
    final d = _decode(r.data);
    return d is Map ? Map<String, dynamic>.from(d) : <String, dynamic>{};
  }

  /// Generates a new consolidated report (usually under a minute).
  Future<Map<String, dynamic>> generateReport(int productId) async {
    final r = await _dio.post(
      '/product-feedback/$productId/consolidate',
      options: Options(receiveTimeout: const Duration(minutes: 15)),
    );
    final d = _decode(r.data);
    return d is Map ? Map<String, dynamic>.from(d) : <String, dynamic>{};
  }

  Future<int> createRespondent(Map<String, dynamic> respondent) async {
    final r = await _dio.post('/surveys/respondents', data: respondent);
    final d = _decode(r.data);
    return _int(d is Map ? (d['id'] ?? d['respondent_id']) : null);
  }

  Future<int> createResponse(Map<String, dynamic> response) async {
    final r = await _dio.post('/surveys/responses', data: response);
    final d = _decode(r.data);
    return _int(d is Map ? (d['id'] ?? d['response_id']) : null);
  }

  /// Links a survey to the signed-in account (so it counts in "my surveys").
  Future<void> claimResponse(int responseId) async {
    await _dio.post('/auth/me/claim/$responseId');
  }

  Future<void> saveAnswers(int responseId, List<Map<String, dynamic>> answers) async {
    await _dio.put('/surveys/responses/$responseId/answers', data: {'answers': answers});
  }

  Future<void> submit(int responseId) async {
    await _dio.post('/surveys/responses/$responseId/submit');
  }

  /// Queues the recording for analysis; returns at once with the job.
  /// Throws a DioException with status 404 on an older backend without the queue.
  Future<Map<String, dynamic>> startAnalysis({
    required String path,
    required String filename,
    required int responseId,
    void Function(double progress)? onProgress,
  }) async {
    final form = FormData.fromMap({
      'file': await MultipartFile.fromFile(path, filename: filename),
      'response_id': '$responseId',
    });
    final r = await _dio.post(
      '/analysis/jobs',
      data: form,
      options: Options(sendTimeout: const Duration(minutes: 15)),
      onSendProgress: (sent, total) {
        if (total > 0) onProgress?.call(sent / total);
      },
    );
    final d = _decode(r.data);
    return d is Map ? Map<String, dynamic>.from(d) : <String, dynamic>{};
  }

  /// Status of an analysis job: queued | processing | done | failed.
  Future<Map<String, dynamic>> analysisJob(int jobId) async {
    final r = await _dio.get('/analysis/jobs/$jobId');
    final d = _decode(r.data);
    return d is Map ? Map<String, dynamic>.from(d) : <String, dynamic>{};
  }

  /// Which of these surveys still exist on the server (deleted ones disappear from the app).
  Future<Set<int>> existingSurveys(List<int> ids) async {
    if (ids.isEmpty) return <int>{};
    final r = await _dio.post('/auth/me/surveys/existing', data: {'ids': ids});
    final d = _decode(r.data);
    final list = d is Map ? (d['existing'] as List? ?? const []) : const [];
    return list.map((e) => _int(e)).toSet();
  }

  /// Uploads the recording linked to the survey and waits for the analysis.
  /// onProgress: 0..1 while uploading; the analysis follows (a few minutes).
  Future<Map<String, dynamic>> uploadRecording({
    required String path,
    required String filename,
    required int responseId,
    void Function(double progress)? onProgress,
  }) async {
    final form = FormData.fromMap({
      'file': await MultipartFile.fromFile(path, filename: filename),
      'response_id': '$responseId',
    });
    final r = await _dio.post(
      '/video/upload',
      data: form,
      options: Options(sendTimeout: const Duration(minutes: 15), receiveTimeout: const Duration(minutes: 40)),
      onSendProgress: (sent, total) {
        if (total > 0) onProgress?.call(sent / total);
      },
    );
    final d = _decode(r.data);
    return d is Map ? Map<String, dynamic>.from(d) : <String, dynamic>{};
  }
}