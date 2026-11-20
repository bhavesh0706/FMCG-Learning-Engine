import 'package:flutter/material.dart';

import '../main.dart';
import '../services/connectivity_service.dart';
import '../services/offline_catalog.dart';
import '../services/session.dart';
import '../services/survey_api.dart' show apiErrorText;
import '../services/survey_queue.dart';

/*
  Offline mode for survey persons: the sync dashboard, the app-bar sync button and
  the offline banner shown above the survey person's home screen.
*/

String _when(DateTime? t) {
  if (t == null) return 'never';
  final d = DateTime.now().difference(t);
  if (d.inMinutes < 1) return 'just now';
  if (d.inHours < 1) return '${d.inMinutes} min ago';
  if (d.inDays < 1) return '${d.inHours} h ago';
  return '${t.day}/${t.month}/${t.year}';
}

String _size(int bytes) {
  if (bytes >= 1024 * 1024) return '${(bytes / (1024 * 1024)).toStringAsFixed(1)} MB';
  return '${(bytes / 1024).ceil()} KB';
}

Color _statusColor(SurveyStatus s) => switch (s) {
  SurveyStatus.waiting => AppColors.warnDeep,
  SurveyStatus.uploading => AppColors.info,
  SurveyStatus.analyzing || SurveyStatus.analyzed => AppColors.pos,
  SurveyStatus.failed => AppColors.neg,
};

IconData _statusIcon(SurveyStatus s) => switch (s) {
  SurveyStatus.waiting => Icons.schedule_rounded,
  SurveyStatus.uploading => Icons.cloud_upload_rounded,
  SurveyStatus.analyzing || SurveyStatus.analyzed => Icons.cloud_done_rounded,
  SurveyStatus.failed => Icons.error_outline_rounded,
};

/// Asks for the password again so uploads can continue (offline session after a restart,
/// or the password was changed on the server).
Future<void> showSignInAgain(BuildContext context) async {
  final password = TextEditingController();
  String? error;
  var busy = false;
  await showDialog<void>(
    context: context,
    builder: (ctx) => StatefulBuilder(
      builder: (ctx, setState) => AlertDialog(
        title: const Text('Sign in to upload'),
        content: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              'Your surveys are saved on this phone. Enter your password to upload them.',
              style: TextStyle(color: AppColors.ink2, height: 1.4),
            ),
            const SizedBox(height: 12),
            TextField(
              controller: password,
              obscureText: true,
              autofocus: true,
              decoration: InputDecoration(labelText: 'Password', errorText: error),
            ),
          ],
        ),
        actions: [
          TextButton(onPressed: busy ? null : () => Navigator.pop(ctx), child: const Text('Later')),
          FilledButton(
            onPressed: busy
                ? null
                : () async {
                    setState(() {
                      busy = true;
                      error = null;
                    });
                    try {
                      await Session.reauthenticate(password.text);
                      if (ctx.mounted) Navigator.pop(ctx);
                      SurveyQueue.syncNow();
                      OfflineCatalog.refreshInBackground();
                    } catch (e) {
                      setState(() {
                        busy = false;
                        error = '$e';
                      });
                    }
                  },
            child: Text(busy ? 'Signing in…' : 'Sign in'),
          ),
        ],
      ),
    ),
  );
  password.dispose();
}

/// Cloud icon in the app bar: pending uploads at a glance; opens the sync dashboard.
class SyncStatusButton extends StatelessWidget {
  const SyncStatusButton({super.key});

  @override
  Widget build(BuildContext context) {
    return ListenableBuilder(
      listenable: Listenable.merge([SurveyQueue.items, SurveyQueue.syncing, ConnectivityService.online]),
      builder: (context, _) {
        final c = SurveyQueue.counts;
        final waiting = c.pending + c.failed + c.uploading;
        final icon = !ConnectivityService.online.value
            ? Icons.cloud_off_rounded
            : SurveyQueue.syncing.value
            ? Icons.cloud_sync_rounded
            : waiting > 0
            ? Icons.cloud_upload_outlined
            : Icons.cloud_done_outlined;
        return IconButton(
          tooltip: 'Offline surveys and sync',
          onPressed: () => Navigator.of(context).push(MaterialPageRoute(builder: (_) => const SyncScreen())),
          icon: Badge(
            isLabelVisible: waiting > 0,
            backgroundColor: c.failed > 0 ? AppColors.neg : AppColors.warnDeep,
            label: Text('$waiting'),
            child: Icon(icon),
          ),
        );
      },
    );
  }
}

/// Above the survey person's home: offline, signed in offline, or sign-in needed to upload.
class OfflineBanner extends StatelessWidget {
  const OfflineBanner({super.key});

  @override
  Widget build(BuildContext context) {
    return ListenableBuilder(
      listenable: Listenable.merge([
        ConnectivityService.online,
        Session.offlineMode,
        Session.needsOnlineSignIn,
        SurveyQueue.items,
      ]),
      builder: (context, _) {
        final pending = SurveyQueue.counts.pending + SurveyQueue.counts.failed;
        final online = ConnectivityService.online.value;
        String? text;
        IconData icon = Icons.cloud_off_rounded;
        VoidCallback? action;
        String? actionLabel;
        if (!online) {
          text =
              'Offline. Surveys are saved on this phone'
              '${pending > 0 ? ' ($pending waiting)' : ''} and upload when you are back online.';
        } else if (Session.needsOnlineSignIn.value) {
          icon = Icons.lock_outline_rounded;
          text = 'Signed in offline. Enter your password to upload ${pending > 0 ? '$pending survey(s)' : 'surveys'}.';
          action = () => showSignInAgain(context);
          actionLabel = 'Sign in';
        } else if (Session.offlineMode.value) {
          icon = Icons.cloud_sync_rounded;
          text = 'Connecting to the server…';
        }
        if (text == null) return const SizedBox.shrink();
        return Material(
          color: AppColors.warnSoft,
          child: SafeArea(
            bottom: false,
            top: false,
            child: Padding(
              padding: const EdgeInsets.fromLTRB(16, 10, 8, 10),
              child: Row(
                children: [
                  Icon(icon, color: AppColors.warnDeep, size: 20),
                  const SizedBox(width: 10),
                  Expanded(
                    child: Text(text, style: TextStyle(color: AppColors.ink, fontSize: 13.5, height: 1.35)),
                  ),
                  if (action != null) TextButton(onPressed: action, child: Text(actionLabel!)),
                ],
              ),
            ),
          ),
        );
      },
    );
  }
}

class SyncScreen extends StatefulWidget {
  const SyncScreen({super.key});

  @override
  State<SyncScreen> createState() => _SyncScreenState();
}

class _SyncScreenState extends State<SyncScreen> {
  String? _downloadMessage;
  int _storage = 0;

  @override
  void initState() {
    super.initState();
    OfflineCatalog.loadMeta();
    _loadStorage();
  }

  Future<void> _loadStorage() async {
    final bytes = await SurveyQueue.storageBytes();
    if (mounted) setState(() => _storage = bytes);
  }

  Future<void> _download() async {
    setState(() => _downloadMessage = null);
    try {
      final r = await OfflineCatalog.downloadAll();
      if (mounted) {
        setState(
          () => _downloadMessage =
              'Downloaded ${r.products} product(s) with their questions. Surveys can now be taken offline.',
        );
      }
    } catch (e) {
      if (mounted) setState(() => _downloadMessage = e is StateError ? e.message : apiErrorText(e));
    }
  }

  Future<void> _syncNow() async {
    if (Session.needsOnlineSignIn.value) {
      await showSignInAgain(context);
      return;
    }
    await SurveyQueue.syncNow();
    await _loadStorage();
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Offline surveys')),
      body: ListenableBuilder(
        listenable: Listenable.merge([
          SurveyQueue.items,
          SurveyQueue.progress,
          SurveyQueue.syncing,
          SurveyQueue.lastSyncAt,
          ConnectivityService.online,
          Session.offlineMode,
          Session.needsOnlineSignIn,
          OfflineCatalog.downloadedAt,
          OfflineCatalog.downloading,
        ]),
        builder: (context, _) {
          final items = SurveyQueue.items.value;
          final c = SurveyQueue.counts;
          final online = ConnectivityService.online.value;
          final syncing = SurveyQueue.syncing.value;
          final canDownload = online && Session.hasServerSession && !OfflineCatalog.downloading.value;
          return RefreshIndicator(
            onRefresh: _syncNow,
            child: ListView(
              padding: const EdgeInsets.fromLTRB(16, 16, 16, 32),
              children: [
                _ConnectionCard(online: online),
                const SizedBox(height: 12),
                Wrap(
                  spacing: 10,
                  runSpacing: 10,
                  children: [
                    _CountTile(label: 'Collected', value: c.total, color: AppColors.brand),
                    _CountTile(label: 'Pending upload', value: c.pending, color: AppColors.warnDeep),
                    _CountTile(label: 'Uploading', value: c.uploading, color: AppColors.info),
                    _CountTile(label: 'Synced', value: c.synced, color: AppColors.pos),
                    _CountTile(label: 'Upload failed', value: c.failed, color: AppColors.neg),
                  ],
                ),
                const SizedBox(height: 14),
                FilledButton.icon(
                  onPressed: syncing ? null : _syncNow,
                  icon: syncing
                      ? const SizedBox(width: 18, height: 18, child: CircularProgressIndicator(strokeWidth: 2))
                      : const Icon(Icons.sync_rounded),
                  label: Text(syncing ? 'Syncing…' : 'Sync now'),
                ),
                const SizedBox(height: 6),
                Text(
                  'Last sync: ${_when(SurveyQueue.lastSyncAt.value)}'
                  '${_storage > 0 ? ' · ${_size(_storage)} of recordings waiting on this phone' : ''}',
                  textAlign: TextAlign.center,
                  style: TextStyle(color: AppColors.muted, fontSize: 12.5),
                ),
                const SizedBox(height: 20),
                Text(
                  'Survey data for offline use',
                  style: TextStyle(fontSize: 16, fontWeight: FontWeight.w800, color: AppColors.ink),
                ),
                const SizedBox(height: 6),
                Text(
                  'Downloaded ${_when(OfflineCatalog.downloadedAt.value)}. Your assigned products, their questions, '
                  'master lists and action plans are kept on this phone so you can survey without internet.',
                  style: TextStyle(color: AppColors.ink2, height: 1.4),
                ),
                const SizedBox(height: 8),
                OutlinedButton.icon(
                  onPressed: canDownload ? _download : null,
                  icon: OfflineCatalog.downloading.value
                      ? const SizedBox(width: 18, height: 18, child: CircularProgressIndicator(strokeWidth: 2))
                      : const Icon(Icons.download_rounded),
                  label: Text(OfflineCatalog.downloading.value ? 'Downloading…' : 'Download for offline use'),
                ),
                if (_downloadMessage != null) ...[
                  const SizedBox(height: 6),
                  Text(_downloadMessage!, style: TextStyle(color: AppColors.ink2, fontSize: 13)),
                ],
                const SizedBox(height: 22),
                Text(
                  'Surveys on this phone',
                  style: TextStyle(fontSize: 16, fontWeight: FontWeight.w800, color: AppColors.ink),
                ),
                const SizedBox(height: 8),
                if (items.isEmpty)
                  Padding(
                    padding: const EdgeInsets.symmetric(vertical: 24),
                    child: Text(
                      'No surveys collected on this phone yet.',
                      textAlign: TextAlign.center,
                      style: TextStyle(color: AppColors.muted),
                    ),
                  )
                else
                  for (final s in items) _SubmissionRow(s: s, progress: SurveyQueue.progress.value[s.id]),
              ],
            ),
          );
        },
      ),
    );
  }
}

class _ConnectionCard extends StatelessWidget {
  const _ConnectionCard({required this.online});

  final bool online;

  @override
  Widget build(BuildContext context) {
    final String title;
    final String text;
    final Color color;
    if (!online) {
      title = 'Offline';
      text = 'Surveys and recordings are saved on this phone and upload automatically when the connection returns.';
      color = AppColors.warnDeep;
    } else if (Session.needsOnlineSignIn.value) {
      title = 'Online · sign-in needed';
      text = 'Enter your password (Sync now) to upload the surveys saved on this phone.';
      color = AppColors.warnDeep;
    } else if (Session.offlineMode.value) {
      title = 'Connecting';
      text = 'Signing in to the server…';
      color = AppColors.info;
    } else {
      title = 'Online';
      text = 'Pending surveys upload automatically. Use Sync now to upload at once or retry failed ones.';
      color = AppColors.pos;
    }
    return Container(
      padding: const EdgeInsets.all(14),
      decoration: BoxDecoration(
        color: AppColors.surface,
        borderRadius: BorderRadius.circular(14),
        border: Border.all(color: AppColors.line),
      ),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Icon(online ? Icons.wifi_rounded : Icons.wifi_off_rounded, color: color),
          const SizedBox(width: 12),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  title,
                  style: TextStyle(fontWeight: FontWeight.w800, color: color),
                ),
                const SizedBox(height: 2),
                Text(text, style: TextStyle(color: AppColors.ink2, fontSize: 13, height: 1.4)),
              ],
            ),
          ),
        ],
      ),
    );
  }
}

class _CountTile extends StatelessWidget {
  const _CountTile({required this.label, required this.value, required this.color});

  final String label;
  final int value;
  final Color color;

  @override
  Widget build(BuildContext context) {
    return Container(
      width: 104,
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
      decoration: BoxDecoration(
        color: AppColors.surface,
        borderRadius: BorderRadius.circular(12),
        border: Border.all(color: AppColors.line),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            '$value',
            style: TextStyle(fontFamily: kDisplayFont, fontSize: 22, fontWeight: FontWeight.w800, color: color),
          ),
          Text(label, style: TextStyle(fontSize: 12, color: AppColors.ink2)),
        ],
      ),
    );
  }
}

class _SubmissionRow extends StatelessWidget {
  const _SubmissionRow({required this.s, this.progress});

  final QueuedSurvey s;
  final double? progress;

  @override
  Widget build(BuildContext context) {
    final color = _statusColor(s.status);
    final created = s.createdAt;
    final showProgress = s.status == SurveyStatus.uploading;
    final total = s.mediaBytes ?? 0;
    final resumed = s.status != SurveyStatus.uploading && total > 0 && s.uploadedBytes > 0 && !s.isSynced
        ? ' · ${(100 * s.uploadedBytes / total).floor()}% of the recording already uploaded'
        : '';
    return Container(
      margin: const EdgeInsets.only(bottom: 10),
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(
        color: AppColors.surface,
        borderRadius: BorderRadius.circular(12),
        border: Border.all(color: AppColors.line),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Icon(_statusIcon(s.status), color: color, size: 20),
              const SizedBox(width: 8),
              Expanded(
                child: Text(
                  '${s.productName} · ${s.respondentLabel}',
                  overflow: TextOverflow.ellipsis,
                  style: TextStyle(fontWeight: FontWeight.w700, color: AppColors.ink),
                ),
              ),
              Text(
                syncLabel(s.status),
                style: TextStyle(color: color, fontWeight: FontWeight.w700, fontSize: 12.5),
              ),
            ],
          ),
          const SizedBox(height: 4),
          Text(
            '${created.day}/${created.month} ${created.hour.toString().padLeft(2, '0')}:'
            '${created.minute.toString().padLeft(2, '0')} · ${s.mediaKind == 'video' ? 'Video' : 'Audio'}'
            '${total > 0 ? ' ${_size(total)}' : ''}$resumed',
            style: TextStyle(color: AppColors.muted, fontSize: 12.5),
          ),
          if (showProgress) ...[
            const SizedBox(height: 8),
            LinearProgressIndicator(value: progress, minHeight: 5, borderRadius: BorderRadius.circular(4)),
          ],
          if (s.error != null && !s.isSynced) ...[
            const SizedBox(height: 6),
            Text(
              s.error!,
              style: TextStyle(color: s.status == SurveyStatus.failed ? AppColors.neg : AppColors.ink2, fontSize: 12.5),
            ),
          ],
          if (s.status == SurveyStatus.failed)
            Align(
              alignment: Alignment.centerRight,
              child: TextButton.icon(
                onPressed: () => SurveyQueue.retry(s.id),
                icon: const Icon(Icons.refresh_rounded, size: 18),
                label: const Text('Try again'),
              ),
            ),
        ],
      ),
    );
  }
}
