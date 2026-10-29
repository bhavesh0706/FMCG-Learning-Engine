import 'dart:convert';

import 'package:dio/dio.dart';
import 'package:flutter/foundation.dart' show TargetPlatform, defaultTargetPlatform;

import 'session.dart';

/* ============================================================
   Where the backend is
   ============================================================
   Android emulator  -> http://10.0.2.2:8000    (default on Android)
   iOS Simulator     -> http://127.0.0.1:8000   (default on iOS: the Simulator runs on the Mac)
   Real phone (Android or iPhone) -> the computer's Wi-Fi IP, for example:
     flutter run --dart-define=API_BASE_URL=http://192.168.1.20:8000
*/
class ApiConfig {
  static const String _fromBuild = String.fromEnvironment('API_BASE_URL');

  static final String baseUrl = _fromBuild.isNotEmpty
      ? _fromBuild
      : defaultTargetPlatform == TargetPlatform.iOS
          ? 'http://127.0.0.1:8000'
          : 'http://10.0.2.2:8000';
}

class ApiException implements Exception {
  ApiException(this.message);

  final String message;

  @override
  String toString() => message;
}

/* ============================================================
   API calls
   ============================================================ */

class ApiService {
  ApiService({Dio? dio})
      : _dio = dio ??
            (Dio(
              BaseOptions(
                baseUrl: ApiConfig.baseUrl,
                connectTimeout: const Duration(seconds: 10),
                receiveTimeout: const Duration(seconds: 30),
                sendTimeout: const Duration(seconds: 30),
                headers: {'Accept': 'application/json'},
              ),
            )..interceptors.add(InterceptorsWrapper(onRequest: (options, handler) {
                // The server requires sign-in for every request.
                final token = Session.token;
                if (token != null) options.headers['Authorization'] = 'Bearer $token';
                handler.next(options);
              })));

  final Dio _dio;

  /// Base URL in use, handy for error messages and media links.
  static String get baseUrl => ApiConfig.baseUrl;

  Future<dynamic> _getJson(String path) async {
    try {
      final response = await _dio.get(path);
      final data = response.data;
      // Dio usually decodes JSON already; decode it if it arrived as text.
      return data is String ? jsonDecode(data) : data;
    } on DioException catch (e) {
      throw ApiException(_describe(e));
    } on FormatException {
      throw ApiException('The server sent data the app could not read.');
    }
  }

  String _describe(DioException e) {
    // Timeouts are matched by name so this keeps compiling when Dio adds
    // new timeout types (5.11 added transformTimeout, for example).
    if (e.type.name.toLowerCase().contains('timeout')) {
      return 'The server took too long to reply. Pull down to try again.';
    }

    switch (e.type) {
      case DioExceptionType.connectionError:
        return "Can't reach the server at ${ApiConfig.baseUrl}. Check that the "
            'backend is running and this phone is on the same network.';
      case DioExceptionType.badResponse:
        final code = e.response?.statusCode;
        // The backend explains rejected files (e.g. "This file is incomplete...").
        final data = e.response?.data;
        final detail = data is Map ? data['detail'] : null;
        if (detail is String && detail.trim().isNotEmpty) return detail;
        if (code == 404) return 'That record was not found on the server.';
        return 'The server replied with an error${code == null ? '' : ' ($code)'}.';
      case DioExceptionType.cancel:
        return 'The request was cancelled.';
      case DioExceptionType.badCertificate:
        return 'The server certificate was rejected.';
      default:
        final msg = '${e.error ?? e.message ?? ''}';
        if (msg.toLowerCase().contains('cleartext') || msg.toLowerCase().contains('insecure http')) {
          return 'Android blocked the plain http:// connection. Allow cleartext traffic in AndroidManifest.xml.';
        }
        return "Can't reach the server at ${ApiConfig.baseUrl}${msg.isEmpty ? '.' : ': $msg'}";
    }
  }

  /// GET /feedback
  Future<List<FeedbackRecord>> getFeedbackHistory() async {
    final data = await _getJson('/feedback');
    final list = data is Map ? (data['feedback'] ?? data['items'] ?? const []) : data;
    if (list is! List) return const [];
    return list
        .whereType<Map>()
        .map((m) => FeedbackRecord.fromJson(Map<String, dynamic>.from(m)))
        .toList();
  }

  /// GET /feedback/{id}
  Future<FeedbackRecord> getFeedbackById(int id) async {
    final data = await _getJson('/feedback/$id');
    if (data is! Map) throw ApiException('The server sent data the app could not read.');
    return FeedbackRecord.fromJson(Map<String, dynamic>.from(data));
  }

  /// URL of the original video or audio, for a player or a download.
  /// Players can't send headers, so the sign-in token goes in the address (recordings only).
  String mediaUrl(int id) {
    final token = Session.token;
    return '${ApiConfig.baseUrl}/feedback/$id/video${token == null ? '' : '?token=${Uri.encodeQueryComponent(token)}'}';
  }

  /// POST /video/upload
  /// The server transcribes and analyzes before replying, which can take
  /// several minutes for a long video, so this call waits up to 30 minutes.
  Future<FeedbackRecord> uploadMedia(
    String path, {
    required String filename,
    void Function(double progress)? onSendProgress,
    CancelToken? cancelToken,
  }) async {
    try {
      final form = FormData.fromMap({
        'file': await MultipartFile.fromFile(path, filename: filename),
      });
      final response = await _dio.post(
        '/video/upload',
        data: form,
        cancelToken: cancelToken,
        options: Options(
          sendTimeout: const Duration(minutes: 15),
          receiveTimeout: const Duration(minutes: 30),
        ),
        onSendProgress: (sent, total) {
          if (total > 0) onSendProgress?.call(sent / total);
        },
      );
      var data = response.data;
      if (data is String) data = jsonDecode(data);
      if (data is! Map) throw ApiException('The server sent data the app could not read.');
      return FeedbackRecord.fromJson(Map<String, dynamic>.from(data));
    } on DioException catch (e) {
      if (CancelToken.isCancel(e)) throw ApiException('The upload was cancelled.');
      throw ApiException(_describe(e));
    } on FormatException {
      throw ApiException('The server sent data the app could not read.');
    }
  }
}

/* ============================================================
   Models
   ============================================================ */

enum ReviewTone { positive, mixed, negative, neutral }

class ToneInfo {
  const ToneInfo(this.tone, this.label);

  final ReviewTone tone;
  final String label;
}

class FeedbackRecord {
  FeedbackRecord({
    required this.id,
    required this.filename,
    required this.uploadedAt,
    required this.durationSeconds,
    required this.processingSeconds,
    required this.sentiment,
    required this.overallRating,
    required this.summary,
    required this.transcript,
    required this.chapters,
    required this.aspectRatings,
  });

  final int id;
  final String filename;
  final DateTime? uploadedAt; // already converted to local time
  final double durationSeconds;
  final double? processingSeconds;
  final String sentiment; // label stored by the backend
  final double? overallRating;
  final String summary; // always plain text, whatever shape the backend used
  final String transcript;
  final List<FeedbackPart> chapters;
  final List<AspectRating> aspectRatings;

  /// The backend label is often "Neutral" even for happy customers,
  /// so read the summary's overall-sentiment sentence first.
  ToneInfo get tone {
    final items = [for (final c in chapters) ...c.feedback];
    if ((overallRating ?? 0) > 0 || sentiment.trim().isNotEmpty || items.isNotEmpty) {
      return toneForFeedback(overallRating, sentiment, items);
    }
    return toneFromSummary(summary, sentiment); // old records with nothing else to go on
  }

  /// First paragraph of the summary, for list cards.
  String get overview => summaryOverview(summary);

  bool get isAudio =>
      RegExp(r'^audio-recording|\.(m4a|mp3|wav|ogg|oga|opus|aac)$', caseSensitive: false)
          .hasMatch(filename);

  factory FeedbackRecord.fromJson(Map<String, dynamic> j) {
    return FeedbackRecord(
      id: _toInt(j['id'] ?? j['feedback_id']) ?? 0,
      filename: _toStr(j['filename']).isEmpty ? 'Customer feedback' : _toStr(j['filename']),
      uploadedAt: _toDate(j['uploaded_at'] ?? j['created_at']),
      durationSeconds: _toDouble(j['video_duration_seconds'] ?? j['duration_seconds']) ?? 0,
      processingSeconds: _toDouble(j['processing_time_seconds']),
      sentiment: _toStr(j['overall_sentiment'] ?? j['sentiment']).isEmpty
          ? 'Neutral'
          : _toStr(j['overall_sentiment'] ?? j['sentiment']),
      overallRating: _toDouble(j['overall_rating']),
      summary: summaryToText(j['summary']),
      transcript: _transcriptToText(j['transcript']),
      chapters: _list(j['chapters']).map(FeedbackPart.fromJson).toList(),
      aspectRatings: _list(j['aspect_ratings']).map(AspectRating.fromJson).toList(),
    );
  }
}

class FeedbackPart {
  FeedbackPart({
    required this.part,
    required this.start,
    required this.end,
    required this.sentiment,
    required this.summary,
    required this.keyPoint,
    required this.transcript,
    required this.feedback,
  });

  final int part;
  final double start;
  final double end;
  final String sentiment;
  final String summary;
  final String keyPoint;
  final String transcript;
  final List<AspectFeedback> feedback;

  factory FeedbackPart.fromJson(Map<String, dynamic> j) {
    return FeedbackPart(
      part: _toInt(j['part']) ?? 0,
      start: _toDouble(j['start']) ?? 0,
      end: _toDouble(j['end']) ?? 0,
      sentiment: _toStr(j['sentiment']),
      summary: _toStr(j['summary']),
      keyPoint: _toStr(j['key_point']),
      transcript: _transcriptToText(j['transcript']),
      feedback: _list(j['feedback']).map(AspectFeedback.fromJson).toList(),
    );
  }
}

class AspectFeedback {
  AspectFeedback({
    required this.name,
    required this.category,
    required this.rating,
    required this.sentiment,
    required this.severity,
    required this.evidence,
    required this.mentions,
  });

  final String name;
  final String category;
  final double? rating;
  final String sentiment;
  final String severity;
  final String evidence;
  final int mentions;

  factory AspectFeedback.fromJson(Map<String, dynamic> j) {
    return AspectFeedback(
      name: _toStr(j['aspect_name'] ?? j['aspect'] ?? j['aspect_key']),
      category: _toStr(j['category']),
      rating: _toDouble(j['rating']),
      sentiment: _toStr(j['sentiment']),
      severity: _toStr(j['severity']),
      evidence: _toStr(j['evidence']),
      mentions: _toInt(j['mentions']) ?? 1,
    );
  }
}

class AspectRating {
  AspectRating({required this.name, required this.category, required this.rating});

  final String name;
  final String category;
  final double? rating;

  factory AspectRating.fromJson(Map<String, dynamic> j) {
    return AspectRating(
      name: _toStr(j['aspect_name'] ?? j['aspect_key']),
      category: _toStr(j['category']).isEmpty ? 'General' : _toStr(j['category']),
      rating: _toDouble(j['rating']),
    );
  }
}

/* ============================================================
   Summary helpers (same rules as the web app)
   ============================================================ */

/// Turns a summary that may be text, JSON text, a map or a list into plain
/// "Heading:" lines with "- item" bullets.
String summaryToText(dynamic summary) {
  if (summary == null) return '';
  if (summary is String) {
    final t = summary.trim();
    if ((t.startsWith('{') && t.endsWith('}')) || (t.startsWith('[') && t.endsWith(']'))) {
      try {
        return summaryToText(jsonDecode(t));
      } catch (_) {/* plain text */}
    }
    return summary;
  }
  if (summary is List) return summary.map((i) => '- ${_itemText(i)}').join('\n');
  if (summary is Map) {
    final parts = <String>[];
    summary.forEach((key, value) {
      if (value == null || value == '') return;
      final heading = _titleCase('$key');
      if (value is List) {
        if (value.isEmpty) return;
        parts.add('$heading:\n${value.map((v) => '- ${_itemText(v)}').join('\n')}');
      } else if (value is Map) {
        parts.add('$heading:\n${summaryToText(value)}');
      } else {
        parts.add('$heading:\n$value');
      }
    });
    return parts.join('\n\n');
  }
  return '$summary';
}

/// One summary line, cleaned of markdown, with its role.
enum SummaryLineKind { heading, bullet, text }

class SummaryLine {
  const SummaryLine(this.kind, this.text);

  final SummaryLineKind kind;
  final String text;
}

List<SummaryLine> parseSummaryLines(String summary) {
  final out = <SummaryLine>[];
  for (final raw in summary.split('\n')) {
    final isBold = RegExp(r'^\*\*[^*]+\*\*:?$').hasMatch(raw.trim());
    final isMdHeading = RegExp(r'^#{1,6}\s+').hasMatch(raw.trim());
    final line = raw.trim().replaceFirst(RegExp(r'^#{1,6}\s+'), '').replaceAll('**', '').trim();
    if (line.isEmpty) continue;

    final bullet = RegExp(r'^([-•*]|\d+[.)])\s+');
    if (bullet.hasMatch(line)) {
      out.add(SummaryLine(SummaryLineKind.bullet, line.replaceFirst(bullet, '')));
    } else if (isMdHeading || isBold || (line.endsWith(':') && line.length < 60)) {
      out.add(SummaryLine(SummaryLineKind.heading, line.replaceFirst(RegExp(r':$'), '')));
    } else {
      // "Overall Sentiment: The customer is..." on one line
      final inline = RegExp(r'^([A-Za-z][A-Za-z /&-]{2,40}):\s+(.+)$').firstMatch(line);
      if (inline != null && RegExp(r'sentiment|overview|summary|verdict', caseSensitive: false).hasMatch(inline.group(1)!)) {
        out.add(SummaryLine(SummaryLineKind.heading, inline.group(1)!));
        out.add(SummaryLine(SummaryLineKind.text, inline.group(2)!));
      } else {
        out.add(SummaryLine(SummaryLineKind.text, line));
      }
    }
  }
  return out;
}

/// The overall-sentiment sentence(s): text lines before the first other heading.
String summaryOverview(String summary) {
  final lines = parseSummaryLines(summary);
  final buffer = <String>[];
  var inOverview = true;
  for (final l in lines) {
    if (l.kind == SummaryLineKind.heading) {
      final isOverviewHeading =
          RegExp(r'sentiment|overview|summary|verdict', caseSensitive: false).hasMatch(l.text);
      if (buffer.isNotEmpty && !isOverviewHeading) break;
      inOverview = isOverviewHeading || buffer.isEmpty;
      continue;
    }
    if (l.kind == SummaryLineKind.text && inOverview) buffer.add(l.text);
  }
  return buffer.join(' ');
}

ToneInfo toneFromSummary(String summary, String backendLabel) {
  final t = summaryOverview(summary).toLowerCase();
  if (t.isNotEmpty) {
    final pos = RegExp(r'positive|(?<!dis)satisf|happy|pleased|favou?rable|impressed').hasMatch(t);
    final neg = RegExp(r'negative|dissatisf|unhappy|disappoint|frustrat|unsatisf').hasMatch(t);
    if (t.contains('mixed') || (pos && neg)) return const ToneInfo(ReviewTone.mixed, 'Mixed');
    if (neg) return const ToneInfo(ReviewTone.negative, 'Negative');
    if (pos) {
      return RegExp(r'\bbut\b|however|concern|issue|some areas|improvement').hasMatch(t)
          ? const ToneInfo(ReviewTone.mixed, 'Positive, with concerns')
          : const ToneInfo(ReviewTone.positive, 'Positive');
    }
  }
  return toneFromLabel(backendLabel);
}

/// THE sentiment rule for one customer's feedback, used on every screen.
/// Exact copy of the backend's response_tone() (app/services/consolidation.py)
/// and the web's feedbackTone(): change all three together.
ToneInfo toneForFeedback(double? rating, String label, List<AspectFeedback> items) {
  String s(String? x) => (x ?? '').trim().toLowerCase();
  final pos = items.where((i) => s(i.sentiment) == 'positive').length;
  final neg = items.fold<double>(
    0,
    (n, i) => n + (s(i.sentiment) == 'negative' ? 1 : (s(i.sentiment) == 'positive with concerns' ? 0.5 : 0)),
  );
  String tone;
  final r = rating ?? 0;
  if (r > 0) {
    if (r >= 4) {
      tone = (neg < pos || neg == 0) ? 'positive' : 'mixed';
    } else if (r < 2.5) {
      tone = pos <= neg / 2 ? 'negative' : 'mixed';
    } else {
      tone = 'mixed';
    }
  } else {
    final l = s(label);
    if (l == 'praise' || l == 'positive') {
      tone = (neg < pos || neg == 0) ? 'positive' : 'mixed';
    } else if (l == 'issue' || l == 'negative') {
      tone = pos <= neg / 2 ? 'negative' : 'mixed';
    } else if (l == 'suggestion' || l == 'mixed') {
      tone = 'mixed';
    } else if (pos > 0 && neg > 0) {
      tone = 'mixed';
    } else {
      tone = pos > 0 ? 'positive' : (neg > 0 ? 'negative' : 'neutral');
    }
  }
  return toneFromLabel(tone);
}

ToneInfo toneFromLabel(String label) {
  final l = label.toLowerCase();
  if (RegExp(r'positive|praise').hasMatch(l)) return const ToneInfo(ReviewTone.positive, 'Positive');
  if (RegExp(r'negative|issue').hasMatch(l)) return const ToneInfo(ReviewTone.negative, 'Negative');
  if (RegExp(r'mixed|suggestion|concern').hasMatch(l)) return const ToneInfo(ReviewTone.mixed, 'Mixed');
  return const ToneInfo(ReviewTone.neutral, 'Neutral');
}

/* ============================================================
   Parsing helpers
   ============================================================ */

List<Map<String, dynamic>> _list(dynamic v) {
  if (v is! List) return const [];
  return v.whereType<Map>().map((m) => Map<String, dynamic>.from(m)).toList();
}

String _toStr(dynamic v) => v == null ? '' : '$v'.trim();

double? _toDouble(dynamic v) {
  if (v == null) return null;
  if (v is num) return v.toDouble();
  return double.tryParse('$v');
}

int? _toInt(dynamic v) {
  if (v == null) return null;
  if (v is int) return v;
  if (v is num) return v.round();
  return int.tryParse('$v');
}

/// The backend stores UTC without a timezone marker; treat it as UTC.
DateTime? _toDate(dynamic v) {
  final s = _toStr(v);
  if (s.isEmpty) return null;
  final hasZone = RegExp(r'([zZ]|[+-]\d{2}:?\d{2})$').hasMatch(s);
  final parsed = DateTime.tryParse(hasZone ? s : '${s.replaceFirst(' ', 'T')}Z');
  return parsed?.toLocal();
}

String _transcriptToText(dynamic v) {
  if (v is List) {
    return v
        .map((s) => s is Map ? _toStr(s['text']) : _toStr(s))
        .where((s) => s.isNotEmpty)
        .join(' ');
  }
  return _toStr(v);
}

String _itemText(dynamic item) {
  if (item is Map) {
    for (final k in ['text', 'point', 'description', 'summary', 'action', 'issue', 'aspect_name', 'aspect']) {
      final v = _toStr(item[k]);
      if (v.isNotEmpty) return v;
    }
    return item.values.whereType<String>().join(' - ');
  }
  return _toStr(item);
}

String _titleCase(String key) => key
    .replaceAll(RegExp(r'[_-]+'), ' ')
    .split(' ')
    .map((w) => w.isEmpty ? w : '${w[0].toUpperCase()}${w.substring(1)}')
    .join(' ');

/* ============================================================
   Insights: liked / issues / actions (same rules as the web app)
   ============================================================ */

final List<RegExp> _negative = [
  r'melt', r'\btor[en]\b', r'\btear', r'sticky', r'sticking', r'\bbroke', r'leak',
  r'expensive', r'costly', r'(price|cost).{0,20}(high|more)', r'(higher|more).{0,12}(price|cost)',
  r'damag', r'\bbad\b', r'\bpoor', r'difficult', r'disappoint', r'stale', r'complain',
  r'problem', r'\bissues?\b', r'\bnot (a |an |very |that |so |really |at all )?(good|great|nice|tasty|happy|satisfied|pleased|impressed|worth)',
  r'\b(dislikes?|disliked|unsatisf|dissatisf|hate[sd]?|worst|terrible|awful)',
].map((p) => RegExp(p, caseSensitive: false)).toList();

final List<RegExp> _positive = [
  r'\bgood\b', r'\bgreat\b', r'smooth', r'\blov(e|ed)\b', r'enjoy', r'tasty', r'delicious',
  r'balanced', r'fresh', r'recommend', r'\bprefer', r'sturdy', r'excellent', r'\bnice\b',
  r'\bhappy\b', r'\bliked?\b', r'(?<!dis)satisf', r'\beasy\b',
].map((p) => RegExp(p, caseSensitive: false)).toList();

// Polite criticism: customers often say "could be better" instead of "bad".
final List<RegExp> _improvement = [
  r"\b(does ?n[o']t|doesn't|did ?n[o']t|do ?n[o']t|not) (last|stay|work|help|clean|remove|suit|lather|dissolve)",
  r'\b(could|can|should|needs? to|would|must) be (made )?(a (little|bit) |slightly |much |even |a lot )?(better|improved|stronger|more|less|cheaper|bigger|smaller|easier)',
  r'\b(needs?|requires?|could use|scope for|room for|rooms for) (some |a little |more |further )?(improvement|work|changes?)',
  r'\bwith (a )?(little|bit of|some|slight) improvement',
  r'\b(lacks?|lacking|missing)\b',
  r'\bnot (enough|sufficient|long[- ]lasting|strong enough|good enough)\b',
  r'\btoo (oily|greasy|dry|sweet|salty|strong|weak|small|thin|expensive|costly|hard|harsh|watery|runny|bitter)\b',
  r'\b(wish|would like|would prefer|hope) (it|they|the)\b',
  r'\b(drawback|downside|shortcoming|weak point)s?\b',
].map((p) => RegExp(p, caseSensitive: false)).toList();

// Compliments phrased with "not": "does not feel too dry", "no residue".
final List<RegExp> _positiveNegation = [
  r"\b(does ?n[o']t|doesn't|do ?n[o']t|not|never) (feel |feels |make (it |hair |skin |the hair |my hair |the skin |my skin )?(feel )?|leave (any )?|get )?(too |very |so |overly )?(dry|greasy|oily|sticky|harsh|heavy|bitter|itchy|rough|frizzy)",
  r'\bno (residue|stickiness|irritation|side effects?|smell|aftertaste|mess|leak(age)?)\b',
].map((p) => RegExp(p, caseSensitive: false)).toList();

// Filler lines like "No specific issues or suggestions provided" or "None".
final _placeholder = RegExp(
  r'^\s*(none|n\/?a|nil|-+|nothing( (specific|notable|in particular|significant))?( (mentioned|provided|noted|reported))?|no (specific |major |particular |other |notable |clear |significant )?(issues?|problems?|complaints?|concerns?|suggestions?|feedback|praise|positives?|negatives?|actions?|recommendations?)\b.*)\.?\s*$',
  caseSensitive: false,
);
bool isPlaceholder(String line) => _placeholder.hasMatch(line);

// "no issues / no problems" says nothing negative.
final _noIssue = RegExp(
  r'\b(no|not any|without any|zero) (specific |major |real |particular |other )?(issues?|problems?|complaints?|concerns?)\b',
  caseSensitive: false,
);

/// Keyword tone of one sentence. Criticism wins when both match.
ReviewTone classifyText(String text) {
  var cleaned = text.replaceAll(_noIssue, ' ');
  for (final r in _positiveNegation) {
    cleaned = cleaned.replaceAll(r, ' ');
  }
  final praisedByNegation = cleaned != text;
  if (_negative.any((r) => r.hasMatch(cleaned)) || _improvement.any((r) => r.hasMatch(cleaned))) {
    return ReviewTone.negative;
  }
  if (praisedByNegation || _positive.any((r) => r.hasMatch(cleaned))) return ReviewTone.positive;
  return ReviewTone.neutral;
}

class SummarySection {
  SummarySection(this.heading);

  final String? heading;
  final List<String> paragraphs = [];
  final List<String> items = [];

  bool get isEmpty => paragraphs.isEmpty && items.isEmpty;
}

List<SummarySection> parseSummarySections(String summary) {
  final sections = <SummarySection>[];
  var current = SummarySection(null);
  for (final l in parseSummaryLines(summary)) {
    switch (l.kind) {
      case SummaryLineKind.heading:
        if (current.heading != null || !current.isEmpty) sections.add(current);
        current = SummarySection(l.text);
      case SummaryLineKind.bullet:
        current.items.add(l.text);
      case SummaryLineKind.text:
        current.paragraphs.add(l.text);
    }
  }
  if (current.heading != null || !current.isEmpty) sections.add(current);
  return sections;
}

class ToneCounts {
  const ToneCounts(this.positive, this.neutral, this.negative);

  final int positive;
  final int neutral;
  final int negative;

  int get total => positive + neutral + negative;
}

class ReviewInsights {
  final List<String> overview = [];
  final List<String> positives = [];
  final List<String> issues = [];
  final List<String> actions = [];
  final List<String> notes = [];
  final List<SummarySection> other = [];

  /// True when liked/issues came from the per-part customer feedback.
  bool derived = false;

  ToneCounts counts = const ToneCounts(0, 0, 0);

  bool get hasLists =>
      positives.isNotEmpty || issues.isNotEmpty || actions.isNotEmpty || notes.isNotEmpty || other.isNotEmpty;
}

final _overviewHeading = RegExp(r'sentiment|overview|verdict|summary|conclusion', caseSensitive: false);
final _actionHeading = RegExp(r'action|recommend|next step|suggest', caseSensitive: false);
final _issueHeading = RegExp(
  r'issue|problem|concern|complaint|negative|pain|improve|weak|drawback|dislike|\bcons\b|challenge',
  caseSensitive: false,
);
final _positiveHeading = RegExp(
  r'positive|strength|liked|praise|highlight|\bpros\b|what worked|appreciat',
  caseSensitive: false,
);
final _bareSentiment = RegExp(
  r'^(praise|positive|negative|neutral|mixed|issue|suggestion|positive, with concerns)\.?$',
  caseSensitive: false,
);
final _feedbackHeading = RegExp(r'feedback|finding|insight|point|theme|observation', caseSensitive: false);

ReviewInsights buildInsights(FeedbackRecord record) {
  final out = ReviewInsights();

  void sortItem(String line) {
    switch (classifyText(line)) {
      case ReviewTone.negative:
        out.issues.add(line);
      case ReviewTone.positive:
        out.positives.add(line);
      default:
        out.notes.add(line);
    }
  }

  for (final sec in parseSummarySections(record.summary)) {
    final h = sec.heading ?? '';
    final lines = [...sec.paragraphs, ...sec.items];
    if (sec.heading == null || _overviewHeading.hasMatch(h)) {
      // Skip a bare sentiment label such as "Praise" on its own line.
      out.overview.addAll(sec.paragraphs.where((p) => !_bareSentiment.hasMatch(p.trim())));
      sec.items.forEach(sortItem);
    } else if (_actionHeading.hasMatch(h)) {
      out.actions.addAll(lines);
    } else if (_issueHeading.hasMatch(h)) {
      out.issues.addAll(lines);
    } else if (_positiveHeading.hasMatch(h)) {
      out.positives.addAll(lines);
    } else if (_feedbackHeading.hasMatch(h)) {
      lines.forEach(sortItem);
    } else {
      out.other.add(sec);
    }
  }

  // Qwen's per-part analysis rates each feedback area. Use it to fill any list
  // the summary left empty (for example, a summary with praise but no issues).
  final feedback = record.chapters.expand((c) => c.feedback).toList();
  if (feedback.isNotEmpty) {
    final fillPositives = out.positives.isEmpty;
    final fillIssues = out.issues.isEmpty;
    final fillNotes = fillPositives && fillIssues;
    final seen = <String>{};
    for (final f in feedback) {
      if (f.name.isEmpty) continue;
      final text = f.evidence.isNotEmpty ? '${f.name}: ${f.evidence}' : f.name;
      final tone = toneFromLabel(f.sentiment).tone;
      final r = f.rating;
      final kind = (tone == ReviewTone.negative || (r != null && r < 3))
          ? 'issue'
          : (tone == ReviewTone.positive || (r != null && r >= 4))
              ? 'positive'
              : 'note';
      if (!seen.add('$kind:${f.name.toLowerCase()}')) continue;
      if (kind == 'issue' && fillIssues) {
        out.issues.add(text);
      } else if (kind == 'positive' && fillPositives) {
        out.positives.add(text);
      } else if (kind == 'note' && fillNotes) {
        out.notes.add(text);
      } else {
        continue;
      }
      out.derived = true;
    }
  }

  // Drop filler lines so "No specific issues provided" isn't shown as an issue.
  for (final list in [out.positives, out.issues, out.actions, out.notes]) {
    list.removeWhere(isPlaceholder);
  }

  // Praise / neutral / issue sentences in the transcript, for the bar.
  final sentences = record.transcript
      .replaceAll(RegExp(r'\s*\n\s*'), ' ')
      .split(RegExp(r'(?<=[.!?])\s+'))
      .map((s) => s.trim())
      .where((s) => s.isNotEmpty);
  var pos = 0, neu = 0, neg = 0;
  for (final s in sentences) {
    switch (classifyText(s)) {
      case ReviewTone.positive:
        pos++;
      case ReviewTone.negative:
        neg++;
      default:
        neu++;
    }
  }
  out.counts = ToneCounts(pos, neu, neg);

  return out;
}