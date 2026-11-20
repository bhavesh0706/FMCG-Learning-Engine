import 'dart:async';

import 'package:flutter/foundation.dart';
import 'package:geolocator/geolocator.dart';

/*
  GPS for surveys, without internet.

    Android  Fused Location Provider (GPS/GNSS, plus Wi-Fi/cell when there is a network).
             Satellite positioning works offline. ACCESS_FINE_LOCATION gives a precise fix;
             if the person allows only "approximate" location the fix is ~1-3 km.
    iOS      Core Location (GPS, plus Wi-Fi when available); works offline.
             "Precise Location" off gives a reduced-accuracy fix: we ask for temporary
             full accuracy (Info.plist NSLocationTemporaryUsageDescriptionDictionary).
    Web      the browser's Geolocation API (geolocator_web). A phone browser may use GPS;
             a laptop usually positions by Wi-Fi and needs a network.

  A fix is never sent on its own: it's saved with the survey (offline queue) and sent with
  it, keeping the time the phone took it (location_captured_at) and its accuracy.
*/

/// One GPS fix, as the server stores it (POST /surveys/responses).
class GpsFix {
  const GpsFix({
    required this.latitude,
    required this.longitude,
    required this.accuracy,
    required this.capturedAt,
    required this.source,
    this.lastKnown = false,
    this.reducedAccuracy = false,
  });

  final double latitude;
  final double longitude;

  /// Radius in metres (68% confidence).
  final double accuracy;

  /// When the phone took the fix (UTC).
  final DateTime capturedAt;

  /// android | ios | web
  final String source;

  /// The GPS gave nothing in time: the phone's last known position (recent) was used.
  final bool lastKnown;

  /// The person allowed only approximate location.
  final bool reducedAccuracy;

  bool get isPoor => accuracy > GpsService.poorAccuracyMetres;

  Map<String, dynamic> toJson() => {
        'latitude': latitude,
        'longitude': longitude,
        'location_accuracy': double.parse(accuracy.toStringAsFixed(1)),
        'location_captured_at': capturedAt.toUtc().toIso8601String(),
        'location_source': source,
      };

  String get coordinates => '${latitude.toStringAsFixed(6)}, ${longitude.toStringAsFixed(6)}';

  /// Put into a place field while offline (no address lookup): "GPS 28.601077, 77.432895 (±6 m)".
  /// Replaced by the address when the app is back online, or by the server when the survey
  /// arrives (backend api/geo.py is_gps_placeholder).
  String get placeholder => 'GPS $coordinates (±${accuracy.round()} m)';

  static final _placeholder = RegExp(r'^\s*GPS\s+-?\d{1,2}(\.\d+)?,\s*-?\d{1,3}(\.\d+)?');

  static bool isPlaceholder(String? text) => text != null && _placeholder.hasMatch(text);
}

enum GpsProblem { serviceOff, denied, deniedForever, timeout, unavailable }

class GpsException implements Exception {
  const GpsException(this.problem);

  final GpsProblem problem;

  String get message => switch (problem) {
        GpsProblem.serviceOff => 'Location is turned off on this device. Turn it on, or type the location.',
        GpsProblem.denied => 'Location is not allowed. Allow it, or type the location.',
        GpsProblem.deniedForever => 'Location is blocked for this app. Allow it in Settings, or type the location.',
        GpsProblem.timeout =>
          'No GPS signal in time. Move to an open area (away from walls and roofs) and try again, or type the location.',
        GpsProblem.unavailable => "Couldn't get the location on this device. Try again, or type the location.",
      };

  /// Settings can fix it (app permission or the device's location switch).
  bool get needsSettings => problem == GpsProblem.deniedForever || problem == GpsProblem.serviceOff;

  @override
  String toString() => message;
}

class GpsService {
  GpsService._();

  /// Above this the fix is shown as low accuracy (still saved).
  static const poorAccuracyMetres = 100.0;

  /// A last known position older than this isn't used when the GPS times out.
  static const lastKnownMaxAge = Duration(minutes: 5);

  static String get platform {
    if (kIsWeb) return 'web';
    return defaultTargetPlatform == TargetPlatform.iOS ? 'ios' : 'android';
  }

  static LocationSettings _settings(Duration timeLimit) {
    if (kIsWeb) return LocationSettings(accuracy: LocationAccuracy.high, timeLimit: timeLimit);
    if (defaultTargetPlatform == TargetPlatform.android) {
      // Fused provider: GNSS offline, faster with Wi-Fi/cell when available.
      return AndroidSettings(accuracy: LocationAccuracy.best, timeLimit: timeLimit, forceLocationManager: false);
    }
    if (defaultTargetPlatform == TargetPlatform.iOS) {
      return AppleSettings(
        accuracy: LocationAccuracy.best,
        timeLimit: timeLimit,
        activityType: ActivityType.other,
        pauseLocationUpdatesAutomatically: false,
        allowBackgroundLocationUpdates: false,
      );
    }
    return LocationSettings(accuracy: LocationAccuracy.best, timeLimit: timeLimit);
  }

  /// Checks the device and the permission (asking when [ask]), then takes a fix.
  /// Throws [GpsException].
  static Future<GpsFix> capture({bool ask = true, Duration timeLimit = const Duration(seconds: 25)}) async {
    final bool serviceOn;
    try {
      serviceOn = await Geolocator.isLocationServiceEnabled();
    } catch (_) {
      throw const GpsException(GpsProblem.unavailable);
    }
    if (!serviceOn) throw const GpsException(GpsProblem.serviceOff);

    var permission = await Geolocator.checkPermission();
    if (permission == LocationPermission.denied && ask) permission = await Geolocator.requestPermission();
    if (permission == LocationPermission.deniedForever) throw const GpsException(GpsProblem.deniedForever);
    if (permission == LocationPermission.denied || permission == LocationPermission.unableToDetermine) {
      throw const GpsException(GpsProblem.denied);
    }

    var reduced = false;
    if (!kIsWeb) {
      try {
        if (await Geolocator.getLocationAccuracy() == LocationAccuracyStatus.reduced) {
          // iOS: ask for precise location for this survey; Android can't upgrade from here.
          reduced = defaultTargetPlatform != TargetPlatform.iOS ||
              await Geolocator.requestTemporaryFullAccuracy(purposeKey: 'SurveyLocation') == LocationAccuracyStatus.reduced;
        }
      } catch (_) {
        // not supported on this OS version: carry on
      }
    }

    try {
      final pos = await Geolocator.getCurrentPosition(locationSettings: _settings(timeLimit));
      return _fix(pos, reduced: reduced);
    } on LocationServiceDisabledException {
      throw const GpsException(GpsProblem.serviceOff);
    } on PermissionDeniedException {
      throw const GpsException(GpsProblem.denied);
    } on TimeoutException {
      // No satellites in time (indoors): a recent last known position is better than nothing.
      final last = await _lastKnown();
      if (last != null) return _fix(last, reduced: reduced, lastKnown: true);
      throw const GpsException(GpsProblem.timeout);
    } catch (_) {
      final last = await _lastKnown();
      if (last != null) return _fix(last, reduced: reduced, lastKnown: true);
      throw const GpsException(GpsProblem.unavailable);
    }
  }

  static Future<Position?> _lastKnown() async {
    if (kIsWeb) return null; // browsers have no last known position
    try {
      final p = await Geolocator.getLastKnownPosition();
      if (p == null) return null;
      return DateTime.now().difference(p.timestamp).abs() <= lastKnownMaxAge ? p : null;
    } catch (_) {
      return null;
    }
  }

  static GpsFix _fix(Position p, {bool reduced = false, bool lastKnown = false}) => GpsFix(
        latitude: double.parse(p.latitude.toStringAsFixed(6)),
        longitude: double.parse(p.longitude.toStringAsFixed(6)),
        accuracy: p.accuracy,
        // The position's own time (the moment of the fix), not when we read it.
        capturedAt: p.timestamp.toUtc(),
        source: platform,
        lastKnown: lastKnown,
        reducedAccuracy: reduced,
      );

  /// Opens the right Settings page for [e].
  static Future<void> openSettingsFor(GpsException e) async {
    if (e.problem == GpsProblem.serviceOff) {
      await Geolocator.openLocationSettings();
    } else {
      await Geolocator.openAppSettings();
    }
  }
}
