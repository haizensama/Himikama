import 'package:firebase_auth/firebase_auth.dart';
import 'package:flutter/material.dart';

import '../../core/api_service.dart';
import '../auth/auth_helpers.dart';
import 'policy_content.dart';
import 'policy_document_screen.dart';

class PolicyReviewScreen extends StatefulWidget {
  const PolicyReviewScreen({
    required this.firebaseAuth,
    required this.apiService,
    required this.onAccepted,
    super.key,
  });

  final FirebaseAuth firebaseAuth;
  final ApiService apiService;
  final VoidCallback onAccepted;

  @override
  State<PolicyReviewScreen> createState() => _PolicyReviewScreenState();
}

class _PolicyReviewScreenState extends State<PolicyReviewScreen> {
  bool _termsAccepted = false;
  bool _privacyAccepted = false;
  bool _submitting = false;
  String? _error;

  Future<void> _accept() async {
    if (!_termsAccepted || !_privacyAccepted) {
      setState(() => _error = 'Review and accept both documents to continue.');
      return;
    }
    setState(() {
      _submitting = true;
      _error = null;
    });
    try {
      await widget.apiService.acceptCurrentPolicies();
      widget.onAccepted();
    } on ApiException catch (error) {
      if (mounted) setState(() => _error = error.message);
    } finally {
      if (mounted) setState(() => _submitting = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return AuthPageScaffold(
      title: 'Review updated policies',
      subtitle:
          'Himikama now explains assessment retention, AI processing, your '
          'privacy controls, and the seven-day account recovery period.',
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          if (_error case final error?) ...[
            AuthErrorBanner(message: error),
            const SizedBox(height: 14),
          ],
          _PolicyLink(
            title: 'Terms of Use v$himikamaTermsVersion',
            onTap: () => _open(PolicyDocumentType.terms),
          ),
          const SizedBox(height: 10),
          _PolicyLink(
            title: 'Privacy Notice v$himikamaPrivacyVersion',
            onTap: () => _open(PolicyDocumentType.privacy),
          ),
          const SizedBox(height: 10),
          SelectableText('Privacy contact: $himikamaPrivacyContact'),
          const SizedBox(height: 16),
          CheckboxListTile(
            contentPadding: EdgeInsets.zero,
            controlAffinity: ListTileControlAffinity.leading,
            value: _termsAccepted,
            onChanged: _submitting
                ? null
                : (value) => setState(() => _termsAccepted = value ?? false),
            title: const Text('I accept the current Terms of Use'),
          ),
          CheckboxListTile(
            contentPadding: EdgeInsets.zero,
            controlAffinity: ListTileControlAffinity.leading,
            value: _privacyAccepted,
            onChanged: _submitting
                ? null
                : (value) => setState(() => _privacyAccepted = value ?? false),
            title: const Text('I accept the current Privacy Notice'),
          ),
          const SizedBox(height: 14),
          FilledButton(
            key: const Key('accept-current-policies'),
            onPressed: _submitting ? null : _accept,
            child: _submitting
                ? const SizedBox.square(
                    dimension: 20,
                    child: CircularProgressIndicator(strokeWidth: 2),
                  )
                : const Text('Accept and continue'),
          ),
          TextButton(
            onPressed: _submitting ? null : widget.firebaseAuth.signOut,
            child: const Text('Sign out'),
          ),
        ],
      ),
    );
  }

  Future<void> _open(PolicyDocumentType type) {
    return Navigator.of(context).push(
      MaterialPageRoute<void>(builder: (_) => PolicyDocumentScreen(type: type)),
    );
  }
}

class _PolicyLink extends StatelessWidget {
  const _PolicyLink({required this.title, required this.onTap});

  final String title;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return OutlinedButton.icon(
      onPressed: onTap,
      icon: const Icon(Icons.description_outlined),
      label: Text(title),
    );
  }
}
