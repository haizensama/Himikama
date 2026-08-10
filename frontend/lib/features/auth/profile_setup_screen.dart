import 'package:firebase_auth/firebase_auth.dart';
import 'package:flutter/material.dart';

import '../../core/api_service.dart';
import '../../core/auth_validators.dart';
import '../privacy/policy_content.dart';
import '../privacy/policy_document_screen.dart';
import 'auth_helpers.dart';

class ProfileSetupScreen extends StatefulWidget {
  const ProfileSetupScreen({
    required this.firebaseAuth,
    required this.apiService,
    required this.user,
    required this.initialDisplayName,
    required this.initialPoliciesAccepted,
    required this.onCompleted,
    super.key,
  });

  final FirebaseAuth firebaseAuth;
  final ApiService apiService;
  final User user;
  final String initialDisplayName;
  final bool initialPoliciesAccepted;
  final VoidCallback onCompleted;

  @override
  State<ProfileSetupScreen> createState() => _ProfileSetupScreenState();
}

class _ProfileSetupScreenState extends State<ProfileSetupScreen> {
  final _formKey = GlobalKey<FormState>();
  late final TextEditingController _nameController;

  late bool _acceptTerms;
  late bool _acceptPrivacy;
  bool _submitting = false;
  String? _error;

  @override
  void initState() {
    super.initState();
    _nameController = TextEditingController(text: widget.initialDisplayName);
    _acceptTerms = widget.initialPoliciesAccepted;
    _acceptPrivacy = widget.initialPoliciesAccepted;
  }

  @override
  void dispose() {
    _nameController.dispose();
    super.dispose();
  }

  Future<void> _completeSetup() async {
    if (!_formKey.currentState!.validate()) return;
    if (!_acceptTerms || !_acceptPrivacy) {
      setState(
        () =>
            _error = 'Accept the Terms of Use and Privacy Policy to continue.',
      );
      return;
    }

    setState(() {
      _submitting = true;
      _error = null;
    });

    try {
      final displayName = _nameController.text.trim();
      await widget.user.updateDisplayName(displayName);
      await widget.apiService.createProfile(
        displayName: displayName,
        acceptTerms: true,
        acceptPrivacyPolicy: true,
      );
      widget.onCompleted();
    } on ApiException catch (error) {
      if (!mounted) return;
      setState(() => _error = error.message);
    } on FirebaseAuthException catch (error) {
      if (!mounted) return;
      setState(() => _error = readableFirebaseAuthError(error));
    } finally {
      if (mounted) {
        setState(() => _submitting = false);
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    return AuthPageScaffold(
      title: 'Complete account setup',
      subtitle:
          'Your email is verified. Finish your Himikama profile before '
          'starting an assessment.',
      child: Form(
        key: _formKey,
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            if (_error case final error?) ...[
              AuthErrorBanner(message: error),
              const SizedBox(height: 16),
            ],
            TextFormField(
              controller: _nameController,
              textCapitalization: TextCapitalization.words,
              validator: AuthValidators.displayName,
              decoration: const InputDecoration(
                labelText: 'Full name',
                prefixIcon: Icon(Icons.person_outline),
              ),
            ),
            const SizedBox(height: 12),
            CheckboxListTile(
              contentPadding: EdgeInsets.zero,
              controlAffinity: ListTileControlAffinity.leading,
              value: _acceptTerms,
              onChanged: _submitting
                  ? null
                  : (value) => setState(() => _acceptTerms = value ?? false),
              title: Wrap(
                crossAxisAlignment: WrapCrossAlignment.center,
                children: [
                  const Text('I accept the '),
                  TextButton(
                    onPressed: () => _openPolicy(PolicyDocumentType.terms),
                    child: const Text('Terms of Use'),
                  ),
                ],
              ),
            ),
            CheckboxListTile(
              contentPadding: EdgeInsets.zero,
              controlAffinity: ListTileControlAffinity.leading,
              value: _acceptPrivacy,
              onChanged: _submitting
                  ? null
                  : (value) => setState(() => _acceptPrivacy = value ?? false),
              title: Wrap(
                crossAxisAlignment: WrapCrossAlignment.center,
                children: [
                  const Text('I accept the '),
                  TextButton(
                    onPressed: () => _openPolicy(PolicyDocumentType.privacy),
                    child: const Text('Privacy Notice'),
                  ),
                ],
              ),
            ),
            const SizedBox(height: 16),
            FilledButton(
              onPressed: _submitting ? null : _completeSetup,
              child: _submitting
                  ? const SizedBox.square(
                      dimension: 22,
                      child: CircularProgressIndicator(strokeWidth: 2),
                    )
                  : const Text('Complete setup'),
            ),
            const SizedBox(height: 8),
            TextButton(
              onPressed: _submitting ? null : widget.firebaseAuth.signOut,
              child: const Text('Sign out'),
            ),
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
