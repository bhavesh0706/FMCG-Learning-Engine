import 'dart:async';

import 'package:connectivity_plus/connectivity_plus.dart';
import 'package:flutter/foundation.dart';

/*
  Network availability (Wi-Fi, mobile data, ethernet, VPN) from the OS.

  "Online" here only means the phone has a network; the server may still be
  unreachable, so every upload still handles connection errors itself. When the
  network comes back, [onReconnect] runs: it restores the online session and
  starts synchronising pending surveys (wired up in main.dart).
*/

class ConnectivityService {
  ConnectivityService._();

  /// True until the OS says there is no network (so nothing is skipped by mistake).
  static final ValueNotifier<bool> online = ValueNotifier<bool>(true);

  /// Called each time the phone goes from no network to a network.
  static Future<void> Function()? onReconnect;

  static StreamSubscription<List<ConnectivityResult>>? _sub;

  static bool _hasNetwork(List<ConnectivityResult> results) => results.any((r) => r != ConnectivityResult.none);

  static Future<void> start() async {
    if (_sub != null) return;
    final connectivity = Connectivity();
    try {
      online.value = _hasNetwork(await connectivity.checkConnectivity());
    } catch (e) {
      debugPrint('Connectivity: check failed ($e)');
    }
    _sub = connectivity.onConnectivityChanged.listen(
      update,
      onError: (Object e) {
        debugPrint('Connectivity: $e');
      },
    );
  }

  /// Applies a change from the OS (public for tests).
  @visibleForTesting
  static void update(List<ConnectivityResult> results) {
    final wasOnline = online.value;
    online.value = _hasNetwork(results);
    if (!wasOnline && online.value) {
      final callback = onReconnect;
      if (callback != null) unawaited(callback());
    }
  }
}
