/*
  LOV (list of values) from GET /lov, the same lists as the web (frontend/src/services/lov.js):
  COUNTRY -> STATE -> CITY, each value inside a value of the previous list (parent_id).
  Admins change them in Catalog > Master lists. Downloaded with the offline survey data.
*/

class LovValue {
  const LovValue({required this.id, required this.label, this.parentId, this.aliases = const [], this.code = '', this.behavesLike});

  final int id;
  final String label;
  /// The value's code (what the app stores), e.g. "single_choice" or a type added in Master.
  final String code;
  /// Question types added in Master: the built-in type it works like (`extra.behaves_like`).
  final String? behavesLike;
  final int? parentId;

  /// Other names of the same place (e.g. "Bangalore" for Bengaluru), from `extra.aliases`.
  final List<String> aliases;
}

class Lov {
  const Lov(this._lists);

  static const empty = Lov({});

  final Map<String, List<LovValue>> _lists;

  /// GET /lov: {COUNTRY: {values: [{id, label, parent_id, extra, is_active}, ...]}, ...}
  factory Lov.parse(dynamic data) {
    final lists = <String, List<LovValue>>{};
    if (data is Map) {
      data.forEach((code, list) {
        final values = list is Map ? list['values'] : null;
        if (values is! List) return;
        lists['$code'] = [
          for (final v in values)
            if (v is Map && v['is_active'] != false && v['id'] is num)
              LovValue(
                id: (v['id'] as num).toInt(),
                label: '${v['label'] ?? ''}'.trim(),
                code: '${v['code'] ?? ''}',
                behavesLike: v['extra'] is Map && v['extra']['behaves_like'] is String ? v['extra']['behaves_like'] as String : null,
                parentId: v['parent_id'] is num ? (v['parent_id'] as num).toInt() : null,
                aliases: [
                  for (final a in ((v['extra'] is Map ? v['extra']['aliases'] : null) as List? ?? const [])) '$a',
                ],
              ),
        ];
      });
    }
    return Lov(lists);
  }

  List<LovValue> values(String masterCode) => _lists[masterCode] ?? const [];

  /// Values of a list inside another list (STATE of a country, CITY of a state).
  List<LovValue> children(String masterCode, int? parentId) =>
      parentId == null ? const [] : values(masterCode).where((v) => v.parentId == parentId).toList();

  static String _norm(String? s) => (s ?? '').toLowerCase().replaceAll(RegExp(r'[^a-z0-9]+'), ' ').trim();

  /// The value whose label (or alias) matches a place name ("Pune City" matches "Pune"); else null.
  static LovValue? match(List<LovValue> values, String? name) {
    final want = _norm(name);
    if (want.isEmpty) return null;
    List<String> names(LovValue v) => [v.label, ...v.aliases].map(_norm).toList();
    for (final v in values) {
      if (names(v).contains(want)) return v;
    }
    for (final v in values) {
      if (names(v).any((n) => n.isNotEmpty && (want.startsWith('$n ') || want.endsWith(' $n')))) return v;
    }
    return null;
  }
}
