import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:mobile/core/api_service.dart';
import 'package:mobile/core/app_theme.dart';
import 'package:mobile/features/auth/auth_gate.dart';
import 'package:mobile/features/home/home_screen.dart';
import 'package:mobile/features/navigation/authenticated_shell.dart';
import 'package:mobile/features/privacy/account_settings_screen.dart';
import 'package:mobile/features/privacy/policy_content.dart';
import 'package:mobile/features/privacy/policy_document_screen.dart';

void main() {
  UserProfile profile({
    String accountStatus = 'active',
    bool termsCurrent = true,
    bool privacyCurrent = true,
    bool assessmentConsentCurrent = false,
  }) {
    return UserProfile(
      displayName: 'Privacy User',
      email: 'privacy@example.test',
      emailVerified: true,
      accountStatus: accountStatus,
      termsVersion: '1.0',
      privacyVersion: '1.1',
      termsCurrent: termsCurrent,
      privacyCurrent: privacyCurrent,
      assessmentConsentVersion: assessmentConsentCurrent ? '1.0' : '',
      assessmentConsentCurrent: assessmentConsentCurrent,
    );
  }

  test('privacy contact and user-controlled retention are published', () {
    expect(himikamaPrivacyContact, 'ghc_dilshan@protonmail.com');
    final notice = privacySections.map((section) => section.body).join(' ');
    expect(notice, contains('Google Gemini API'));
    expect(notice, contains('until you delete'));
    expect(notice, contains('seven-day recovery period'));
    expect(notice, contains('withdraw consent'));
  });

  test('profile routing handles policy updates and deletion recovery', () {
    expect(classifyProfileAccess(profile()), ProfileAccessStage.active);
    expect(
      classifyProfileAccess(profile(privacyCurrent: false)),
      ProfileAccessStage.policyReview,
    );
    expect(
      classifyProfileAccess(profile(accountStatus: 'deletion_scheduled')),
      ProfileAccessStage.deletionRecovery,
    );
    expect(
      classifyProfileAccess(profile(accountStatus: 'deletion_processing')),
      ProfileAccessStage.deletionRecovery,
    );
  });

  test('profile timestamps and consent parse from API data', () {
    final parsed = UserProfile.fromJson({
      'display_name': 'Privacy User',
      'email': 'privacy@example.test',
      'email_verified': true,
      'account_status': 'deletion_scheduled',
      'terms_version': '1.0',
      'privacy_version': '1.1',
      'terms_current': true,
      'privacy_current': true,
      'assessment_consent_version': '1.0',
      'assessment_consent_current': true,
      'assessment_consent_at': '2026-08-05T10:00:00Z',
      'deletion_effective_at': '2026-08-12T10:00:00Z',
    });
    expect(parsed.assessmentConsentCurrent, isTrue);
    expect(parsed.deletionEffectiveAt, isNotNull);
    expect(parsed.policiesCurrent, isTrue);
  });

  test('profile refresh waits for account settings route disposal', () async {
    final routeDisposed = Completer<bool?>();
    var refreshCount = 0;

    final refresh = refreshProfileAfterRouteIsDisposed(
      routeDisposed: routeDisposed.future,
      onProfileChanged: () => refreshCount++,
    );

    await Future<void>.delayed(Duration.zero);
    expect(refreshCount, 0);

    routeDisposed.complete(null);
    await refresh;
    expect(refreshCount, 1);
  });

  testWidgets('privacy notice displays contact and core controls', (
    tester,
  ) async {
    await tester.pumpWidget(
      MaterialApp(
        theme: AppTheme.light,
        home: const PolicyDocumentScreen(type: PolicyDocumentType.privacy),
      ),
    );

    expect(find.text('Privacy Notice'), findsWidgets);
    expect(find.textContaining(himikamaPrivacyContact), findsWidgets);
    expect(find.text('Information we process'), findsOneWidget);
    await tester.scrollUntilVisible(
      find.text('Retention and deletion'),
      300,
      scrollable: find.byType(Scrollable).first,
    );
    expect(find.text('Retention and deletion'), findsOneWidget);
  });

  testWidgets('deletion dialog keeps its controller through route disposal', (
    tester,
  ) async {
    String? submittedPassword;
    await tester.pumpWidget(
      MaterialApp(
        home: Builder(
          builder: (context) => FilledButton(
            onPressed: () async {
              submittedPassword = await showAccountDeletionConfirmationDialog(
                context,
              );
            },
            child: const Text('Open deletion dialog'),
          ),
        ),
      ),
    );

    await tester.tap(find.text('Open deletion dialog'));
    await tester.pumpAndSettle();
    await tester.enterText(
      find.byKey(const Key('deletion-password')),
      'test-password',
    );
    await tester.tap(find.byKey(const Key('confirm-seven-day-deletion')));
    await tester.pump();
    await tester.tap(find.byKey(const Key('confirm-account-deletion')));

    await tester.pump();
    expect(tester.takeException(), isNull);
    await tester.pumpAndSettle();

    expect(submittedPassword, 'test-password');
    expect(tester.takeException(), isNull);
  });

  testWidgets('account menu opens Account & Privacy', (tester) async {
    var opened = false;
    await tester.pumpWidget(
      MaterialApp(
        theme: AppTheme.light,
        home: AuthenticatedShell(
          displayName: 'Privacy User',
          email: 'privacy@example.test',
          onSignOut: () async {},
          onOpenAccountSettings: () async => opened = true,
          homePage: const SizedBox(),
          assessmentsPage: const SizedBox(),
          helpPage: const SizedBox(),
        ),
      ),
    );

    await tester.tap(find.byKey(const Key('account-menu-button')));
    await tester.pumpAndSettle();
    expect(find.text('Account & Privacy'), findsOneWidget);
    await tester.tap(find.byKey(const Key('account-privacy-settings')));
    await tester.pump();
    expect(opened, isFalse);
    await tester.pumpAndSettle();
    expect(opened, isTrue);
  });
}
