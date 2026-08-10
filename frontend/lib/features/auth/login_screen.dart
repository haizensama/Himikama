import 'package:firebase_auth/firebase_auth.dart';
import 'package:flutter/material.dart';

import '../../core/auth_validators.dart';
import 'auth_helpers.dart';
import 'password_reset_screen.dart';
import 'register_screen.dart';

class LoginScreen extends StatefulWidget {
  const LoginScreen({
    required this.firebaseAuth,
    super.key,
    this.showBackButton = false,
  });

  final FirebaseAuth firebaseAuth;
  final bool showBackButton;

  @override
  State<LoginScreen> createState() => _LoginScreenState();
}

class _LoginScreenState extends State<LoginScreen> {
  final _formKey = GlobalKey<FormState>();
  final _emailController = TextEditingController();
  final _passwordController = TextEditingController();

  bool _submitting = false;
  String? _error;

  @override
  void dispose() {
    _emailController.dispose();
    _passwordController.dispose();
    super.dispose();
  }

  Future<void> _signIn() async {
    FocusScope.of(context).unfocus();
    if (!_formKey.currentState!.validate()) return;

    setState(() {
      _submitting = true;
      _error = null;
    });

    try {
      await widget.firebaseAuth.signInWithEmailAndPassword(
        email: _emailController.text.trim(),
        password: _passwordController.text,
      );
      if (!mounted) return;
      Navigator.of(context).popUntil((route) => route.isFirst);
    } on FirebaseAuthException catch (error) {
      if (!mounted) return;
      setState(() => _error = readableFirebaseAuthError(error));
    } catch (_) {
      if (!mounted) return;
      setState(() => _error = 'Could not sign in. Please try again.');
    } finally {
      if (mounted) {
        setState(() => _submitting = false);
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    return AuthPageScaffold(
      title: 'Welcome to Himikama',
      subtitle:
          'Sign in to access your private Fundamental Rights assessments.',
      showBackButton: widget.showBackButton,
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
                validator: AuthValidators.requiredPassword,
                textInputAction: TextInputAction.done,
                onFieldSubmitted: (_) => _submitting ? null : _signIn(),
              ),
              Align(
                alignment: Alignment.centerRight,
                child: TextButton(
                  onPressed: _submitting
                      ? null
                      : () {
                          Navigator.of(context).push(
                            MaterialPageRoute<void>(
                              builder: (_) => PasswordResetScreen(
                                firebaseAuth: widget.firebaseAuth,
                                initialEmail: _emailController.text.trim(),
                              ),
                            ),
                          );
                        },
                  child: const Text('Forgot password?'),
                ),
              ),
              const SizedBox(height: 8),
              FilledButton(
                onPressed: _submitting ? null : _signIn,
                child: _submitting
                    ? const SizedBox.square(
                        dimension: 22,
                        child: CircularProgressIndicator(strokeWidth: 2),
                      )
                    : const Text('Sign in'),
              ),
              const SizedBox(height: 20),
              Row(
                mainAxisAlignment: MainAxisAlignment.center,
                children: [
                  const Text('New to Himikama?'),
                  TextButton(
                    onPressed: _submitting
                        ? null
                        : () {
                            Navigator.of(context).push(
                              MaterialPageRoute<void>(
                                builder: (_) => RegisterScreen(
                                  firebaseAuth: widget.firebaseAuth,
                                ),
                              ),
                            );
                          },
                    child: const Text('Create account'),
                  ),
                ],
              ),
              const SizedBox(height: 16),
              Text(
                'Himikama provides legal information and triage support. '
                'It is not a substitute for advice from a qualified lawyer.',
                textAlign: TextAlign.center,
                style: Theme.of(context).textTheme.bodySmall,
              ),
            ],
          ),
        ),
      ),
    );
  }
}
