import 'package:flutter/material.dart';

import '../main.dart';
import '../services/api_service.dart';
import '../services/notification_service.dart';
import '../services/session.dart';
import 'home_screens.dart';

/* ================================================================
   Bell button (app bar) and the notifications screen
   ================================================================ */

/// Bell with the unread count. Opens the notifications screen.
class NotificationBellButton extends StatelessWidget {
  const NotificationBellButton({super.key});

  @override
  Widget build(BuildContext context) {
    return ValueListenableBuilder<int>(
      valueListenable: Notifications.unread,
      builder: (context, count, _) {
        return IconButton(
          tooltip: count > 0 ? 'Notifications, $count unread' : 'Notifications',
          onPressed: () => Navigator.of(context).push(
            MaterialPageRoute(builder: (_) => const NotificationsPage()),
          ),
          icon: Badge(
            isLabelVisible: count > 0,
            backgroundColor: AppColors.record,
            textColor: Colors.white,
            label: Text(count > 99 ? '99+' : '$count'),
            child: const Icon(Icons.notifications_outlined),
          ),
        );
      },
    );
  }
}

class NotificationsPage extends StatefulWidget {
  const NotificationsPage({super.key});

  @override
  State<NotificationsPage> createState() => _NotificationsPageState();
}

class _NotificationsPageState extends State<NotificationsPage> {
  bool _loading = true;
  String? _error;

  @override
  void initState() {
    super.initState();
    _reload();
  }

  Future<void> _reload() async {
    setState(() {
      _loading = Notifications.items.value.isEmpty;
      _error = null;
    });
    try {
      await Notifications.load();
    } on ApiException catch (e) {
      _error = e.message;
    }
    if (mounted) setState(() => _loading = false);
  }

  void _open(AppNotification n) {
    Notifications.markRead(n);
    showModalBottomSheet<void>(
      context: context,
      showDragHandle: true,
      isScrollControlled: true,
      backgroundColor: AppColors.surface,
      builder: (sheetContext) => _NotificationDetails(
        notification: n,
        onStartSurvey: n.kind == AppNotification.productAssigned && !Session.isAdmin
            ? () {
                Navigator.of(sheetContext).pop();
                openSurvey(context);
              }
            : null,
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: AppColors.bg,
      appBar: AppBar(
        title: const Text('Notifications'),
        actions: [
          ValueListenableBuilder<int>(
            valueListenable: Notifications.unread,
            builder: (context, count, _) => count == 0
                ? const SizedBox.shrink()
                : TextButton(
                    onPressed: Notifications.markAllRead,
                    child: const Text('Mark all read'),
                  ),
          ),
        ],
      ),
      body: RefreshIndicator(
        onRefresh: _reload,
        child: ValueListenableBuilder<List<AppNotification>>(
          valueListenable: Notifications.items,
          builder: (context, items, _) {
            if (_loading) {
              return const Center(child: CircularProgressIndicator());
            }
            if (items.isEmpty) {
              return ListView(
                physics: const AlwaysScrollableScrollPhysics(),
                padding: const EdgeInsets.fromLTRB(24, 80, 24, 24),
                children: [
                  Icon(
                    _error != null ? Icons.cloud_off_rounded : Icons.notifications_none_rounded,
                    size: 56,
                    color: AppColors.muted,
                  ),
                  const SizedBox(height: 16),
                  Text(
                    _error ?? "You're all caught up",
                    textAlign: TextAlign.center,
                    style: TextStyle(fontSize: 17, fontWeight: FontWeight.w700, color: AppColors.ink),
                  ),
                  const SizedBox(height: 6),
                  Text(
                    _error != null
                        ? 'Pull down to try again.'
                        : 'When your admin gives you a new product to survey, it appears here.',
                    textAlign: TextAlign.center,
                    style: TextStyle(fontSize: 14.5, height: 1.4, color: AppColors.muted),
                  ),
                ],
              );
            }
            return ListView.separated(
              physics: const AlwaysScrollableScrollPhysics(),
              padding: const EdgeInsets.symmetric(vertical: 8),
              itemCount: items.length,
              separatorBuilder: (context, index) => Divider(height: 1, color: AppColors.line),
              itemBuilder: (context, i) => _NotificationTile(notification: items[i], onTap: () => _open(items[i])),
            );
          },
        ),
      ),
    );
  }
}

class _NotificationTile extends StatelessWidget {
  const _NotificationTile({required this.notification, required this.onTap});

  final AppNotification notification;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final n = notification;
    final missing = n.kind == AppNotification.missingVideo;
    final catalog = n.kind == AppNotification.catalogChanged;
    final iconBg = n.resolved ? AppColors.posSoft : (missing ? AppColors.warnSoft : AppColors.brandSoft);
    final iconFg = n.resolved ? AppColors.pos : (missing ? AppColors.warn : AppColors.brand);
    return Material(
      color: n.isRead ? AppColors.surface : AppColors.brandSoft.withValues(alpha: 0.55),
      child: InkWell(
        onTap: onTap,
        child: Padding(
          padding: const EdgeInsets.fromLTRB(16, 14, 16, 14),
          child: Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Container(
                width: 40,
                height: 40,
                decoration: BoxDecoration(color: iconBg, borderRadius: BorderRadius.circular(12)),
                child: Icon(missing ? Icons.videocam_off_outlined : (catalog ? Icons.edit_note_rounded : Icons.inventory_2_outlined), color: iconFg, size: 21),
              ),
              const SizedBox(width: 14),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Row(
                      children: [
                        Expanded(
                          child: Text(
                            n.title,
                            style: TextStyle(
                              fontSize: 15,
                              fontWeight: n.isRead ? FontWeight.w600 : FontWeight.w800,
                              color: AppColors.ink,
                            ),
                          ),
                        ),
                        if (!n.isRead)
                          Container(
                            width: 9,
                            height: 9,
                            margin: const EdgeInsets.only(left: 8),
                            decoration: BoxDecoration(color: AppColors.brand, shape: BoxShape.circle),
                          ),
                      ],
                    ),
                    const SizedBox(height: 3),
                    Text(n.message, style: TextStyle(fontSize: 14, height: 1.4, color: AppColors.ink2)),
                    const SizedBox(height: 5),
                    Text(
                      n.resolved && n.text('resolution') == 'video_received'
                          ? '${timeAgo(n.createdAt)}  ·  Video received'
                          : timeAgo(n.createdAt),
                      style: TextStyle(fontSize: 12.5, color: AppColors.muted),
                    ),
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

class _NotificationDetails extends StatelessWidget {
  const _NotificationDetails({required this.notification, this.onStartSurvey});

  final AppNotification notification;
  final VoidCallback? onStartSurvey;

  @override
  Widget build(BuildContext context) {
    final n = notification;
    final rows = <MapEntry<String, String>>[];
    void add(String label, String? value) {
      if (value != null && value.isNotEmpty) rows.add(MapEntry(label, value));
    }

    String? chip;
    Color chipBg = AppColors.brandSoft;
    Color chipFg = AppColors.brand;

    if (n.kind == AppNotification.productAssigned) {
      add('Product', n.text('product_name'));
      add('Category', n.text('category_name'));
      add('Assigned by', n.text('assigned_by'));
      add('Assigned on', formatDate(n.time('assigned_at') ?? n.createdAt));
    } else if (n.kind == AppNotification.missingVideo) {
      final Map<String, dynamic> person = n.data['survey_person'] is Map
          ? Map<String, dynamic>.from(n.data['survey_person'] as Map)
          : const <String, dynamic>{};
      final received = n.text('resolution') == 'video_received';
      chip = received ? 'Video received' : (n.resolved ? 'Survey deleted' : 'Video still missing');
      chipBg = received ? AppColors.posSoft : AppColors.warnSoft;
      chipFg = received ? AppColors.pos : AppColors.warn;
      add('Survey person', person['name']?.toString());
      add('Contact', (person['mobile'] ?? person['email'])?.toString());
      add('Product', n.text('product_name'));
      add('Category', n.text('category_name'));
      add('Survey', n.text('survey_id') == null ? null : '#${n.text('survey_id')}');
      add('Survey location', n.text('survey_location'));
      add("Customer's city", n.text('respondent_location'));
      add('Submitted', formatDate(n.time('submitted_at')));
      if (received) add('Video received', formatDate(n.time('resolved_at')));
    } else if (n.kind == AppNotification.catalogChanged) {
      // The organization changed its catalog in its portal or app.
      const actions = {
        'added': 'Added',
        'updated': 'Updated',
        'deactivated': 'Deactivated',
        'reactivated': 'Reactivated',
        'deleted': 'Permanently deleted',
      };
      final action = n.text('action') ?? 'updated';
      chip = actions[action] ?? 'Updated';
      chipBg = action == 'deleted' || action == 'deactivated' ? AppColors.warnSoft : AppColors.brandSoft;
      chipFg = action == 'deleted' || action == 'deactivated' ? AppColors.warn : AppColors.brand;
      add(n.text('entity') == 'category' ? 'Category' : 'Product', n.text('name'));
      final who = n.text('client_name');
      add('Changed by', who == null ? null : '$who (Super Admin)');
      add('Changed', formatDate(n.time('changed_at') ?? n.createdAt));
    }
    final changes = n.data['changes'] is List
        ? (n.data['changes'] as List).map((c) => '$c').where((c) => c.trim().isNotEmpty).toList()
        : const <String>[];

    return SafeArea(
      child: Padding(
        padding: const EdgeInsets.fromLTRB(20, 0, 20, 20),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(n.title, style: TextStyle(fontSize: 19, fontWeight: FontWeight.w800, color: AppColors.ink)),
            const SizedBox(height: 6),
            Text(n.message, style: TextStyle(fontSize: 15, height: 1.45, color: AppColors.ink2)),
            if (chip != null) ...[
              const SizedBox(height: 12),
              Container(
                padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
                decoration: BoxDecoration(color: chipBg, borderRadius: BorderRadius.circular(999)),
                child: Text(chip, style: TextStyle(fontSize: 12.5, fontWeight: FontWeight.w700, color: chipFg)),
              ),
            ],
            const SizedBox(height: 16),
            for (final c in changes)
              Padding(
                padding: const EdgeInsets.only(bottom: 6),
                child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
                  Padding(padding: const EdgeInsets.only(top: 6), child: Icon(Icons.circle, size: 6, color: AppColors.brand)),
                  const SizedBox(width: 8),
                  Expanded(child: Text(c, style: TextStyle(fontSize: 14.5, height: 1.4, color: AppColors.ink))),
                ]),
              ),
            if (changes.isNotEmpty) const SizedBox(height: 10),
            for (final r in rows)
              Padding(
                padding: const EdgeInsets.symmetric(vertical: 5),
                child: Row(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    SizedBox(
                      width: 128,
                      child: Text(r.key, style: TextStyle(fontSize: 14, color: AppColors.muted)),
                    ),
                    Expanded(
                      child: Text(r.value,
                          style: TextStyle(fontSize: 14, fontWeight: FontWeight.w600, color: AppColors.ink)),
                    ),
                  ],
                ),
              ),
            if (onStartSurvey != null) ...[
              const SizedBox(height: 18),
              SizedBox(
                width: double.infinity,
                child: FilledButton.icon(
                  onPressed: onStartSurvey,
                  icon: const Icon(Icons.add_rounded),
                  label: const Text('Start a survey'),
                ),
              ),
            ],
          ],
        ),
      ),
    );
  }
}