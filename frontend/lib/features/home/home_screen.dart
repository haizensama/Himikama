import 'package:firebase_auth/firebase_auth.dart';
import 'package:flutter/material.dart';

import '../../core/api_service.dart';
import '../../core/pending_attempt_store.dart';
import '../assessments/assessments_screen.dart';
import '../intake/describe_situation_screen.dart';
import '../navigation/authenticated_shell.dart';
import '../privacy/account_settings_screen.dart';
import '../privacy/assessment_consent_screen.dart';
import '../results/analysis_processing_screen.dart';
import '../results/pending_attempt_recovery.dart';

class HomeScreen extends StatefulWidget {
  const HomeScreen({
    required this.firebaseAuth,
    required this.apiService,
    required this.user,
    required this.profile,
    required this.onProfileChanged,
    super.key,
    this.pendingAttemptStore,
  });

  final FirebaseAuth firebaseAuth;
  final ApiService apiService;
  final User user;
  final UserProfile profile;
  final VoidCallback onProfileChanged;
  final PendingAttemptStore? pendingAttemptStore;

  @override
  State<HomeScreen> createState() => _HomeScreenState();
}

class _HomeScreenState extends State<HomeScreen> {
  late final PendingAttemptStore _pendingAttemptStore;
  late bool _assessmentConsentCurrent;

  @override
  void initState() {
    super.initState();
    _pendingAttemptStore =
        widget.pendingAttemptStore ?? SharedPreferencesPendingAttemptStore();
    _assessmentConsentCurrent = widget.profile.assessmentConsentCurrent;
  }

  Future<void> _startAssessment() async {
    var consentAcceptedNow = false;
    if (!_assessmentConsentCurrent) {
      final accepted = await Navigator.of(context).push<bool>(
        MaterialPageRoute<bool>(
          builder: (_) =>
              AssessmentConsentScreen(apiService: widget.apiService),
        ),
      );
      if (accepted != true || !mounted) return;
      setState(() => _assessmentConsentCurrent = true);
      consentAcceptedNow = true;
    }

    if (!mounted) return;
    await Navigator.of(context).push(
      MaterialPageRoute<void>(
        builder: (_) => DescribeSituationScreen(
          apiService: widget.apiService,
          pendingAttemptStore: _pendingAttemptStore,
        ),
      ),
    );

    if (consentAcceptedNow && mounted) {
      widget.onProfileChanged();
    }
  }

  void _markAssessmentConsentAccepted() {
    if (mounted && !_assessmentConsentCurrent) {
      setState(() => _assessmentConsentCurrent = true);
    }
  }

  Future<void> _resumeAttempt(String attemptId) async {
    await Navigator.of(context).push(
      MaterialPageRoute<void>(
        builder: (_) => AnalysisProcessingScreen(
          apiService: widget.apiService,
          attemptId: attemptId,
          ownerUid: widget.user.uid,
          pendingAttemptStore: _pendingAttemptStore,
        ),
      ),
    );
  }

  Future<void> _openAccountSettings() async {
    final route = MaterialPageRoute<void>(
      builder: (_) => AccountSettingsScreen(
        firebaseAuth: widget.firebaseAuth,
        apiService: widget.apiService,
        user: widget.user,
        profile: widget.profile,
        pendingAttemptStore: _pendingAttemptStore,
      ),
    );

    await Navigator.of(context).push(route);
    // Navigator.push completes when pop starts. Wait until the reverse
    // transition removes the route before replacing the authenticated tree.
    await refreshProfileAfterRouteIsDisposed(
      routeDisposed: route.completed,
      onProfileChanged: () {
        if (mounted) widget.onProfileChanged();
      },
    );
  }

  @override
  Widget build(BuildContext context) {
    return PendingAttemptRecovery(
      ownerUid: widget.user.uid,
      store: _pendingAttemptStore,
      onResume: _resumeAttempt,
      child: AuthenticatedShell(
        displayName: widget.profile.displayName,
        email: widget.profile.email,
        onSignOut: widget.firebaseAuth.signOut,
        onOpenAccountSettings: _openAccountSettings,
        onStartAssessment: _startAssessment,
        assessmentsPage: AssessmentsScreen(
          firebaseAuth: widget.firebaseAuth,
          apiService: widget.apiService,
          user: widget.user,
          profile: widget.profile,
          onAssessmentConsentAccepted: _markAssessmentConsentAccepted,
          pendingAttemptStore: _pendingAttemptStore,
        ),
      ),
    );
  }
}

@visibleForTesting
Future<void> refreshProfileAfterRouteIsDisposed<T>({
  required Future<T?> routeDisposed,
  required VoidCallback onProfileChanged,
}) async {
  await routeDisposed;
  onProfileChanged();
}
