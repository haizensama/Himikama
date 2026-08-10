import 'package:flutter/foundation.dart';

abstract final class AppConfig {
  static const String _configuredApiBaseUrl = String.fromEnvironment(
    'API_BASE_URL',
  );
  static const bool _debugAppCheckEnabled = bool.fromEnvironment(
    'APP_CHECK_ENABLED',
    defaultValue: false,
  );
  static const bool showDeveloperTools = bool.fromEnvironment(
    'SHOW_DEVELOPER_TOOLS',
    defaultValue: false,
  );

  static bool get appCheckEnabled => kReleaseMode || _debugAppCheckEnabled;

  static String get apiBaseUrl {
    if (_configuredApiBaseUrl.isNotEmpty) {
      final configured = _configuredApiBaseUrl.trim();
      final uri = Uri.tryParse(configured);
      if (uri == null || !uri.hasScheme || uri.host.isEmpty) {
        throw StateError('API_BASE_URL must be an absolute URL.');
      }
      if (kReleaseMode && uri.scheme != 'https') {
        throw StateError('Release builds require an HTTPS API_BASE_URL.');
      }
      return configured;
    }

    if (kReleaseMode) {
      throw StateError(
        'Release builds require --dart-define=API_BASE_URL=https://…',
      );
    }

    if (kIsWeb) {
      return 'http://127.0.0.1:8000';
    }

    switch (defaultTargetPlatform) {
      case TargetPlatform.android:
        return 'http://10.0.2.2:8000';
      case TargetPlatform.iOS:
      case TargetPlatform.linux:
      case TargetPlatform.macOS:
      case TargetPlatform.windows:
        return 'http://127.0.0.1:8000';
      case TargetPlatform.fuchsia:
        return 'http://127.0.0.1:8000';
    }
  }
}
