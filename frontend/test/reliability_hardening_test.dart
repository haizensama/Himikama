import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:mobile/core/api_service.dart';
import 'package:mobile/core/pending_attempt_store.dart';
import 'package:mobile/features/auth/auth_gate.dart';
import 'package:mobile/features/results/pending_attempt_recovery.dart';

void main() {
  const ownerUid = 'firebase-user-a';
  const attemptId = 'b9dd68f9-e2bb-4aa5-8889-6bca4c8dab42';

  test('client-generated attempt identifiers are valid UUID v4 values', () {
    final first = createAttemptUuid();
    final second = createAttemptUuid();
    final pattern = RegExp(
      r'^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-'
      r'[89ab][0-9a-f]{3}-[0-9a-f]{12}$',
    );

    expect(first, matches(pattern));
    expect(second, matches(pattern));
    expect(second, isNot(first));
  });

  test(
    'pending reference is isolated by Firebase UID and exact attempt ID',
    () async {
      final store = MemoryPendingAttemptStore();
      await store.save(
        const PendingAttemptReference(ownerUid: ownerUid, attemptId: attemptId),
      );

      expect((await store.readForUser(ownerUid))?.attemptId, attemptId);
      expect(await store.readForUser('firebase-user-b'), isNull);

      await store.clear(ownerUid: ownerUid, attemptId: createAttemptUuid());
      expect(await store.readForUser(ownerUid), isNotNull);

      await store.clear(ownerUid: ownerUid, attemptId: attemptId);
      expect(await store.readForUser(ownerUid), isNull);
    },
  );

  test('offline, timeout, and server failures are retryable', () {
    for (final kind in const [
      ApiFailureKind.offline,
      ApiFailureKind.timeout,
      ApiFailureKind.server,
    ]) {
      expect(
        ApiException(message: 'temporary', kind: kind).isTransient,
        isTrue,
      );
    }
    expect(
      const ApiException(
        message: 'sign in',
        kind: ApiFailureKind.authentication,
      ).isTransient,
      isFalse,
    );
  });

  test('technical connection details stay separate from user-facing text', () {
    const error = ApiException(
      message:
          'We could not reach Himikama. Check your connection and try again.',
      technicalDetails: 'Server: http://10.0.2.2:8000',
      kind: ApiFailureKind.offline,
    );

    expect(error.toString(), error.message);
    expect(error.message, isNot(contains('10.0.2.2')));
    expect(error.technicalDetails, contains('10.0.2.2'));
  });

  test('authentication routing requires sign-in and verified email', () {
    expect(
      classifyAuthenticationStage(signedIn: false, emailVerified: false),
      AuthenticationStage.signedOut,
    );
    expect(
      classifyAuthenticationStage(signedIn: true, emailVerified: false),
      AuthenticationStage.emailVerification,
    );
    expect(
      classifyAuthenticationStage(signedIn: true, emailVerified: true),
      AuthenticationStage.activeProfile,
    );
  });

  testWidgets('signed-in user is prompted to resume a pending attempt', (
    tester,
  ) async {
    final store = MemoryPendingAttemptStore(
      const PendingAttemptReference(ownerUid: ownerUid, attemptId: attemptId),
    );
    String? resumedAttemptId;

    await tester.pumpWidget(
      MaterialApp(
        home: PendingAttemptRecovery(
          ownerUid: ownerUid,
          store: store,
          onResume: (value) async {
            resumedAttemptId = value;
          },
          child: const Scaffold(body: Text('Home content')),
        ),
      ),
    );
    await tester.pumpAndSettle();

    expect(find.byKey(const Key('pending-attempt-dialog')), findsOneWidget);
    expect(find.text('Resume your assessment?'), findsOneWidget);

    await tester.tap(find.byKey(const Key('pending-attempt-resume')));
    await tester.pumpAndSettle();

    expect(resumedAttemptId, attemptId);
    expect(find.text('Home content'), findsOneWidget);
  });

  testWidgets('pending attempt from another UID is never displayed', (
    tester,
  ) async {
    final store = MemoryPendingAttemptStore(
      const PendingAttemptReference(
        ownerUid: 'firebase-user-b',
        attemptId: attemptId,
      ),
    );

    await tester.pumpWidget(
      MaterialApp(
        home: PendingAttemptRecovery(
          ownerUid: ownerUid,
          store: store,
          onResume: (_) async {},
          child: const Scaffold(body: Text('Private home')),
        ),
      ),
    );
    await tester.pumpAndSettle();

    expect(find.byKey(const Key('pending-attempt-dialog')), findsNothing);
    expect(find.text('Private home'), findsOneWidget);
  });
}
