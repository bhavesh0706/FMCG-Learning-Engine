import 'package:flutter/material.dart';

import '../main.dart';

/*
  SearchDropdownField: every dropdown in the app, with a search box (same as the web app's
  components/Select.jsx). A drop-in for DropdownButtonFormField: the same value /
  initialValue, items (DropdownMenuItem), decoration, hint and onChanged. Tapping it opens a
  sheet with a search field and the list; the chosen item shows in the field.
*/
class SearchDropdownField<T> extends StatefulWidget {
  const SearchDropdownField({
    super.key,
    required this.items,
    required this.onChanged,
    this.value,
    this.initialValue,
    this.decoration,
    this.hint,
    this.isExpanded = true,
    this.menuMaxHeight,
  });

  final List<DropdownMenuItem<T>> items;
  final ValueChanged<T?>? onChanged;

  /// Controlled value (the field always shows it).
  final T? value;

  /// Starting value; afterwards the field keeps the chosen one.
  final T? initialValue;
  final InputDecoration? decoration;
  final Widget? hint;
  final bool isExpanded; // kept for DropdownButtonFormField compatibility
  final double? menuMaxHeight; // kept for DropdownButtonFormField compatibility

  @override
  State<SearchDropdownField<T>> createState() => _SearchDropdownFieldState<T>();
}

/// The text of a menu item (for searching): its Text, or the first Text inside it.
String dropdownItemText(Widget? w) {
  if (w == null) return '';
  if (w is Text) return w.data ?? w.textSpan?.toPlainText() ?? '';
  if (w is RichText) return w.text.toPlainText();
  if (w is Row) return w.children.map(dropdownItemText).where((s) => s.isNotEmpty).join(' ');
  if (w is Column) return w.children.map(dropdownItemText).where((s) => s.isNotEmpty).join(' ');
  if (w is Flex) return w.children.map(dropdownItemText).where((s) => s.isNotEmpty).join(' ');
  if (w is SingleChildRenderObjectWidget) return dropdownItemText(w.child);
  if (w is ProxyWidget) return dropdownItemText(w.child);
  return '';
}

class _SearchDropdownFieldState<T> extends State<SearchDropdownField<T>> {
  late T? _current = widget.value ?? widget.initialValue;

  @override
  void didUpdateWidget(covariant SearchDropdownField<T> old) {
    super.didUpdateWidget(old);
    if (widget.value != old.value) _current = widget.value;
  }

  DropdownMenuItem<T>? get _selected {
    for (final item in widget.items) {
      if (item.value == _current) return item;
    }
    return null;
  }

  Future<void> _open() async {
    if (widget.onChanged == null) return;
    FocusScope.of(context).unfocus();
    final label = widget.decoration?.labelText ?? 'Choose';
    final picked = await showModalBottomSheet<_Pick<T>>(
      context: context,
      isScrollControlled: true,
      useSafeArea: true,
      showDragHandle: true,
      backgroundColor: AppColors.surface,
      builder: (_) => _SearchSheet<T>(title: label, items: widget.items, current: _current),
    );
    if (picked == null || !mounted) return;
    setState(() => _current = picked.value);
    widget.onChanged?.call(picked.value);
  }

  @override
  Widget build(BuildContext context) {
    final selected = _selected;
    final enabled = widget.onChanged != null;
    final decoration = (widget.decoration ?? const InputDecoration()).applyDefaults(Theme.of(context).inputDecorationTheme).copyWith(
          suffixIcon: const Icon(Icons.arrow_drop_down_rounded),
          enabled: enabled,
        );
    return Semantics(
      button: true,
      label: widget.decoration?.labelText,
      value: selected == null ? null : dropdownItemText(selected.child),
      child: InkWell(
        onTap: enabled ? _open : null,
        borderRadius: BorderRadius.circular(10),
        child: InputDecorator(
          decoration: decoration,
          isEmpty: selected == null && widget.hint == null,
          child: selected != null
              ? DefaultTextStyle.merge(maxLines: 1, overflow: TextOverflow.ellipsis, child: selected.child)
              : widget.hint != null
                  ? DefaultTextStyle.merge(style: TextStyle(color: AppColors.muted), child: widget.hint!)
                  : const SizedBox.shrink(),
        ),
      ),
    );
  }
}

class _Pick<T> {
  const _Pick(this.value);
  final T? value;
}

class _SearchSheet<T> extends StatefulWidget {
  const _SearchSheet({required this.title, required this.items, required this.current});

  final String title;
  final List<DropdownMenuItem<T>> items;
  final T? current;

  @override
  State<_SearchSheet<T>> createState() => _SearchSheetState<T>();
}

class _SearchSheetState<T> extends State<_SearchSheet<T>> {
  String _query = '';

  @override
  Widget build(BuildContext context) {
    final q = _query.trim().toLowerCase();
    final shown = [
      for (final item in widget.items)
        if (q.isEmpty || dropdownItemText(item.child).toLowerCase().contains(q)) item,
    ];
    final height = MediaQuery.sizeOf(context).height;
    return Padding(
      padding: EdgeInsets.only(bottom: MediaQuery.viewInsetsOf(context).bottom),
      child: ConstrainedBox(
        constraints: BoxConstraints(maxHeight: height * 0.75),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Padding(
              padding: const EdgeInsets.fromLTRB(20, 0, 20, 10),
              child: Text(widget.title,
                  style: TextStyle(fontFamily: kDisplayFont, fontSize: 18, fontWeight: FontWeight.w800, color: AppColors.ink)),
            ),
            Padding(
              padding: const EdgeInsets.symmetric(horizontal: 16),
              child: TextField(
                autofocus: widget.items.length > 8, // short lists: no keyboard covering them
                textInputAction: TextInputAction.search,
                decoration: const InputDecoration(prefixIcon: Icon(Icons.search_rounded), hintText: 'Search…', isDense: true),
                onChanged: (v) => setState(() => _query = v),
              ),
            ),
            const SizedBox(height: 6),
            Flexible(
              child: shown.isEmpty
                  ? Padding(
                      padding: const EdgeInsets.all(24),
                      child: Text('No match for “$_query”', textAlign: TextAlign.center, style: TextStyle(color: AppColors.muted)),
                    )
                  : ListView.builder(
                      shrinkWrap: true,
                      padding: const EdgeInsets.fromLTRB(8, 4, 8, 16),
                      itemCount: shown.length,
                      itemBuilder: (context, i) {
                        final item = shown[i];
                        final on = item.value == widget.current;
                        return ListTile(
                          enabled: item.enabled,
                          selected: on,
                          selectedColor: AppColors.brand,
                          selectedTileColor: AppColors.brandSoft,
                          shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(10)),
                          title: DefaultTextStyle.merge(
                            style: TextStyle(fontWeight: on ? FontWeight.w700 : FontWeight.w500),
                            child: item.child,
                          ),
                          trailing: on ? Icon(Icons.check_rounded, color: AppColors.brand) : null,
                          onTap: () {
                            item.onTap?.call();
                            Navigator.of(context).pop(_Pick<T>(item.value));
                          },
                        );
                      },
                    ),
            ),
          ],
        ),
      ),
    );
  }
}
