import 'package:flutter_test/flutter_test.dart';

import 'package:mobile/main.dart' show HomeShell;
import 'package:mobile/screens/admin_screens.dart';
import 'package:mobile/screens/change_password_screen.dart';
import 'package:mobile/screens/onboarding_screens.dart';
import 'package:mobile/screens/org_screens.dart';
import 'package:mobile/services/session.dart';
import 'package:mobile/services/survey_queue.dart' show AppProfile;

/// One role contract: admin -> AdminShell (web admin dashboard), executive -> HomeShell,
/// client (organization) -> OrgShell. The role always comes from the server.

Map<String, dynamic> serverUser(String? role, {bool mustChange = false}) => {
      'id': 7,
      'name': 'Vipin Gupta',
      'email': 'vipin@brand.in',
      if (role != null) 'role': role,
      'can_view_history': true,
      'must_change_password': mustChange,
    };

void signInAs(String role, {bool mustChange = false}) {
  Session.token = 'test-token';
  Session.user.value = SessionUser.fromJson(serverUser(role, mustChange: mustChange));
}

void main() {
  tearDown(() {
    Session.token = null;
    Session.user.value = null;
    AppProfile.permissionsDone = false;
  });

  group('SessionUser parses the server role', () {
    test('each known role is kept as sent', () {
      for (final role in ['admin', 'executive', 'client']) {
        final u = SessionUser.fromJson(serverUser(role));
        expect(u.role, role);
        expect(u.hasKnownRole, isTrue);
      }
    });

    test('a missing role is not turned into a survey person', () {
      final u = SessionUser.fromJson(serverUser(null));
      expect(u.role, '');
      expect(u.hasKnownRole, isFalse);
    });

    test('an unknown role stays unknown', () {
      final u = SessionUser.fromJson(serverUser('superuser'));
      expect(u.hasKnownRole, isFalse);
    });

    test('role text is normalised (spaces, upper case)', () {
      expect(SessionUser.fromJson(serverUser(' Admin ')).role, 'admin');
    });

    test('must_change_password is read and survives save/restore', () {
      final u = SessionUser.fromJson(serverUser('client', mustChange: true));
      expect(u.mustChangePassword, isTrue);
      final restored = SessionUser.fromJson(u.toJson());
      expect(restored.role, 'client');
      expect(restored.mustChangePassword, isTrue);
    });
  });

  group('Role label', () {
    test('shows the real role, never "Survey executive" for everyone', () {
      signInAs('admin');
      expect(Session.roleLabel, 'Admin');
      signInAs('client');
      expect(Session.roleLabel, 'Super Admin');
      signInAs('executive');
      expect(Session.roleLabel, 'Survey executive');
    });
  });

  group('Navigation after sign-in', () {
    test('admin opens the admin dashboard, never the survey app', () {
      signInAs('admin');
      expect(Session.isAdmin, isTrue);
      expect(nextScreen(), isA<AdminShell>()); // no permissions step: admins don't record
      AppProfile.permissionsDone = true;
      expect(nextScreen(), isA<AdminShell>());
      expect(nextScreen(), isNot(isA<HomeShell>()));
    });

    test('survey person opens HomeShell, after the permissions step', () {
      signInAs('executive');
      expect(nextScreen(), isA<PermissionsScreen>());
      AppProfile.permissionsDone = true;
      expect(nextScreen(), isA<HomeShell>());
      expect(Session.isAdmin, isFalse);
    });

    test('organization opens its own pages, without the permissions step', () {
      signInAs('client');
      expect(nextScreen(), isA<OrgShell>());
    });

    test('a required new password comes first, for any role', () {
      for (final role in ['client', 'admin', 'executive']) {
        signInAs(role, mustChange: true);
        expect(nextScreen(), isA<ChangePasswordScreen>(), reason: role);
      }
    });

    test('an unknown role never opens a dashboard', () {
      Session.token = 'test-token';
      Session.user.value = SessionUser.fromJson(serverUser('superuser'));
      expect(nextScreen(), isA<WelcomeScreen>());
    });

    test('signed out opens Welcome', () {
      expect(nextScreen(), isA<WelcomeScreen>());
    });

    test('switching accounts uses the new account\'s role', () {
      AppProfile.permissionsDone = true;
      signInAs('executive');
      expect(nextScreen(), isA<HomeShell>());
      signInAs('client');
      expect(nextScreen(), isA<OrgShell>());
      signInAs('admin');
      expect(Session.isAdmin, isTrue);
      expect(nextScreen(), isA<AdminShell>());
    });
  });
}