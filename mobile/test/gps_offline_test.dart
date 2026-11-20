// Offline GPS on Android and iOS: permissions, location off, timeouts, low accuracy,
// and a fix taken offline that reaches the server with its original time.
//
// Run:  flutter test --dart-define=API_BASE_URL=http://127.0.0.1:8765

import 'dart:async';
import 'dart:io';

import 'package:connectivity_plus/connectivity_plus.dart';
import 'package:flutter/foundation.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:geolocator/geolocator.dart' show AndroidSettings, AppleSettings;
import 'package:geolocator_platform_interface/geolocator_platform_interface.dart';
import 'package:mobile/services/connectivity_service.dart';
import 'package:mobile/services/gps_service.dart';
import 'package:mobile/services/survey_queue.dart';
import 'package:plugin_platform_interface/plugin_platform_interface.dart';

import 'offline_test_support.dart';

/// The phone's location hardware / OS, as the plugin sees it.
class FakeLocation extends GeolocatorPlatform with MockPlatformInterfaceMixin {
  bool serviceOn = true;
  LocationPermission permission = LocationPermission.whileInUse;
  LocationPermission afterRequest = LocationPermission.whileInUse;
  LocationAccuracyStatus accuracyStatus = LocationAccuracyStatus.precise;
  LocationAccuracyStatus afterFullAccuracyRequest = LocationAccuracyStatus.precise;
  Position? current;
  Object? currentError;
  Position? lastKnown;
  LocationSettings? lastSettings;
  int permissionRequests = 0;
  int fullAccuracyRequests = 0;
  String? opened;

  @override
  Future<bool> isLocationServiceEnabled() async => serviceOn;
  @override
  Future<LocationPermission> checkPermission() async => permission;
  @override
  Future<LocationPermission> requestPermission() async {
    permissionRequests++;
    return permission = afterRequest;
  }

  @override
  Future<LocationAccuracyStatus> getLocationAccuracy() async => accuracyStatus;
  @override
  Future<LocationAccuracyStatus> requestTemporaryFullAccuracy({required String purposeKey}) async {
    fullAccuracyRequests++;
    expect(purposeKey, 'SurveyLocation'); // the key in ios/Runner/Info.plist
    return accuracyStatus = afterFullAccuracyRequest;
  }

  @override
  Future<Position> getCurrentPosition({LocationSettings? locationSettings}) async {
    lastSettings = locationSettings;
    if (currentError != null) throw currentError!;
    return current!;
  }

  @override
  Future<Position?> getLastKnownPosition({bool forceLocationManager = false}) async => lastKnown;
  @override
  Future<bool> openAppSettings() async {
    opened = 'app';
    return true;
  }

  @override
  Future<bool> openLocationSettings() async {
    opened = 'location';
    return true;
  }
}

Position position(double lat, double lng, {double accuracy = 8, DateTime? at}) => Position(
      latitude: lat,
      longitude: lng,
      timestamp: at ?? DateTime.now().toUtc(),
      accuracy: accuracy,
      altitude: 0,
      altitudeAccuracy: 0,
      heading: 0,
      headingAccuracy: 0,
      speed: 0,
      speedAccuracy: 0,
    );

void main() {
  late FakeLocation phone;

  setUp(() {
    phone = FakeLocation();
    GeolocatorPlatform.instance = phone;
  });

  tearDown(() => debugDefaultTargetPlatformOverride = null);

  group('Taking a fix (no internet needed)', () {
    test('Android: precise GNSS fix with the moment it was taken, fused provider settings', () async {
      debugDefaultTargetPlatformOverride = TargetPlatform.android;
      final at = DateTime.utc(2026, 11, 20, 4, 15, 30);
      phone.current = position(28.4743881234, 77.5039904321, accuracy: 6.44, at: at);
      final fix = await GpsService.capture();
      expect(fix.latitude, 28.474388);
      expect(fix.longitude, 77.50399);
      expect(fix.accuracy, closeTo(6.44, 0.001));
      expect(fix.capturedAt, at, reason: 'the time of the fix, not of the upload');
      expect(fix.source, 'android');
      expect(fix.isPoor || fix.lastKnown || fix.reducedAccuracy, isFalse);
      expect(phone.lastSettings, isA<AndroidSettings>());
      expect((phone.lastSettings as AndroidSettings).forceLocationManager, isFalse);
      expect(phone.lastSettings!.accuracy, LocationAccuracy.best);
    });

    test('iOS: Core Location fix; Precise Location off asks for full accuracy once', () async {
      debugDefaultTargetPlatformOverride = TargetPlatform.iOS;
      phone.accuracyStatus = LocationAccuracyStatus.reduced;
      phone.afterFullAccuracyRequest = LocationAccuracyStatus.precise;
      phone.current = position(19.076, 72.8777);
      final fix = await GpsService.capture();
      expect(fix.source, 'ios');
      expect(phone.fullAccuracyRequests, 1);
      expect(fix.reducedAccuracy, isFalse, reason: 'the person allowed precise location');
      expect(phone.lastSettings, isA<AppleSettings>());

      phone.accuracyStatus = LocationAccuracyStatus.reduced;
      phone.afterFullAccuracyRequest = LocationAccuracyStatus.reduced; // declined
      phone.current = position(19.076, 72.8777, accuracy: 2500);
      final rough = await GpsService.capture();
      expect(rough.reducedAccuracy, isTrue);
      expect(rough.isPoor, isTrue);
    });

    test('Android approximate-only permission is reported (no prompt possible from the app)', () async {
      debugDefaultTargetPlatformOverride = TargetPlatform.android;
      phone.accuracyStatus = LocationAccuracyStatus.reduced;
      phone.current = position(12.97, 77.59, accuracy: 1800);
      final fix = await GpsService.capture();
      expect(fix.reducedAccuracy, isTrue);
      expect(phone.fullAccuracyRequests, 0);
    });

    test('low accuracy is kept but flagged', () async {
      phone.current = position(12.97, 77.59, accuracy: 240);
      final fix = await GpsService.capture();
      expect(fix.isPoor, isTrue);
      expect(fix.accuracy, 240);
    });
  });

  group('Place fields while offline', () {
    test('the position is written in a form the app and the server recognise', () {
      final fix = GpsFix(latitude: 28.601077, longitude: 77.432895, accuracy: 6.4, capturedAt: DateTime.utc(2026, 11, 20), source: 'ios');
      expect(fix.placeholder, 'GPS 28.601077, 77.432895 (±6 m)');
      expect(GpsFix.isPlaceholder(fix.placeholder), isTrue);
      expect(GpsFix.isPlaceholder('GPS -33.86, 151.2 (±20 m)'), isTrue);
      for (final typed in ['Sector 62', 'GPS market road', '', null]) {
        expect(GpsFix.isPlaceholder(typed), isFalse, reason: '$typed');
      }
    });
  });

  group('Problems', () {
    test('location services off', () async {
      phone.serviceOn = false;
      final e = await _problem();
      expect(e.problem, GpsProblem.serviceOff);
      expect(e.needsSettings, isTrue);
      await GpsService.openSettingsFor(e);
      expect(phone.opened, 'location');
    });

    test('denied: asked once when allowed to ask, otherwise not', () async {
      phone.permission = LocationPermission.denied;
      phone.afterRequest = LocationPermission.denied;
      expect((await _problem(ask: false)).problem, GpsProblem.denied);
      expect(phone.permissionRequests, 0);
      expect((await _problem()).problem, GpsProblem.denied);
      expect(phone.permissionRequests, 1);

      phone.afterRequest = LocationPermission.whileInUse; // allowed this time
      phone.current = position(1, 2);
      expect((await GpsService.capture()).latitude, 1);
    });

    test('permanently denied sends the person to the app settings', () async {
      phone.permission = LocationPermission.deniedForever;
      final e = await _problem();
      expect(e.problem, GpsProblem.deniedForever);
      expect(e.needsSettings, isTrue);
      await GpsService.openSettingsFor(e);
      expect(phone.opened, 'app');
    });

    test('timeout: a recent last known position is used, an old one is not', () async {
      phone.currentError = TimeoutException('no satellites');
      final recent = DateTime.now().toUtc().subtract(const Duration(minutes: 2));
      phone.lastKnown = position(28.5, 77.3, accuracy: 35, at: recent);
      final fix = await GpsService.capture();
      expect(fix.lastKnown, isTrue);
      expect(fix.capturedAt, recent, reason: 'when that position was really taken');

      phone.lastKnown = position(28.5, 77.3, at: DateTime.now().toUtc().subtract(const Duration(hours: 3)));
      expect((await _problem()).problem, GpsProblem.timeout);
      phone.lastKnown = null;
      expect((await _problem()).problem, GpsProblem.timeout);
    });

    test('location switched off or permission lost while measuring', () async {
      phone.currentError = const LocationServiceDisabledException();
      expect((await _problem()).problem, GpsProblem.serviceOff);
      phone.currentError = const PermissionDeniedException('revoked');
      expect((await _problem()).problem, GpsProblem.denied);
    });
  });

  group('Offline survey with GPS', () {
    late Directory root;
    late FakeServer server;

    setUp(() async {
      root = await setUpPhone();
      server = FakeServer();
      SurveyQueue.api = server;
    });

    tearDown(() => tearDownPhone(root));

    test('the fix is saved on the phone and arrives unchanged when the connection returns', () async {
      signInAs(7);
      ConnectivityService.online.value = false;
      final takenAt = DateTime.utc(2026, 11, 20, 3, 0, 5);
      final fix = GpsFix(
        latitude: 28.474388,
        longitude: 77.50399,
        accuracy: 7.26,
        capturedAt: takenAt,
        source: 'android',
      );
      final s = await collectSurvey(root, extraResponse: fix.toJson());
      expect(server.calls, isEmpty);

      // Closed and reopened while still offline: the fix is in the encrypted database.
      await restartApp();
      signInAs(7);
      await SurveyQueue.load(startUploads: false);
      final saved = SurveyQueue.items.value.single.response;
      expect(saved['latitude'], 28.474388);
      expect(saved['location_captured_at'], '2026-11-20T03:00:05.000Z');

      ConnectivityService.onReconnect = () => SurveyQueue.process();
      ConnectivityService.online.value = false;
      ConnectivityService.update([ConnectivityResult.mobile]); // back online
      for (var i = 0; i < 100 && !server.responseBodies.containsKey(s.clientUuid); i++) {
        await Future<void>.delayed(const Duration(milliseconds: 30));
      }
      await SurveyQueue.idle();

      final sent = server.responseBodies[s.clientUuid]!;
      expect(sent['latitude'], 28.474388);
      expect(sent['longitude'], 77.50399);
      expect(sent['location_accuracy'], 7.3);
      expect(sent['location_captured_at'], '2026-11-20T03:00:05.000Z', reason: 'the original time, not the sync time');
      expect(sent['location_source'], 'android');
    });

    test('a survey without a fix sends no GPS fields', () async {
      signInAs(7);
      final s = await collectSurvey(root);
      await SurveyQueue.process();
      final sent = server.responseBodies[s.clientUuid]!;
      expect(sent.keys.where((k) => k.startsWith('location_') || k == 'latitude'), isEmpty);
    });
  });
}

Future<GpsException> _problem({bool ask = true}) async {
  try {
    await GpsService.capture(ask: ask);
  } on GpsException catch (e) {
    return e;
  }
  fail('expected a GpsException');
}
