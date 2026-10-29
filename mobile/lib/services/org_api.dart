import 'dart:convert';

import 'package:dio/dio.dart';

import 'api_service.dart';
import 'session.dart';

/* ============================================================
   Organization (client role) API
   ============================================================
   The same backend routes the organization's web portal uses:
     /org/...                         overview, products, reports, rounds, updates
     /products/{id}/recipe            recipe (saving creates a new version)
     /products/{id}/questions, /questions/{id}   survey questions
     /categories, /products           catalog (organization-only changes; the
                                      server notifies the admins of each one)
   The server checks every permission; the app only shows what the
   organization may do.
*/

typedef Json = Map<String, dynamic>;

Json asJson(dynamic v) => v is Map ? Map<String, dynamic>.from(v) : <String, dynamic>{};
List<Json> asJsonList(dynamic v) => v is List ? v.map(asJson).toList() : <Json>[];
String str(dynamic v, [String fallback = '']) => (v == null || '$v'.trim().isEmpty || '$v' == 'null') ? fallback : '$v';
num? numOrNull(dynamic v) => v is num ? v : num.tryParse('${v ?? ''}');
int intOf(dynamic v, [int fallback = 0]) => (numOrNull(v) ?? fallback).toInt();

class OrgApi {
  OrgApi._();

  static final Dio _dio = Dio(
    BaseOptions(
      baseUrl: ApiConfig.baseUrl,
      connectTimeout: const Duration(seconds: 10),
      receiveTimeout: const Duration(seconds: 30),
      sendTimeout: const Duration(seconds: 30),
      headers: {'Accept': 'application/json'},
    ),
  )..interceptors.add(InterceptorsWrapper(onRequest: (options, handler) {
      final token = Session.token;
      if (token != null) options.headers['Authorization'] = 'Bearer $token';
      handler.next(options);
    }));

  // ---------------------------------------------------------------- plumbing

  static Future<Json> _send(String method, String path, {Object? body, Map<String, dynamic>? query}) async {
    try {
      final r = await _dio.request(path, data: body, queryParameters: query, options: Options(method: method));
      final data = r.data is String && (r.data as String).isNotEmpty ? jsonDecode(r.data as String) : r.data;
      return asJson(data);
    } on DioException catch (e) {
      throw ApiException(_describe(e));
    } on FormatException {
      throw ApiException('The server sent data the app could not read.');
    }
  }

  static String _describe(DioException e) {
    final status = e.response?.statusCode;
    final data = e.response?.data;
    String detail = '';
    if (data is Map && data['detail'] != null) {
      final d = data['detail'];
      if (d is String) {
        detail = d;
      } else if (d is List && d.isNotEmpty) {
        // FastAPI validation errors: [{loc, msg, ...}]
        detail = d.map((x) => x is Map ? '${x['msg'] ?? ''}' : '$x').where((s) => s.isNotEmpty).join(' ');
      }
    }
    if (detail.isNotEmpty) return detail;
    switch (e.type) {
      case DioExceptionType.connectionTimeout:
      case DioExceptionType.connectionError:
      case DioExceptionType.receiveTimeout:
      case DioExceptionType.sendTimeout:
        return "Can't reach the server at ${ApiConfig.baseUrl}. Check the connection and try again.";
      default:
        break;
    }
    if (status == 401) return 'Your session has ended. Log in again.';
    if (status == 403) return "Your account can't do this.";
    if (status == 404) return 'Not found. It may have been removed.';
    return 'Something went wrong${status != null ? ' ($status)' : ''}. Try again.';
  }

  static Future<Json> _get(String path, [Map<String, dynamic>? query]) => _send('GET', path, query: query);
  static Future<Json> _post(String path, [Object? body]) => _send('POST', path, body: body ?? <String, dynamic>{});
  static Future<Json> _put(String path, Object body) => _send('PUT', path, body: body);
  static Future<Json> _delete(String path) => _send('DELETE', path);

  // ---------------------------------------------------------------- portal

  static Future<Json> overview() => _get('/org/overview');
  static Future<List<Json>> products() async => asJsonList((await _get('/org/products'))['products']);
  static Future<Json> product(int id) => _get('/org/products/$id');

  /// Report for one round (latest when [round] is null).
  static Future<Json> report(int id, {int? round}) => _get('/org/products/$id/report', {if (round != null) 'round': round});

  static Future<Json> compare(int id, {int? a, int? b}) =>
      _get('/org/products/$id/compare', {if (a != null) 'a': a, if (b != null) 'b': b});

  static Future<Json> startRound(int id, {int? target, String? note, String? dueDate}) => _post('/org/products/$id/rounds', {
        if (target != null) 'target': target,
        if (note != null && note.trim().isNotEmpty) 'note': note.trim(),
        if (dueDate != null && dueDate.isNotEmpty) 'due_date': dueDate,
      });

  static Future<Json> endRound(int id, int number) => _post('/org/products/$id/rounds/$number/end');

  static Future<Json> updates() => _get('/org/updates');
  static Future<int> unreadUpdates() async => intOf((await _get('/org/updates/unread'))['unread']);
  static Future<void> markUpdatesSeen() async {
    await _post('/org/updates/seen');
  }
  static Future<Json> account() => _get('/org/account');

  // ---------------------------------------------------------------- recipe

  static Future<Json> recipe(int productId) async => asJson((await _get('/products/$productId/recipe'))['recipe']);
  static Future<Json> saveRecipe(int productId, Json recipe) => _put('/products/$productId/recipe', recipe);

  // ---------------------------------------------------------------- survey questions

  static Future<List<Json>> questions(int productId) async =>
      asJsonList((await _get('/products/$productId/questions'))['questions']);

  static Future<Json> addQuestion(int productId, Json body) => _post('/products/$productId/questions', body);
  static Future<Json> updateQuestion(int questionId, Json body) => _put('/questions/$questionId', body);
  static Future<Json> removeQuestion(int questionId) => _delete('/questions/$questionId');
  static Future<Json> reorderQuestions(int productId, List<int> ids) =>
      _put('/products/$productId/questions/order', {'question_ids': ids});

  // ---------------------------------------------------------------- catalog

  static Future<List<Json>> categories({bool includeInactive = true}) async =>
      asJsonList((await _get('/categories', {'include_inactive': includeInactive}))['categories']);

  static Future<List<Json>> catalogProducts({bool includeInactive = true}) async =>
      asJsonList((await _get('/products', {'include_inactive': includeInactive}))['products']);

  static Future<Json> createCategory(String name, String? description) =>
      _post('/categories', {'name': name, if (description != null && description.isNotEmpty) 'description': description});

  static Future<Json> updateCategory(int id, Json changes) => _put('/categories/$id', changes);
  static Future<Json> deactivateCategory(int id) => _delete('/categories/$id');

  static Future<Json> createProduct(Json body) => _post('/products', body);
  static Future<Json> updateProduct(int id, Json changes) => _put('/products/$id', changes);
  static Future<Json> deactivateProduct(int id) => _delete('/products/$id');
}
