import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

class LaunchSplashScreen extends StatefulWidget {
  const LaunchSplashScreen({
    required this.destination,
    super.key,
    this.duration = const Duration(milliseconds: 2200),
  });

  final Widget destination;
  final Duration duration;

  @override
  State<LaunchSplashScreen> createState() => _LaunchSplashScreenState();
}

class _LaunchSplashScreenState extends State<LaunchSplashScreen> {
  Timer? _timer;
  bool _finished = false;

  @override
  void initState() {
    super.initState();
    _timer = Timer(widget.duration, () {
      if (mounted) setState(() => _finished = true);
    });
  }

  @override
  void dispose() {
    _timer?.cancel();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return AnimatedSwitcher(
      duration: const Duration(milliseconds: 350),
      switchInCurve: Curves.easeOut,
      switchOutCurve: Curves.easeIn,
      child: _finished
          ? KeyedSubtree(
              key: const ValueKey('splash-destination'),
              child: widget.destination,
            )
          : const _SplashArtwork(key: ValueKey('launch-splash')),
    );
  }
}

class _SplashArtwork extends StatelessWidget {
  const _SplashArtwork({super.key});

  @override
  Widget build(BuildContext context) {
    return AnnotatedRegion<SystemUiOverlayStyle>(
      value: SystemUiOverlayStyle.dark,
      child: Scaffold(
        backgroundColor: const Color(0xFFFDFCFA),
        body: Semantics(
          label: 'Himikama launch screen',
          image: true,
          child: SizedBox.expand(
            child: Image.asset(
              'assets/images/himikama_splash.png',
              fit: BoxFit.cover,
              alignment: Alignment.center,
              filterQuality: FilterQuality.high,
              errorBuilder: (context, error, stackTrace) =>
                  const _FallbackLogo(),
            ),
          ),
        ),
      ),
    );
  }
}

class _FallbackLogo extends StatelessWidget {
  const _FallbackLogo();

  @override
  Widget build(BuildContext context) {
    return const ColoredBox(
      color: Color(0xFFFDFCFA),
      child: Center(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(Icons.balance_outlined, size: 72, color: Color(0xFF194F82)),
            SizedBox(height: 18),
            Text(
              'Himikama',
              style: TextStyle(
                color: Color(0xFF123A60),
                fontSize: 38,
                fontWeight: FontWeight.w700,
              ),
            ),
          ],
        ),
      ),
    );
  }
}
