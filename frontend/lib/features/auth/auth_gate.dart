import 'package:firebase_auth/firebase_auth.dart';
import 'package:flutter/material.dart';

import '../../core/api_service.dart';
import '../../core/pending_registration.dart';
import '../home/home_screen.dart';
import '../privacy/account_deletion_pending_screen.dart';
import '../privacy/policy_review_screen.dart';
import 'email_verification_screen.dart';
import 'login_screen.dart';
import 'profile_setup_screen.dart';
import 'register_screen.dart';
import 'welcome_screen.dart';

enum AuthenticationStage { signedOut, emailVerification, activeProfile }

enum ProfileAccessStage { active, policyReview, deletionRecovery, unavailable }

AuthenticationStage classifyAuthenticationStage({
  required bool signedIn,
  required bool emailVerified,
}) {
  if (!signedIn) return AuthenticationStage.signedOut;
  if (!emailVerified) return AuthenticationStage.emailVerification;
  return AuthenticationStage.activeProfile;
}

ProfileAccessStage classifyProfileAccess(UserProfile profile) {
  if (const {
    'deletion_scheduled',
    'deletion_processing',
  }.contains(profile.accountStatus)) {
    return ProfileAccessStage.deletionRecovery;
  }
  if (profile.accountStatus != 'active') {
    return ProfileAccessStage.unavailable;
  }
  if (!profile.policiesCurrent) return ProfileAccessStage.policyReview;
  return ProfileAccessStage.active;
}

class AuthGate extends StatefulWidget {
  const AuthGate({
    required this.firebaseAuth,
    required this.apiService,
    super.key,
  });

  final FirebaseAuth firebaseAuth;
  final ApiService apiService;

  @override
  State<AuthGate> createState() => _AuthGateState();
}

class _AuthGateState extends State<AuthGate> {
  int _revision = 0;

  void _refresh() {
    if (mounted) {
      setState(() => _revision++);
    }
  }

  @override
  Widget build(BuildContext context) {
    return StreamBuilder<User?>(
      stream: widget.firebaseAuth.userChanges(),
      initialData: widget.firebaseAuth.currentUser,
      builder: (context, snapshot) {
        if (snapshot.connectionState == ConnectionState.waiting &&
            snapshot.data == null) {
          return const _LoadingScreen(message: 'Restoring your session…');
        }

        final user = widget.firebaseAuth.currentUser ?? snapshot.data;
        final stage = classifyAuthenticationStage(
          signedIn: user != null,
          emailVerified: user?.emailVerified == true,
        );
        if (stage == AuthenticationStage.signedOut) {
          return WelcomeScreen(
            onSignIn: () {
              Navigator.of(context).push(
                MaterialPageRoute<void>(
                  builder: (_) => LoginScreen(
                    firebaseAuth: widget.firebaseAuth,
                    showBackButton: true,
                  ),
                ),
              );
            },
            onCreateAccount: () {
              Navigator.of(context).push(
                MaterialPageRoute<void>(
                  builder: (_) =>
                      RegisterScreen(firebaseAuth: widget.firebaseAuth),
                ),
              );
            },
          );
        }
        final authenticatedUser = user!;

        if (stage == AuthenticationStage.emailVerification) {
          return EmailVerificationScreen(
            key: ValueKey('verify-${authenticatedUser.uid}-$_revision'),
            firebaseAuth: widget.firebaseAuth,
            user: authenticatedUser,
            onVerified: _refresh,
          );
        }

        return ProfileGate(
          key: ValueKey('profile-${authenticatedUser.uid}-$_revision'),
          firebaseAuth: widget.firebaseAuth,
          apiService: widget.apiService,
          user: authenticatedUser,
        );
      },
    );
  }
}

class ProfileGate extends StatefulWidget {
  const ProfileGate({
    required this.firebaseAuth,
    required this.apiService,
    required this.user,
    super.key,
  });

  final FirebaseAuth firebaseAuth;
  final ApiService apiService;
  final User user;

  @override
  State<ProfileGate> createState() => _ProfileGateState();
}

class _ProfileGateState extends State<ProfileGate> {
  UserProfile? _profile;
  ApiException? _error;
  bool _needsProfile = false;

  @override
  void initState() {
    super.initState();
    _loadProfile();
  }

  Future<void> _loadProfile() async {
    if (mounted) {
      setState(() {
        _profile = null;
        _error = null;
        _needsProfile = false;
      });
    }

    try {
      final profile = await widget.apiService.getProfile();
      if (!mounted) return;
      setState(() => _profile = profile);
    } on ApiException catch (error) {
      if (error.statusCode != 404) {
        if (!mounted) return;
        setState(() => _error = error);
        return;
      }

      final draft = PendingRegistrationStore.read(widget.user.uid);
      if (draft != null && draft.acceptedTerms && draft.acceptedPrivacyPolicy) {
        try {
          final profile = await widget.apiService.createProfile(
            displayName: draft.displayName,
            acceptTerms: true,
            acceptPrivacyPolicy: true,
          );
          PendingRegistrationStore.remove(widget.user.uid);
          if (!mounted) return;
          setState(() => _profile = profile);
          return;
        } on ApiException catch (createError) {
          if (!mounted) return;
          setState(() => _error = createError);
          return;
        }
      }

      if (!mounted) return;
      setState(() => _needsProfile = true);
    }
  }

  @override
  Widget build(BuildContext context) {
    if (_profile case final profile?) {
      final accessStage = classifyProfileAccess(profile);
      if (accessStage == ProfileAccessStage.deletionRecovery) {
        return AccountDeletionPendingScreen(
          firebaseAuth: widget.firebaseAuth,
          apiService: widget.apiService,
          profile: profile,
          onRecovered: _loadProfile,
        );
      }
      if (accessStage == ProfileAccessStage.policyReview) {
        return PolicyReviewScreen(
          firebaseAuth: widget.firebaseAuth,
          apiService: widget.apiService,
          onAccepted: _loadProfile,
        );
      }
      if (accessStage == ProfileAccessStage.unavailable) {
        return _AccountConnectionError(
          error: const ApiException(
            message: 'This account is not currently available.',
            kind: ApiFailureKind.authorization,
          ),
          onRetry: _loadProfile,
          onSignOut: widget.firebaseAuth.signOut,
        );
      }
      return HomeScreen(
        firebaseAuth: widget.firebaseAuth,
        apiService: widget.apiService,
        user: widget.user,
        profile: profile,
        onProfileChanged: _loadProfile,
      );
    }

    if (_needsProfile) {
      final draft = PendingRegistrationStore.read(widget.user.uid);
      return ProfileSetupScreen(
        firebaseAuth: widget.firebaseAuth,
        apiService: widget.apiService,
        user: widget.user,
        initialDisplayName: draft?.displayName ?? widget.user.displayName ?? '',
        initialPoliciesAccepted:
            draft?.acceptedTerms == true &&
            draft?.acceptedPrivacyPolicy == true,
        onCompleted: () {
          PendingRegistrationStore.remove(widget.user.uid);
          _loadProfile();
        },
      );
    }

    if (_error case final error?) {
      return _AccountConnectionError(
        error: error,
        onRetry: _loadProfile,
        onSignOut: widget.firebaseAuth.signOut,
      );
    }

    return const _LoadingScreen(message: 'Preparing your Himikama account…');
  }
}

class _LoadingScreen extends StatelessWidget {
  const _LoadingScreen({required this.message});

  final String message;

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: SafeArea(
        child: Center(
          child: Padding(
            padding: const EdgeInsets.all(24),
            child: Column(
              mainAxisSize: MainAxisSize.min,
              children: [
                const CircularProgressIndicator(),
                const SizedBox(height: 20),
                Text(message, textAlign: TextAlign.center),
              ],
            ),
          ),
        ),
      ),
    );
  }
}

class _AccountConnectionError extends StatelessWidget {
  const _AccountConnectionError({
    required this.error,
    required this.onRetry,
    required this.onSignOut,
  });

  final ApiException error;
  final VoidCallback onRetry;
  final Future<void> Function() onSignOut;

  @override
  Widget build(BuildContext context) {
    final colors = Theme.of(context).colorScheme;
    final isConnectionIssue = const {
      ApiFailureKind.offline,
      ApiFailureKind.timeout,
      ApiFailureKind.server,
    }.contains(error.kind);
    final title = isConnectionIssue
        ? 'We could not load your account'
        : 'Your account needs attention';
    final details = <String>[];
    final technicalDetails = error.technicalDetails;
    if (technicalDetails != null) details.add(technicalDetails);
    final statusCode = error.statusCode;
    if (statusCode != null) details.add('HTTP status: $statusCode');
    final requestId = error.requestId;
    if (requestId != null) details.add('Request ID: $requestId');

    return Scaffold(
      appBar: AppBar(title: const Text('Himikama')),
      body: SafeArea(
        child: Center(
          child: ConstrainedBox(
            constraints: const BoxConstraints(maxWidth: 480),
            child: SingleChildScrollView(
              padding: const EdgeInsets.all(24),
              child: Card(
                child: Padding(
                  padding: const EdgeInsets.all(22),
                  child: Column(
                    mainAxisSize: MainAxisSize.min,
                    crossAxisAlignment: CrossAxisAlignment.stretch,
                    children: [
                      Align(
                        child: Container(
                          width: 58,
                          height: 58,
                          decoration: BoxDecoration(
                            color: isConnectionIssue
                                ? colors.primaryContainer
                                : colors.errorContainer,
                            borderRadius: BorderRadius.circular(18),
                          ),
                          child: Icon(
                            isConnectionIssue
                                ? Icons.cloud_off_outlined
                                : Icons.manage_accounts_outlined,
                            color: isConnectionIssue
                                ? colors.primary
                                : colors.onErrorContainer,
                            size: 30,
                          ),
                        ),
                      ),
                      const SizedBox(height: 20),
                      Text(
                        title,
                        key: const Key('account-error-title'),
                        textAlign: TextAlign.center,
                        style: Theme.of(context).textTheme.headlineSmall,
                      ),
                      const SizedBox(height: 10),
                      Text(
                        error.message,
                        textAlign: TextAlign.center,
                        style: Theme.of(context).textTheme.bodyLarge?.copyWith(
                          color: colors.onSurfaceVariant,
                        ),
                      ),
                      if (isConnectionIssue) ...[
                        const SizedBox(height: 8),
                        Text(
                          'Your saved information remains safe.',
                          textAlign: TextAlign.center,
                          style: Theme.of(context).textTheme.bodySmall,
                        ),
                      ],
                      const SizedBox(height: 24),
                      FilledButton.icon(
                        onPressed: onRetry,
                        icon: const Icon(Icons.refresh),
                        label: const Text('Try again'),
                      ),
                      const SizedBox(height: 8),
                      TextButton(
                        onPressed: onSignOut,
                        child: const Text('Sign out'),
                      ),
                      if (details.isNotEmpty) ...[
                        const SizedBox(height: 8),
                        ExpansionTile(
                          key: const Key('account-error-technical-details'),
                          tilePadding: EdgeInsets.zero,
                          childrenPadding: const EdgeInsets.only(bottom: 8),
                          title: const Text('Technical details'),
                          children: [
                            Align(
                              alignment: Alignment.centerLeft,
                              child: SelectableText(
                                details.join('\n'),
                                style: Theme.of(context).textTheme.bodySmall,
                              ),
                            ),
                          ],
                        ),
                      ],
                    ],
                  ),
                ),
              ),
            ),
          ),
        ),
      ),
    );
  }
}
