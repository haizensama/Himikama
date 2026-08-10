import 'package:firebase_auth/firebase_auth.dart';
import 'package:flutter/material.dart';

import 'core/api_service.dart';
import 'core/app_theme.dart';
import 'features/auth/auth_gate.dart';
import 'features/splash/launch_splash_screen.dart';

class HimikamaApp extends StatefulWidget {
  const HimikamaApp({
    super.key,
    this.home,
    this.apiService,
    this.firebaseAuth,
    this.splashDuration = const Duration(milliseconds: 2200),
  });

  final Widget? home;
  final ApiService? apiService;
  final FirebaseAuth? firebaseAuth;
  final Duration splashDuration;

  @override
  State<HimikamaApp> createState() => _HimikamaAppState();
}

class _HimikamaAppState extends State<HimikamaApp> {
  ApiService? _ownedApiService;

  @override
  void dispose() {
    _ownedApiService?.close();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'Himikama',
      debugShowCheckedModeBanner: false,
      theme: AppTheme.light,
      home:
          widget.home ??
          LaunchSplashScreen(
            duration: widget.splashDuration,
            destination: _buildAuthGate(),
          ),
    );
  }

  Widget _buildAuthGate() {
    final auth = widget.firebaseAuth ?? FirebaseAuth.instance;
    final apiService =
        widget.apiService ??
        (_ownedApiService ??= ApiService(firebaseAuth: auth));
    return AuthGate(firebaseAuth: auth, apiService: apiService);
  }
}
