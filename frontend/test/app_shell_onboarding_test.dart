import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:mobile/core/app_theme.dart';
import 'package:mobile/features/auth/welcome_screen.dart';
import 'package:mobile/features/help/help_screen.dart';
import 'package:mobile/features/navigation/authenticated_shell.dart';
import 'package:mobile/features/splash/launch_splash_screen.dart';

void main() {
  testWidgets('launch artwork appears before the destination', (tester) async {
    await tester.pumpWidget(
      const MaterialApp(
        home: LaunchSplashScreen(
          duration: Duration(milliseconds: 50),
          destination: Scaffold(body: Text('Welcome destination')),
        ),
      ),
    );

    expect(find.byKey(const ValueKey('launch-splash')), findsOneWidget);
    expect(find.text('Welcome destination'), findsNothing);

    await tester.pump(const Duration(milliseconds: 60));
    await tester.pumpAndSettle();

    expect(find.text('Welcome destination'), findsOneWidget);
  });

  testWidgets('welcome portal exposes separate sign-in and sign-up actions', (
    tester,
  ) async {
    var signInSelected = false;
    var signUpSelected = false;

    await tester.pumpWidget(
      MaterialApp(
        theme: AppTheme.light,
        home: WelcomeScreen(
          onSignIn: () => signInSelected = true,
          onCreateAccount: () => signUpSelected = true,
        ),
      ),
    );

    expect(find.text('Welcome to Himikama'), findsOneWidget);
    await tester.tap(find.byKey(const Key('welcome-sign-in')));
    await tester.tap(find.byKey(const Key('welcome-create-account')));

    expect(signInSelected, isTrue);
    expect(signUpSelected, isTrue);
  });

  testWidgets('bottom navigation switches between all three main pages', (
    tester,
  ) async {
    await tester.pumpWidget(
      MaterialApp(
        theme: AppTheme.light,
        home: AuthenticatedShell(
          displayName: 'Charith Hewage',
          email: 'charith@example.com',
          onSignOut: () async {},
          homePage: const Center(child: Text('Home content')),
          assessmentsPage: const Center(child: Text('Assessment content')),
          helpPage: const Center(child: Text('Help content')),
        ),
      ),
    );

    expect(find.text('Home content'), findsOneWidget);

    await tester.tap(find.byIcon(Icons.assignment_outlined));
    await tester.pumpAndSettle();
    expect(find.text('Assessment content'), findsOneWidget);
    expect(find.widgetWithText(AppBar, 'Assessments'), findsOneWidget);

    await tester.tap(find.byIcon(Icons.help_outline));
    await tester.pumpAndSettle();
    expect(find.text('Help content'), findsOneWidget);
    expect(find.widgetWithText(AppBar, 'Help'), findsOneWidget);

    await tester.tap(find.byIcon(Icons.home_outlined));
    await tester.pumpAndSettle();
    expect(find.text('Home content'), findsOneWidget);
  });

  testWidgets('home call to action opens the Assessments tab', (tester) async {
    await tester.pumpWidget(
      MaterialApp(
        theme: AppTheme.light,
        home: AuthenticatedShell(
          displayName: 'Charith Hewage',
          email: 'charith@example.com',
          onSignOut: () async {},
          assessmentsPage: const Center(child: Text('Assessment workspace')),
        ),
      ),
    );

    expect(find.text('Welcome, Charith'), findsOneWidget);

    final startAssessmentButton = find.byKey(
      const Key('home-start-assessment'),
    );

    await Scrollable.ensureVisible(
      tester.element(startAssessmentButton),
      alignment: 0.5,
    );
    await tester.pumpAndSettle();

    await tester.tap(startAssessmentButton);
    await tester.pumpAndSettle();

    expect(find.text('Assessment workspace'), findsOneWidget);
  });

  testWidgets('home call to action can start the intake flow directly', (
    tester,
  ) async {
    var started = false;
    await tester.pumpWidget(
      MaterialApp(
        theme: AppTheme.light,
        home: AuthenticatedShell(
          displayName: 'Charith Hewage',
          email: 'charith@example.com',
          onSignOut: () async {},
          onStartAssessment: () {
            started = true;
          },
          assessmentsPage: const Center(child: Text('Assessment workspace')),
        ),
      ),
    );

    final startAssessmentButton = find.byKey(
      const Key('home-start-assessment'),
    );
    await Scrollable.ensureVisible(
      tester.element(startAssessmentButton),
      alignment: 0.5,
    );
    await tester.pumpAndSettle();
    await tester.tap(startAssessmentButton);
    await tester.pumpAndSettle();

    expect(started, isTrue);
    expect(find.text('Assessment workspace'), findsNothing);
    final navigationBar = tester.widget<NavigationBar>(
      find.byKey(const Key('main-bottom-navigation')),
    );
    expect(navigationBar.selectedIndex, 0);
  });

  testWidgets('account menu presents identity and sign out', (tester) async {
    var signedOut = false;
    await tester.pumpWidget(
      MaterialApp(
        theme: AppTheme.light,
        home: AuthenticatedShell(
          displayName: 'Charith Hewage',
          email: 'charith@example.com',
          onSignOut: () async => signedOut = true,
          homePage: const SizedBox(),
          assessmentsPage: const SizedBox(),
          helpPage: const SizedBox(),
        ),
      ),
    );

    await tester.tap(find.byKey(const Key('account-menu-button')));
    await tester.pumpAndSettle();

    expect(find.text('Charith Hewage'), findsOneWidget);
    expect(find.text('charith@example.com'), findsOneWidget);

    await tester.tap(find.byKey(const Key('account-sign-out')));
    await tester.pumpAndSettle();
    expect(signedOut, isTrue);
  });

  testWidgets('help page explains the full assessment flow', (tester) async {
    await tester.pumpWidget(
      MaterialApp(
        theme: AppTheme.light,
        home: const Scaffold(body: HelpScreen()),
      ),
    );

    expect(find.text('How to use Himikama'), findsOneWidget);
    expect(find.text('Open Assessments'), findsOneWidget);
    expect(find.text('Describe one incident'), findsOneWidget);
    expect(find.text('Review the extracted details'), findsOneWidget);
    await tester.scrollUntilVisible(find.text('Return through history'), 300);
    expect(find.text('Return through history'), findsOneWidget);
  });
}
