import 'package:firebase_auth/firebase_auth.dart';
import 'package:flutter/material.dart';

import '../../core/auth_validators.dart';
import 'auth_helpers.dart';

class PasswordResetScreen extends StatefulWidget {
  const PasswordResetScreen({
    required this.firebaseAuth,
    super.key,
    this.initialEmail = '',
  });

  final FirebaseAuth firebaseAuth;
  final String initialEmail;

  @override
  State<PasswordResetScreen> createState() => _PasswordResetScreenState();
}

class _PasswordResetScreenState extends State<PasswordResetScreen> {
  final _formKey = GlobalKey<FormState>();
  late final TextEditingController _emailController;

  bool _submitting = false;
  bool _sent = false;
  String? _error;

  @override
  void initState() {
    super.initState();
    _emailController = TextEditingController(text: widget.initialEmail);
  }

  @override
  void dispose() {
    _emailController.dispose();
    super.dispose();
  }

  Future<void> _sendResetEmail() async {
    if (!_formKey.currentState!.validate()) return;
    setState(() {
      _submitting = true;
      _error = null;
    });

    try {
      await widget.firebaseAuth.sendPasswordResetEmail(
        email: _emailController.text.trim(),
      );
      if (!mounted) return;
      setState(() => _sent = true);
    } on FirebaseAuthException catch (error) {
      if (!mounted) return;
      if (error.code == 'user-not-found') {
        setState(() => _sent = true);
      } else {
        setState(() => _error = readableFirebaseAuthError(error));
      }
    } catch (_) {
      if (!mounted) return;
      setState(
        () => _error = 'Could not send the reset email. Please try again.',
      );
    } finally {
      if (mounted) {
        setState(() => _submitting = false);
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    if (_sent) {
      return AuthPageScaffold(
        title: 'Check your email',
        subtitle:
            'If an account exists for ${_emailController.text.trim()}, '
            'Firebase has sent password-reset instructions.',
        showBackButton: true,
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            const Icon(Icons.mark_email_read_outlined, size: 72),
            const SizedBox(height: 24),
            FilledButton(
              onPressed: () => Navigator.of(context).pop(),
              child: const Text('Return to sign in'),
            ),
          ],
        ),
      );
    }

    return AuthPageScaffold(
      title: 'Reset your password',
      subtitle: 'Enter your email address and we will send reset instructions.',
      showBackButton: true,
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
              controller: _emailController,
              keyboardType: TextInputType.emailAddress,
              autocorrect: false,
              autofillHints: const [AutofillHints.email],
              validator: AuthValidators.email,
              decoration: const InputDecoration(
                labelText: 'Email address',
                prefixIcon: Icon(Icons.email_outlined),
              ),
            ),
            const SizedBox(height: 20),
            FilledButton(
              onPressed: _submitting ? null : _sendResetEmail,
              child: _submitting
                  ? const SizedBox.square(
                      dimension: 22,
                      child: CircularProgressIndicator(strokeWidth: 2),
                    )
                  : const Text('Send reset email'),
            ),
          ],
        ),
      ),
    );
  }
}
