import 'package:firebase_auth/firebase_auth.dart';
import 'package:flutter/material.dart';

import '../../core/api_service.dart';
import '../auth/auth_helpers.dart';
import 'policy_content.dart';

class AccountDeletionPendingScreen extends StatefulWidget {
  const AccountDeletionPendingScreen({
    required this.firebaseAuth,
    required this.apiService,
    required this.profile,
    required this.onRecovered,
    super.key,
  });

  final FirebaseAuth firebaseAuth;
  final ApiService apiService;
  final UserProfile profile;
  final VoidCallback onRecovered;

  @override
  State<AccountDeletionPendingScreen> createState() =>
      _AccountDeletionPendingScreenState();
}

class _AccountDeletionPendingScreenState
    extends State<AccountDeletionPendingScreen> {
  bool _recovering = false;
  String? _error;

  bool get _canRecover {
    final deadline = widget.profile.deletionEffectiveAt;
    return widget.profile.accountStatus == 'deletion_scheduled' &&
        deadline != null &&
        DateTime.now().isBefore(deadline);
  }

  Future<void> _recover() async {
    setState(() {
      _recovering = true;
      _error = null;
    });
    try {
      await widget.apiService.cancelAccountDeletion();
      widget.onRecovered();
    } on ApiException catch (error) {
      if (mounted) setState(() => _error = error.message);
    } finally {
      if (mounted) setState(() => _recovering = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final deadline = widget.profile.deletionEffectiveAt;
    return AuthPageScaffold(
      title: _canRecover
          ? 'Account deletion is scheduled'
          : 'Account deletion is being completed',
      subtitle: _canRecover
          ? 'Your account is paused. You can restore it before the deadline.'
          : 'The seven-day recovery period has ended. Account data is being '
                'permanently erased.',
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Card(
            color: Theme.of(context).colorScheme.errorContainer,
            child: Padding(
              padding: const EdgeInsets.all(16),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  const Text(
                    'Deletion deadline',
                    style: TextStyle(fontWeight: FontWeight.w800),
                  ),
                  const SizedBox(height: 6),
                  Text(
                    deadline == null
                        ? 'Deadline unavailable'
                        : _formatDeadline(deadline),
                  ),
                  const SizedBox(height: 10),
                  const Text(
                    'New assessments are blocked. Any analysis that was still '
                    'processing was safely stopped.',
                  ),
                ],
              ),
            ),
          ),
          if (_error case final error?) ...[
            const SizedBox(height: 14),
            AuthErrorBanner(message: error),
          ],
          const SizedBox(height: 18),
          if (_canRecover)
            FilledButton.icon(
              key: const Key('recover-scheduled-account'),
              onPressed: _recovering ? null : _recover,
              icon: _recovering
                  ? const SizedBox.square(
                      dimension: 18,
                      child: CircularProgressIndicator(strokeWidth: 2),
                    )
                  : const Icon(Icons.restore),
              label: const Text('Keep my account'),
            ),
          const SizedBox(height: 8),
          OutlinedButton.icon(
            onPressed: _recovering ? null : widget.firebaseAuth.signOut,
            icon: const Icon(Icons.logout),
            label: const Text('Sign out'),
          ),
          const SizedBox(height: 20),
          SelectableText(
            'For deletion or recovery questions, contact '
            '$himikamaPrivacyContact.',
            textAlign: TextAlign.center,
          ),
        ],
      ),
    );
  }
}

String _formatDeadline(DateTime value) {
  final local = value.toLocal();
  final day = local.day.toString().padLeft(2, '0');
  final month = local.month.toString().padLeft(2, '0');
  final hour = local.hour.toString().padLeft(2, '0');
  final minute = local.minute.toString().padLeft(2, '0');
  return '$day/$month/${local.year} at $hour:$minute';
}
