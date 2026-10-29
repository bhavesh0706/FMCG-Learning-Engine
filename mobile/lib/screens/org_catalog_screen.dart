import 'package:flutter/material.dart';

import '../main.dart';
import '../services/org_api.dart';
import 'org_product_screens.dart';
import 'org_screens.dart';

/* ============================================================
   Catalog (organization): products and categories
   ============================================================
   The organization adds, changes, deactivates and reactivates its products
   and categories. The server allows these changes only for the organization
   and notifies every admin of each one (with what changed).
*/

class _CatalogData {
  _CatalogData(this.categories, this.products);

  final List<Json> categories;
  final List<Json> products;
}

class OrgCatalogPage extends StatefulWidget {
  const OrgCatalogPage({super.key});

  @override
  State<OrgCatalogPage> createState() => _OrgCatalogPageState();
}

class _OrgCatalogPageState extends State<OrgCatalogPage> {
  int? _category; // null = all
  bool _showInactive = false;
  String _query = '';

  Future<_CatalogData> _load() async {
    final results = await Future.wait([OrgApi.categories(), OrgApi.catalogProducts()]);
    return _CatalogData(results[0], results[1]);
  }

  @override
  Widget build(BuildContext context) {
    return Loaded<_CatalogData>(
      load: _load,
      builder: (context, data, reload) {
        final cats = data.categories.where((c) => _showInactive || c['is_active'] != false).toList();
        final q = _query.toLowerCase();
        final products = data.products
            .where((p) => _showInactive || p['is_active'] != false)
            .where((p) => _category == null || intOf(p['category_id']) == _category)
            .where((p) => q.isEmpty || '${p['name']} ${p['sku']}'.toLowerCase().contains(q))
            .toList();
        final selected = data.categories.where((c) => intOf(c['id']) == _category).firstOrNull;

        Future<void> addCategory() async {
          final ok = await _categorySheet(context);
          if (ok == true) {
            await reload();
            if (context.mounted) showMessage(context, 'Category added.');
          }
        }

        Future<void> addProduct() async {
          final active = data.categories.where((c) => c['is_active'] != false).toList();
          if (active.isEmpty) {
            showMessage(context, 'Add a category first.');
            return;
          }
          final ok = await showModalBottomSheet<bool>(
            context: context,
            isScrollControlled: true,
            useSafeArea: true,
            builder: (_) => ProductEditorSheet(categories: active, categoryId: _category),
          );
          if (ok == true) {
            await reload();
            if (context.mounted) showMessage(context, 'Product added. Add its survey questions and recipe in Products.');
          }
        }

        return ListView(
          padding: const EdgeInsets.fromLTRB(16, 8, 16, 28),
          children: [
            Text('Your products and categories. The research team is notified of every change.',
                style: TextStyle(color: AppColors.muted, height: 1.4)),
            const SizedBox(height: 12),
            Row(children: [
              Expanded(child: OutlinedButton.icon(onPressed: addCategory, icon: const Icon(Icons.create_new_folder_outlined), label: const Text('Add category'))),
              const SizedBox(width: 8),
              Expanded(child: FilledButton.icon(onPressed: addProduct, icon: const Icon(Icons.add), label: const Text('Add product'))),
            ]),
            const SizedBox(height: 12),
            SingleChildScrollView(
              scrollDirection: Axis.horizontal,
              child: Row(children: [
                Padding(
                  padding: const EdgeInsets.only(right: 6),
                  child: ChoiceChip(
                    label: Text('All · ${data.products.where((p) => _showInactive || p['is_active'] != false).length}'),
                    selected: _category == null,
                    onSelected: (_) => setState(() => _category = null),
                  ),
                ),
                for (final c in cats)
                  Padding(
                    padding: const EdgeInsets.only(right: 6),
                    child: ChoiceChip(
                      label: Text('${str(c['name'])}${c['is_active'] == false ? ' (inactive)' : ''} · ${intOf(c['product_count'])}'),
                      selected: _category == intOf(c['id']),
                      onSelected: (_) => setState(() => _category = intOf(c['id'])),
                    ),
                  ),
              ]),
            ),
            SwitchListTile(
              contentPadding: EdgeInsets.zero,
              dense: true,
              value: _showInactive,
              onChanged: (v) => setState(() => _showInactive = v),
              title: const Text('Show inactive'),
            ),
            if (selected != null) _CategoryHeader(category: selected, onChanged: reload),
            TextField(
              decoration: const InputDecoration(prefixIcon: Icon(Icons.search), hintText: 'Search products or SKUs'),
              onChanged: (v) => setState(() => _query = v),
            ),
            const SizedBox(height: 10),
            if (products.isEmpty)
              EmptyNote(
                icon: Icons.inventory_2_outlined,
                title: q.isEmpty ? 'No products here yet' : 'No matches',
                text: q.isEmpty ? 'Add a product to start collecting feedback on it.' : 'Try a different name or SKU.',
              ),
            for (final p in products) ...[
              _ProductTile(product: p, categories: data.categories, onChanged: reload),
              const SizedBox(height: 8),
            ],
          ],
        );
      },
    );
  }
}

class _ProductTile extends StatelessWidget {
  const _ProductTile({required this.product, required this.categories, required this.onChanged});

  final Json product;
  final List<Json> categories;
  final Future<void> Function() onChanged;

  @override
  Widget build(BuildContext context) {
    final p = product;
    final inactive = p['is_active'] == false;
    final questions = intOf(p['question_count']);
    final (tone, status) = inactive
        ? ('muted', 'Inactive')
        : p['survey_ready'] == true
            ? ('ok', 'Ready for surveys')
            : ('warn', questions < 5 ? 'Needs ${5 - questions} more questions' : 'Not ready');
    final versions = (p['recipe_versions'] is List ? (p['recipe_versions'] as List).length : 0);
    return Panel(
      onTap: () async {
        final changed = await showModalBottomSheet<bool>(
          context: context,
          isScrollControlled: true,
          useSafeArea: true,
          builder: (_) => ProductEditorSheet(categories: categories, product: p),
        );
        if (changed == true) await onChanged();
      },
      child: Row(children: [
        Expanded(
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Text(str(p['name']),
                style: TextStyle(fontWeight: FontWeight.w700, color: inactive ? AppColors.muted : AppColors.ink)),
            const SizedBox(height: 3),
            Text(
              [str(p['category_name']), if (str(p['sku']).isNotEmpty) str(p['sku'])].join(' · '),
              style: TextStyle(fontSize: 12, color: AppColors.muted),
            ),
            const SizedBox(height: 6),
            Wrap(spacing: 6, runSpacing: 4, children: [
              Pill(status, tone: tone),
              Pill('$questions questions', tone: 'muted'),
              if (versions > 0) Pill(versions == 1 ? '1 version' : '$versions versions', tone: 'muted'),
            ]),
          ]),
        ),
        Icon(Icons.chevron_right, color: AppColors.muted),
      ]),
    );
  }
}

class _CategoryHeader extends StatelessWidget {
  const _CategoryHeader({required this.category, required this.onChanged});

  final Json category;
  final Future<void> Function() onChanged;

  @override
  Widget build(BuildContext context) {
    final inactive = category['is_active'] == false;
    final id = intOf(category['id']);
    Future<void> act(String a) async {
      try {
        if (a == 'rename') {
          final ok = await _categorySheet(context, category: category);
          if (ok == true) {
            await onChanged();
            if (context.mounted) showMessage(context, 'Category saved.');
          }
        } else if (a == 'deactivate') {
          final ok = await confirmAction(
            context,
            title: 'Deactivate ${str(category['name'])}?',
            message: 'Its products stay, but the category is hidden from new surveys. You can reactivate it later.',
            confirmLabel: 'Deactivate',
            cancelLabel: 'Keep',
          );
          if (!ok) return;
          await OrgApi.deactivateCategory(id);
          await onChanged();
          if (context.mounted) showMessage(context, 'Category deactivated.');
        } else if (a == 'reactivate') {
          await OrgApi.updateCategory(id, {'is_active': true});
          await onChanged();
          if (context.mounted) showMessage(context, 'Category reactivated.');
        }
      } catch (e) {
        if (context.mounted) showMessage(context, '$e', error: true);
      }
    }

    return Padding(
      padding: const EdgeInsets.only(bottom: 10),
      child: Panel(
        padding: const EdgeInsets.fromLTRB(14, 6, 4, 6),
        child: Row(children: [
          Expanded(
            child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Text(str(category['name']), style: TextStyle(fontWeight: FontWeight.w800, color: AppColors.ink)),
              if (str(category['description']).isNotEmpty)
                Text(str(category['description']), style: TextStyle(fontSize: 12, color: AppColors.muted)),
            ]),
          ),
          PopupMenuButton<String>(
            tooltip: 'Category actions',
            onSelected: act,
            itemBuilder: (_) => [
              const PopupMenuItem(value: 'rename', child: Text('Rename')),
              PopupMenuItem(value: inactive ? 'reactivate' : 'deactivate', child: Text(inactive ? 'Reactivate' : 'Deactivate')),
            ],
          ),
        ]),
      ),
    );
  }
}

Future<bool?> _categorySheet(BuildContext context, {Json? category}) {
  return showModalBottomSheet<bool>(
    context: context,
    isScrollControlled: true,
    useSafeArea: true,
    builder: (_) => _CategorySheet(category: category),
  );
}

class _CategorySheet extends StatefulWidget {
  const _CategorySheet({this.category});

  final Json? category;

  @override
  State<_CategorySheet> createState() => _CategorySheetState();
}

class _CategorySheetState extends State<_CategorySheet> {
  late final TextEditingController _name = TextEditingController(text: str(widget.category?['name']));
  late final TextEditingController _description = TextEditingController(text: str(widget.category?['description']));
  bool _saving = false;
  String? _error;

  @override
  void dispose() {
    _name.dispose();
    _description.dispose();
    super.dispose();
  }

  Future<void> _save() async {
    if (_name.text.trim().length < 2) {
      setState(() => _error = 'Enter a category name.');
      return;
    }
    setState(() {
      _saving = true;
      _error = null;
    });
    try {
      if (widget.category == null) {
        await OrgApi.createCategory(_name.text.trim(), _description.text.trim());
      } else {
        await OrgApi.updateCategory(intOf(widget.category!['id']), {
          'name': _name.text.trim(),
          'description': _description.text.trim().isEmpty ? null : _description.text.trim(),
        });
      }
      if (mounted) Navigator.of(context).pop(true);
    } catch (e) {
      if (mounted) setState(() {
        _saving = false;
        _error = '$e';
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: EdgeInsets.fromLTRB(20, 8, 20, 16 + MediaQuery.of(context).viewInsets.bottom),
      child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, mainAxisSize: MainAxisSize.min, children: [
        Text(widget.category == null ? 'Add a category' : 'Rename category',
            style: TextStyle(fontSize: 18, fontWeight: FontWeight.w800, color: AppColors.ink)),
        const SizedBox(height: 14),
        TextField(controller: _name, decoration: const InputDecoration(labelText: 'Name', hintText: 'Breakfast Cereals')),
        const SizedBox(height: 12),
        TextField(controller: _description, decoration: const InputDecoration(labelText: 'Description (optional)')),
        if (_error != null) Padding(padding: const EdgeInsets.only(top: 8), child: Text(_error!, style: TextStyle(color: AppColors.neg))),
        const SizedBox(height: 14),
        FilledButton(onPressed: _saving ? null : _save, child: Text(_saving ? 'Saving…' : (widget.category == null ? 'Add category' : 'Save'))),
        TextButton(onPressed: () => Navigator.of(context).pop(false), child: const Text('Cancel')),
      ]),
    );
  }
}

/// Add a product, or change / deactivate / reactivate one.
class ProductEditorSheet extends StatefulWidget {
  const ProductEditorSheet({super.key, required this.categories, this.product, this.categoryId});

  final List<Json> categories;
  final Json? product;
  final int? categoryId;

  @override
  State<ProductEditorSheet> createState() => _ProductEditorSheetState();
}

class _ProductEditorSheetState extends State<ProductEditorSheet> {
  late final TextEditingController _name = TextEditingController(text: str(widget.product?['name']));
  late final TextEditingController _sku = TextEditingController(text: str(widget.product?['sku']));
  late final TextEditingController _description = TextEditingController(text: str(widget.product?['description']));
  late int? _categoryId = widget.product != null
      ? intOf(widget.product!['category_id'])
      : (widget.categoryId ?? (widget.categories.isEmpty ? null : intOf(widget.categories.first['id'])));
  bool _saving = false;
  String? _error;

  bool get _isNew => widget.product == null;
  bool get _inactive => widget.product?['is_active'] == false;

  @override
  void dispose() {
    _name.dispose();
    _sku.dispose();
    _description.dispose();
    super.dispose();
  }

  Future<void> _run(Future<void> Function() action) async {
    setState(() {
      _saving = true;
      _error = null;
    });
    try {
      await action();
      if (mounted) Navigator.of(context).pop(true);
    } catch (e) {
      if (mounted) setState(() {
        _saving = false;
        _error = '$e';
      });
    }
  }

  Future<void> _save() async {
    if (_name.text.trim().length < 2 || _categoryId == null) {
      setState(() => _error = 'Enter the product name and choose its category.');
      return;
    }
    final body = <String, dynamic>{
      'name': _name.text.trim(),
      'category_id': _categoryId,
      'sku': _sku.text.trim().isEmpty ? null : _sku.text.trim(),
      'description': _description.text.trim().isEmpty ? null : _description.text.trim(),
    };
    await _run(() async {
      if (_isNew) {
        await OrgApi.createProduct(body);
      } else {
        await OrgApi.updateProduct(intOf(widget.product!['id']), body);
      }
      if (mounted) showMessage(context, _isNew ? 'Product added.' : 'Product saved.');
    });
  }

  Future<void> _toggleActive() async {
    final id = intOf(widget.product!['id']);
    if (!_inactive) {
      final ok = await confirmAction(
        context,
        title: 'Deactivate ${str(widget.product!['name'])}?',
        message: 'Survey persons can no longer survey it. Its surveys and reports are kept, and you can reactivate it later.',
        confirmLabel: 'Deactivate',
        cancelLabel: 'Keep',
      );
      if (!ok) return;
    }
    await _run(() async {
      if (_inactive) {
        await OrgApi.updateProduct(id, {'is_active': true});
      } else {
        await OrgApi.deactivateProduct(id);
      }
      if (mounted) showMessage(context, _inactive ? 'Product reactivated.' : 'Product deactivated.');
    });
  }

  @override
  Widget build(BuildContext context) {
    final cats = widget.categories;
    final hasSelected = cats.any((c) => intOf(c['id']) == _categoryId);
    return Padding(
      padding: EdgeInsets.fromLTRB(20, 8, 20, 16 + MediaQuery.of(context).viewInsets.bottom),
      child: SingleChildScrollView(
        child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, mainAxisSize: MainAxisSize.min, children: [
          Text(_isNew ? 'Add a product' : 'Product details', style: TextStyle(fontSize: 18, fontWeight: FontWeight.w800, color: AppColors.ink)),
          if (_isNew)
            Padding(
              padding: const EdgeInsets.only(top: 4),
              child: Text('Add its survey questions and recipe afterwards in Products.', style: TextStyle(color: AppColors.muted)),
            ),
          const SizedBox(height: 14),
          DropdownButtonFormField<int>(
                isExpanded: true,
            value: hasSelected ? _categoryId : null,
            decoration: const InputDecoration(labelText: 'Category'),
            items: [for (final c in cats) DropdownMenuItem(value: intOf(c['id']), child: Text(str(c['name'])))],
            onChanged: (v) => setState(() => _categoryId = v),
          ),
          const SizedBox(height: 12),
          TextField(controller: _name, decoration: const InputDecoration(labelText: 'Product name', hintText: 'Aam (Mango) Jelly')),
          const SizedBox(height: 12),
          TextField(controller: _sku, decoration: const InputDecoration(labelText: 'SKU (optional)', hintText: 'FOOD-AAM-JELLY')),
          const SizedBox(height: 12),
          TextField(controller: _description, minLines: 2, maxLines: 4, decoration: const InputDecoration(labelText: 'Description (optional)', hintText: 'Pack size, variant…')),
          if (_error != null) Padding(padding: const EdgeInsets.only(top: 8), child: Text(_error!, style: TextStyle(color: AppColors.neg))),
          const SizedBox(height: 14),
          FilledButton(onPressed: _saving ? null : _save, child: Text(_saving ? 'Saving…' : (_isNew ? 'Create product' : 'Save changes'))),
          if (!_isNew) ...[
            const SizedBox(height: 6),
            OutlinedButton(
              onPressed: _saving
                  ? null
                  : () {
                      // Capture the navigator first: this sheet's context is gone once it closes.
                      final navigator = Navigator.of(context);
                      navigator.pop(false);
                      navigator.push(MaterialPageRoute(
                        builder: (_) => OrgProductPage(productId: intOf(widget.product!['id']), name: str(widget.product!['name'])),
                      ));
                    },
              child: const Text('Open report, recipe and questions'),
            ),
            TextButton(
              onPressed: _saving ? null : _toggleActive,
              style: TextButton.styleFrom(foregroundColor: _inactive ? AppColors.brand : AppColors.neg),
              child: Text(_inactive ? 'Reactivate product' : 'Deactivate product'),
            ),
          ],
          TextButton(onPressed: () => Navigator.of(context).pop(false), child: const Text('Cancel')),
        ]),
      ),
    );
  }
}
