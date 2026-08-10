import 'package:firebase_auth/firebase_auth.dart';
import 'package:flutter/material.dart';

import '../../core/api_service.dart';
import '../../core/auth_validators.dart';
import '../../core/pending_attempt_store.dart';
import '../auth/auth_helpers.dart';
import 'policy_content.dart';
import 'policy_document_screen.dart';

class AccountSettingsScreen extends StatefulWidget {
  const AccountSettingsScreen({
    required this.firebaseAuth,
    required this.apiService,
    required this.user,
    required this.profile,
    required this.pendingAttemptStore,
    super.key,
  });

  final FirebaseAuth firebaseAuth;
  final ApiService apiService;
  final User user;
  final UserProfile profile;
  final PendingAttemptStore pendingAttemptStore;

  @override
  State<AccountSettingsScreen> createState() => _AccountSettingsScreenState();
}

class _AccountSettingsScreenState extends State<AccountSettingsScreen> {
  late UserProfile _profile;
  bool _busy = false;
  String? _message;
  bool _messageIsError = false;

  @override
  void initState() {
    super.initState();
    _profile = widget.profile;
  }

  void _showMessage(String message, {bool error = false}) {
    if (!mounted) return;
    setState(() {
      _message = message;
      _messageIsError = error;
    });
  }

  Future<void> _editName() async {
    final value = await showDialog<String>(
      context: context,
      builder: (_) => _DisplayNameDialog(initialName: _profile.displayName),
    );
    if (value == null || value == _profile.displayName) return;

    setState(() => _busy = true);
    try {
      final updated = await widget.apiService.updateProfile(displayName: value);
      await widget.user.updateDisplayName(value);
      if (mounted) setState(() => _profile = updated);
      _showMessage('Your display name was updated.');
    } on ApiException catch (error) {
      _showMessage(error.message, error: true);
    } on FirebaseAuthException catch (error) {
      _showMessage(readableFirebaseAuthError(error), error: true);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _sendPasswordReset() async {
    final email = widget.user.email;
    if (email == null || email.isEmpty) {
      _showMessage(
        'No email address is available for this account.',
        error: true,
      );
      return;
    }
    setState(() => _busy = true);
    try {
      await widget.firebaseAuth.sendPasswordResetEmail(email: email);
      _showMessage('Password-reset instructions were sent to $email.');
    } on FirebaseAuthException catch (error) {
      _showMessage(readableFirebaseAuthError(error), error: true);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _withdrawConsent() async {
    final confirmed = await _confirm(
      title: 'Withdraw assessment consent?',
      message:
          'You will not be able to start or retry assessments until you '
          'consent again. Existing history will remain until you delete it.',
      action: 'Withdraw consent',
    );
    if (!confirmed) return;
    setState(() => _busy = true);
    try {
      final updated = await widget.apiService.withdrawAssessmentConsent();
      if (mounted) setState(() => _profile = updated);
      _showMessage('Consent for future assessments was withdrawn.');
    } on ApiException catch (error) {
      _showMessage(error.message, error: true);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _clearHistory() async {
    final confirmed = await _confirm(
      title: 'Delete all assessment history?',
      message:
          'Every saved assessment, result, and active processing job will be '
          'permanently deleted. This cannot be undone.',
      action: 'Delete all history',
      destructive: true,
    );
    if (!confirmed) return;
    setState(() => _busy = true);
    try {
      await widget.apiService.clearHistory();
      await _clearPendingReference();
      _showMessage('All assessment history was deleted.');
    } on ApiException catch (error) {
      _showMessage(error.message, error: true);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _scheduleDeletion() async {
    final password = await _requestDeletionPassword();
    if (password == null) return;
    setState(() => _busy = true);
    try {
      final email = widget.user.email;
      if (email == null || email.isEmpty) {
        throw FirebaseAuthException(
          code: 'invalid-email',
          message: 'The account email is unavailable.',
        );
      }
      final credential = EmailAuthProvider.credential(
        email: email,
        password: password,
      );
      await widget.user.reauthenticateWithCredential(credential);
      await widget.apiService.scheduleAccountDeletion();
      await _clearPendingReference();
      if (!mounted) return;
      Navigator.of(context).pop();
    } on FirebaseAuthException catch (error) {
      _showMessage(readableFirebaseAuthError(error), error: true);
    } on ApiException catch (error) {
      _showMessage(error.message, error: true);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _clearPendingReference() async {
    final pending = await widget.pendingAttemptStore.readForUser(
      widget.user.uid,
    );
    if (pending != null) {
      await widget.pendingAttemptStore.clear(
        ownerUid: widget.user.uid,
        attemptId: pending.attemptId,
      );
    }
  }

  Future<bool> _confirm({
    required String title,
    required String message,
    required String action,
    bool destructive = false,
  }) async {
    return await showDialog<bool>(
          context: context,
          builder: (dialogContext) => AlertDialog(
            title: Text(title),
            content: Text(message),
            actions: [
              TextButton(
                onPressed: () => Navigator.of(dialogContext).pop(false),
                child: const Text('Cancel'),
              ),
              FilledButton(
                style: destructive
                    ? FilledButton.styleFrom(
                        backgroundColor: Theme.of(context).colorScheme.error,
                      )
                    : null,
                onPressed: () => Navigator.of(dialogContext).pop(true),
                child: Text(action),
              ),
            ],
          ),
        ) ??
        false;
  }

  Future<String?> _requestDeletionPassword() async {
    return showAccountDeletionConfirmationDialog(context);
  }

  @override
  Widget build(BuildContext context) {
    final colors = Theme.of(context).colorScheme;
    return Scaffold(
      appBar: AppBar(title: const Text('Account & Privacy')),
      body: SafeArea(
        child: ListView(
          padding: const EdgeInsets.fromLTRB(20, 12, 20, 32),
          children: [
            if (_message case final message?) ...[
              Container(
                key: const Key('settings-message'),
                padding: const EdgeInsets.all(14),
                decoration: BoxDecoration(
                  color: _messageIsError
                      ? colors.errorContainer
                      : colors.primaryContainer,
                  borderRadius: BorderRadius.circular(12),
                ),
                child: Text(message),
              ),
              const SizedBox(height: 14),
            ],
            _SectionCard(
              title: 'Account',
              children: [
                ListTile(
                  leading: const Icon(Icons.person_outline),
                  title: Text(_profile.displayName),
                  subtitle: Text(_profile.email),
                  trailing: const Icon(Icons.edit_outlined),
                  onTap: _busy ? null : _editName,
                ),
                ListTile(
                  leading: const Icon(Icons.password_outlined),
                  title: const Text('Reset password'),
                  subtitle: const Text('Send a secure reset link by email'),
                  onTap: _busy ? null : _sendPasswordReset,
                ),
              ],
            ),
            const SizedBox(height: 14),
            _SectionCard(
              title: 'Privacy and consent',
              children: [
                ListTile(
                  leading: const Icon(Icons.privacy_tip_outlined),
                  title: const Text('Privacy Notice'),
                  subtitle: const Text(
                    'Data use, AI processing, retention, and your choices',
                  ),
                  onTap: () => _openPolicy(PolicyDocumentType.privacy),
                ),
                ListTile(
                  leading: const Icon(Icons.description_outlined),
                  title: const Text('Terms of Use'),
                  onTap: () => _openPolicy(PolicyDocumentType.terms),
                ),
                ListTile(
                  leading: Icon(
                    _profile.assessmentConsentCurrent
                        ? Icons.check_circle_outline
                        : Icons.pause_circle_outline,
                  ),
                  title: const Text('Assessment data processing'),
                  subtitle: Text(
                    _profile.assessmentConsentCurrent
                        ? 'Consent is active for new assessments'
                        : 'Consent is not active',
                  ),
                  trailing: _profile.assessmentConsentCurrent
                      ? TextButton(
                          onPressed: _busy ? null : _withdrawConsent,
                          child: const Text('Withdraw'),
                        )
                      : null,
                ),
                const ListTile(
                  leading: Icon(Icons.email_outlined),
                  title: Text('Privacy contact'),
                  subtitle: SelectableText(himikamaPrivacyContact),
                ),
              ],
            ),
            const SizedBox(height: 14),
            _SectionCard(
              title: 'Your assessment data',
              children: [
                ListTile(
                  leading: const Icon(Icons.delete_sweep_outlined),
                  title: const Text('Delete all assessment history'),
                  subtitle: const Text(
                    'Permanently erase every saved assessment and active job',
                  ),
                  onTap: _busy ? null : _clearHistory,
                ),
              ],
            ),
            const SizedBox(height: 14),
            Card(
              color: colors.errorContainer,
              child: Padding(
                padding: const EdgeInsets.all(16),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                    Text(
                      'Delete account',
                      style: Theme.of(context).textTheme.titleMedium?.copyWith(
                        fontWeight: FontWeight.w800,
                        color: colors.onErrorContainer,
                      ),
                    ),
                    const SizedBox(height: 8),
                    Text(
                      'Deletion pauses your account immediately. You have '
                      'seven days to sign in and recover it before permanent '
                      'erasure.',
                      style: TextStyle(color: colors.onErrorContainer),
                    ),
                    const SizedBox(height: 14),
                    FilledButton.icon(
                      key: const Key('schedule-account-deletion'),
                      style: FilledButton.styleFrom(
                        backgroundColor: colors.error,
                        foregroundColor: colors.onError,
                      ),
                      onPressed: _busy ? null : _scheduleDeletion,
                      icon: const Icon(Icons.delete_forever_outlined),
                      label: const Text('Schedule account deletion'),
                    ),
                  ],
                ),
              ),
            ),
            if (_busy) ...[
              const SizedBox(height: 18),
              const Center(child: CircularProgressIndicator()),
            ],
          ],
        ),
      ),
    );
  }

  Future<void> _openPolicy(PolicyDocumentType type) {
    return Navigator.of(context).push(
      MaterialPageRoute<void>(builder: (_) => PolicyDocumentScreen(type: type)),
    );
  }
}

@visibleForTesting
Future<String?> showAccountDeletionConfirmationDialog(BuildContext context) {
  return showDialog<String>(
    context: context,
    builder: (_) => const _DeletionPasswordDialog(),
  );
}

class _DisplayNameDialog extends StatefulWidget {
  const _DisplayNameDialog({required this.initialName});

  final String initialName;

  @override
  State<_DisplayNameDialog> createState() => _DisplayNameDialogState();
}

class _DisplayNameDialogState extends State<_DisplayNameDialog> {
  final _formKey = GlobalKey<FormState>();
  late final TextEditingController _controller;

  @override
  void initState() {
    super.initState();
    _controller = TextEditingController(text: widget.initialName);
  }

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  void _save() {
    if (_formKey.currentState!.validate()) {
      Navigator.of(context).pop(_controller.text.trim());
    }
  }

  @override
  Widget build(BuildContext context) {
    return AlertDialog(
      title: const Text('Update display name'),
      content: Form(
        key: _formKey,
        child: TextFormField(
          key: const Key('settings-display-name'),
          controller: _controller,
          textCapitalization: TextCapitalization.words,
          autofocus: true,
          validator: AuthValidators.displayName,
          onFieldSubmitted: (_) => _save(),
        ),
      ),
      actions: [
        TextButton(
          onPressed: () => Navigator.of(context).pop(),
          child: const Text('Cancel'),
        ),
        FilledButton(onPressed: _save, child: const Text('Save')),
      ],
    );
  }
}

class _DeletionPasswordDialog extends StatefulWidget {
  const _DeletionPasswordDialog();

  @override
  State<_DeletionPasswordDialog> createState() =>
      _DeletionPasswordDialogState();
}

class _DeletionPasswordDialogState extends State<_DeletionPasswordDialog> {
  final _controller = TextEditingController();
  bool _confirmed = false;

  bool get _canSubmit => _confirmed && _controller.text.isNotEmpty;

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return AlertDialog(
      title: const Text('Schedule account deletion'),
      content: SingleChildScrollView(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const Text(
              'Your account will be paused now and permanently deleted '
              'after seven days. Sign in before the deadline to recover it.',
            ),
            const SizedBox(height: 16),
            TextField(
              key: const Key('deletion-password'),
              controller: _controller,
              obscureText: true,
              autocorrect: false,
              onChanged: (_) => setState(() {}),
              decoration: const InputDecoration(
                labelText: 'Current password',
                prefixIcon: Icon(Icons.lock_outline),
              ),
            ),
            const SizedBox(height: 10),
            CheckboxListTile(
              key: const Key('confirm-seven-day-deletion'),
              contentPadding: EdgeInsets.zero,
              controlAffinity: ListTileControlAffinity.leading,
              value: _confirmed,
              onChanged: (value) => setState(() => _confirmed = value ?? false),
              title: const Text(
                'I understand deletion becomes permanent after seven days.',
              ),
            ),
          ],
        ),
      ),
      actions: [
        TextButton(
          onPressed: () => Navigator.of(context).pop(),
          child: const Text('Cancel'),
        ),
        FilledButton(
          key: const Key('confirm-account-deletion'),
          style: FilledButton.styleFrom(
            backgroundColor: Theme.of(context).colorScheme.error,
          ),
          onPressed: _canSubmit
              ? () => Navigator.of(context).pop(_controller.text)
              : null,
          child: const Text('Schedule deletion'),
        ),
      ],
    );
  }
}

class _SectionCard extends StatelessWidget {
  const _SectionCard({required this.title, required this.children});

  final String title;
  final List<Widget> children;

  @override
  Widget build(BuildContext context) {
    return Card(
      child: Padding(
        padding: const EdgeInsets.fromLTRB(8, 14, 8, 8),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Padding(
              padding: const EdgeInsets.symmetric(horizontal: 12),
              child: Text(
                title,
                style: Theme.of(
                  context,
                ).textTheme.titleMedium?.copyWith(fontWeight: FontWeight.w800),
              ),
            ),
            const SizedBox(height: 6),
            ...children,
          ],
        ),
      ),
    );
  }
}
