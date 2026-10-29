import 'dart:convert';

import 'package:dio/dio.dart';

import 'api_service.dart';
import 'org_api.dart' show Json, asJson, asJsonList;
import 'session.dart';

/* ============================================================
   Admin API (role "admin")
   ============================================================
   The same backend routes the web admin dashboard uses:
     /product-feedback, /product-feedback/surveys   Home and Survey history
     /categories, /products                          Catalog (read-only for admins)
     /admin/users...                                 Team: members, add, switches,
                                                     reset password, sign out, products
   Admins don't take surveys, so nothing here records or uploads.
*/

class AdminApi {
  AdminApi._();

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
    final data = e.response?.data;
    if (data is Map && data['detail'] != null) {
      final d = data['detail'];
      if (d is String && d.isNotEmpty) return d;
      if (d is List && d.isNotEmpty) {
        return d.map((x) => x is Map ? '${x['msg'] ?? ''}' : '$x').where((s) => s.isNotEmpty).join(' ');
      }
    }
    switch (e.type) {
      case DioExceptionType.connectionTimeout:
      case DioExceptionType.connectionError:
      case DioExceptionType.receiveTimeout:
      case DioExceptionType.sendTimeout:
        return "Can't reach the server at ${ApiConfig.baseUrl}. Check the connection and try again.";
      default:
        break;
    }
    final status = e.response?.statusCode;
    if (status == 401) return 'Your session has ended. Log in again.';
    if (status == 403) return "Your account can't do this.";
    return 'Something went wrong${status != null ? ' ($status)' : ''}. Try again.';
  }

  // ---------------------------------------------------------------- Home

  /// Every product's feedback: responses, rating, sentiment, rounds, report state.
  static Future<Json> productFeedback() => _send('GET', '/product-feedback');

  /// The latest surveys of every survey person (newest first).
  static Future<Json> recentSurveys({int limit = 8}) => _send('GET', '/product-feedback/surveys', query: {'limit': limit});

  // ---------------------------------------------------------------- Catalog (read-only)

  static Future<List<Json>> categories() async =>
      asJsonList((await _send('GET', '/categories', query: {'include_inactive': false}))['categories']);

  static Future<List<Json>> products() async =>
      asJsonList((await _send('GET', '/products', query: {'include_inactive': false}))['products']);

  /// Who can survey a product (Catalog → product): user ids; empty = every survey executive.
  static Future<List<int>> productExecutives(int productId) async {
    final ids = (await _send('GET', '/admin/products/$productId/executives'))['user_ids'];
    return ids is List ? ids.map((e) => (e as num).toInt()).toList() : <int>[];
  }

  static Future<Json> saveProductExecutives(int productId, List<int> userIds) =>
      _send('PUT', '/admin/products/$productId/executives', body: {'user_ids': userIds});

  /// The product's survey rounds (newest first in the UI).
  static Future<List<Json>> productRounds(int productId) async =>
      asJsonList((await _send('GET', '/products/$productId/rounds'))['rounds']);

  // ---------------------------------------------------------------- Team

  /// Members with their survey numbers ("today" in the phone's time zone).
  static Future<List<Json>> team() async {
    final offset = -DateTime.now().timeZoneOffset.inMinutes; // like the web's getTimezoneOffset()
    return asJsonList((await _send('GET', '/admin/users', query: {'tz_offset': offset}))['users']);
  }

  /// { can_view_history?, is_active? }
  static Future<Json> updateMember(int id, Json changes) => _send('PUT', '/admin/users/$id', body: changes);

  /// { name, mobile, email?, password, can_view_history } -> a survey person's login.
  static Future<Json> addMember(Json member) => _send('POST', '/admin/users', body: member);

  static Future<Json> resetPassword(int id, String password) =>
      _send('PUT', '/admin/users/$id/password', body: {'password': password});

  /// Ends every sign-in of the member (lost or shared phone); the password stays.
  static Future<Json> signOutMember(int id) => _send('POST', '/admin/users/$id/sign-out', body: <String, dynamic>{});

  /// Products the member may survey; empty = every product not assigned to someone else.
  static Future<List<int>> memberProducts(int id) async {
    final ids = (await _send('GET', '/admin/users/$id/products'))['product_ids'];
    return ids is List ? ids.map((e) => (e as num).toInt()).toList() : <int>[];
  }

  static Future<Json> saveMemberProducts(int id, List<int> productIds) =>
      _send('PUT', '/admin/users/$id/products', body: {'product_ids': productIds});
}