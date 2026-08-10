import 'package:firebase_auth/firebase_auth.dart';
import 'package:flutter/material.dart';

import '../../core/auth_validators.dart';
import '../../core/pending_registration.dart';
import '../privacy/policy_content.dart';
import '../privacy/policy_document_screen.dart';
import 'auth_helpers.dart';

class RegisterScreen extends StatefulWidget {
  const RegisterScreen({required this.firebaseAuth, super.key});

  final FirebaseAuth firebaseAuth;

  @override
  State<RegisterScreen> createState() => _RegisterScreenState();
}

class _RegisterScreenState extends State<RegisterScreen> {
  final _formKey = GlobalKey<FormState>();
  final _nameController = TextEditingController();
  final _emailController = TextEditingController();
  final _passwordController = TextEditingController();
  final _confirmPasswordController = TextEditingController();

  bool _acceptTerms = false;
  bool _acceptPrivacy = false;
  bool _submitting = false;
  String? _error;

  @override
  void dispose() {
    _nameController.dispose();
    _emailController.dispose();
    _passwordController.dispose();
    _confirmPasswordController.dispose();
    super.dispose();
  }

  Future<void> _createAccount() async {
    FocusScope.of(context).unfocus();
    final formValid = _formKey.currentState!.validate();
    if (!formValid) return;
    if (!_acceptTerms || !_acceptPrivacy) {
      setState(
        () => _error =
            'Accept the Terms of Use and Privacy Policy to create an account.',
      );
      return;
    }

    setState(() {
      _submitting = true;
      _error = null;
    });

    UserCredential credential;
    try {
      credential = await widget.firebaseAuth.createUserWithEmailAndPassword(
        email: _emailController.text.trim(),
        password: _passwordController.text,
      );
    } on FirebaseAuthException catch (error) {
      if (!mounted) return;
      setState(() {
        _submitting = false;
        _error = readableFirebaseAuthError(error);
      });
      return;
    } catch (_) {
      if (!mounted) return;
      setState(() {
        _submitting = false;
        _error = 'Could not create the account. Please try again.';
      });
      return;
    }

    final user = credential.user;
    if (user == null) {
      if (!mounted) return;
      setState(() {
        _submitting = false;
        _error = 'Firebase did not return the new account.';
      });
      return;
    }

    final displayName = _nameController.text.trim();
    PendingRegistrationStore.save(
      PendingRegistration(
        uid: user.uid,
        displayName: displayName,
        acceptedTerms: _acceptTerms,
        acceptedPrivacyPolicy: _acceptPrivacy,
      ),
    );

    try {
      await user.updateDisplayName(displayName);
      await user.sendEmailVerification();
    } on FirebaseAuthException {
      // The account exists and the verification screen can resend the email.
    }

    if (!mounted) return;
    Navigator.of(context).popUntil((route) => route.isFirst);
  }

  @override
  Widget build(BuildContext context) {
    return AuthPageScaffold(
      title: 'Create your account',
      subtitle:
          'Your assessments are private and stored under your verified '
          'Firebase identity.',
      showBackButton: true,
      child: Form(
        key: _formKey,
        child: AutofillGroup(
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
                textInputAction: TextInputAction.next,
                autofillHints: const [AutofillHints.name],
                validator: AuthValidators.displayName,
                decoration: const InputDecoration(
                  labelText: 'Full name',
                  prefixIcon: Icon(Icons.person_outline),
                ),
              ),
              const SizedBox(height: 16),
              TextFormField(
                controller: _emailController,
                keyboardType: TextInputType.emailAddress,
                textInputAction: TextInputAction.next,
                autocorrect: false,
                autofillHints: const [AutofillHints.email],
                validator: AuthValidators.email,
                decoration: const InputDecoration(
                  labelText: 'Email address',
                  prefixIcon: Icon(Icons.email_outlined),
                ),
              ),
              const SizedBox(height: 16),
              PasswordField(
                controller: _passwordController,
                label: 'Password',
                validator: AuthValidators.password,
                textInputAction: TextInputAction.next,
              ),
              const SizedBox(height: 8),
              Text(
                'Use at least 12 characters with uppercase, lowercase, '
                'a number, and a symbol.',
                style: Theme.of(context).textTheme.bodySmall,
              ),
              const SizedBox(height: 16),
              PasswordField(
                controller: _confirmPasswordController,
                label: 'Confirm password',
                validator: (value) => AuthValidators.confirmedPassword(
                  value,
                  _passwordController.text,
                ),
                textInputAction: TextInputAction.done,
              ),
              const SizedBox(height: 16),
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
                      onPressed: () => Navigator.of(context).push(
                        MaterialPageRoute<void>(
                          builder: (_) => const PolicyDocumentScreen(
                            type: PolicyDocumentType.terms,
                          ),
                        ),
                      ),
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
                    : (value) =>
                          setState(() => _acceptPrivacy = value ?? false),
                title: Wrap(
                  crossAxisAlignment: WrapCrossAlignment.center,
                  children: [
                    const Text('I accept the '),
                    TextButton(
                      onPressed: () => Navigator.of(context).push(
                        MaterialPageRoute<void>(
                          builder: (_) => const PolicyDocumentScreen(
                            type: PolicyDocumentType.privacy,
                          ),
                        ),
                      ),
                      child: const Text('Privacy Policy'),
                    ),
                  ],
                ),
              ),
              const SizedBox(height: 16),
              FilledButton(
                onPressed: _submitting ? null : _createAccount,
                child: _submitting
                    ? const SizedBox.square(
                        dimension: 22,
                        child: CircularProgressIndicator(strokeWidth: 2),
                      )
                    : const Text('Create account'),
              ),
            ],
          ),
        ),
      ),
    );
  }
}
