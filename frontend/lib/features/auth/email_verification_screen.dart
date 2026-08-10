import 'dart:async';

import 'package:firebase_auth/firebase_auth.dart';
import 'package:flutter/material.dart';

import 'auth_helpers.dart';

class EmailVerificationScreen extends StatefulWidget {
  const EmailVerificationScreen({
    required this.firebaseAuth,
    required this.user,
    required this.onVerified,
    super.key,
  });

  final FirebaseAuth firebaseAuth;
  final User user;
  final VoidCallback onVerified;

  @override
  State<EmailVerificationScreen> createState() =>
      _EmailVerificationScreenState();
}

class _EmailVerificationScreenState extends State<EmailVerificationScreen> {
  Timer? _verificationTimer;
  Timer? _cooldownTimer;

  bool _checking = false;
  bool _resending = false;
  int _resendSeconds = 0;
  String? _error;
  String? _message;

  @override
  void initState() {
    super.initState();
    _verificationTimer = Timer.periodic(
      const Duration(seconds: 5),
      (_) => _checkVerification(silent: true),
    );
  }

  @override
  void dispose() {
    _verificationTimer?.cancel();
    _cooldownTimer?.cancel();
    super.dispose();
  }

  Future<void> _checkVerification({bool silent = false}) async {
    if (_checking) return;
    setState(() {
      _checking = true;
      if (!silent) {
        _error = null;
        _message = null;
      }
    });

    try {
      await widget.user.reload();
      final refreshedUser = widget.firebaseAuth.currentUser;
      if (refreshedUser?.emailVerified == true) {
        await refreshedUser!.getIdToken(true);
        _verificationTimer?.cancel();
        widget.onVerified();
        return;
      }
      if (!silent && mounted) {
        setState(
          () => _message =
              'Your email is not verified yet. Open the link in your inbox, '
              'then try again.',
        );
      }
    } on FirebaseAuthException catch (error) {
      if (!silent && mounted) {
        setState(() => _error = readableFirebaseAuthError(error));
      }
    } finally {
      if (mounted) {
        setState(() => _checking = false);
      }
    }
  }

  Future<void> _resendVerification() async {
    if (_resending || _resendSeconds > 0) return;
    setState(() {
      _resending = true;
      _error = null;
      _message = null;
    });

    try {
      await widget.user.sendEmailVerification();
      if (!mounted) return;
      setState(() {
        _message = 'A new verification email was sent.';
        _resendSeconds = 60;
      });
      _cooldownTimer?.cancel();
      _cooldownTimer = Timer.periodic(const Duration(seconds: 1), (timer) {
        if (!mounted) {
          timer.cancel();
          return;
        }
        if (_resendSeconds <= 1) {
          timer.cancel();
          setState(() => _resendSeconds = 0);
        } else {
          setState(() => _resendSeconds--);
        }
      });
    } on FirebaseAuthException catch (error) {
      if (!mounted) return;
      setState(() => _error = readableFirebaseAuthError(error));
    } finally {
      if (mounted) {
        setState(() => _resending = false);
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    return AuthPageScaffold(
      title: 'Verify your email',
      subtitle:
          'We sent a verification link to '
          '${widget.user.email ?? 'your email address'}.',
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Icon(
            Icons.mark_email_unread_outlined,
            size: 80,
            color: Theme.of(context).colorScheme.primary,
          ),
          const SizedBox(height: 24),
          const Text(
            'Open the email and tap the verification link. This screen checks '
            'automatically every few seconds.',
            textAlign: TextAlign.center,
          ),
          if (_error case final error?) ...[
            const SizedBox(height: 16),
            AuthErrorBanner(message: error),
          ],
          if (_message case final message?) ...[
            const SizedBox(height: 16),
            Text(
              message,
              textAlign: TextAlign.center,
              style: TextStyle(
                color: Theme.of(context).colorScheme.primary,
                fontWeight: FontWeight.w600,
              ),
            ),
          ],
          const SizedBox(height: 24),
          FilledButton(
            onPressed: _checking
                ? null
                : () => _checkVerification(silent: false),
            child: _checking
                ? const SizedBox.square(
                    dimension: 22,
                    child: CircularProgressIndicator(strokeWidth: 2),
                  )
                : const Text('I have verified my email'),
          ),
          const SizedBox(height: 8),
          TextButton(
            onPressed: _resending || _resendSeconds > 0
                ? null
                : _resendVerification,
            child: Text(
              _resendSeconds > 0
                  ? 'Resend in $_resendSeconds seconds'
                  : 'Resend verification email',
            ),
          ),
          TextButton(
            onPressed: widget.firebaseAuth.signOut,
            child: const Text('Use a different account'),
          ),
        ],
      ),
    );
  }
}
