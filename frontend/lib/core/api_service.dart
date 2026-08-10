import 'dart:async';
import 'dart:convert';

import 'package:firebase_app_check/firebase_app_check.dart';
import 'package:firebase_auth/firebase_auth.dart';
import 'package:http/http.dart' as http;

import 'app_config.dart';

enum ApiFailureKind {
  offline,
  timeout,
  authentication,
  authorization,
  notFound,
  conflict,
  server,
  invalidResponse,
  validation,
  unknown,
}

class ApiException implements Exception {
  const ApiException({
    required this.message,
    this.statusCode,
    this.requestId,
    this.technicalDetails,
    this.kind = ApiFailureKind.unknown,
  });

  final String message;
  final int? statusCode;
  final String? requestId;
  final String? technicalDetails;
  final ApiFailureKind kind;

  bool get isTransient => const {
    ApiFailureKind.offline,
    ApiFailureKind.timeout,
    ApiFailureKind.server,
  }.contains(kind);

  @override
  String toString() => message;
}

class UserProfile {
  const UserProfile({
    required this.displayName,
    required this.email,
    required this.emailVerified,
    required this.accountStatus,
    required this.termsVersion,
    required this.privacyVersion,
    required this.termsCurrent,
    required this.privacyCurrent,
    required this.assessmentConsentVersion,
    required this.assessmentConsentCurrent,
    this.assessmentConsentAt,
    this.deletionRequestedAt,
    this.deletionEffectiveAt,
  });

  factory UserProfile.fromJson(Map<String, dynamic> json) {
    return UserProfile(
      displayName: json['display_name'] as String? ?? '',
      email: json['email'] as String? ?? '',
      emailVerified: json['email_verified'] as bool? ?? false,
      accountStatus: json['account_status'] as String? ?? '',
      termsVersion: json['terms_version'] as String? ?? '',
      privacyVersion: json['privacy_version'] as String? ?? '',
      termsCurrent: json['terms_current'] as bool? ?? false,
      privacyCurrent: json['privacy_current'] as bool? ?? false,
      assessmentConsentVersion:
          json['assessment_consent_version'] as String? ?? '',
      assessmentConsentCurrent:
          json['assessment_consent_current'] as bool? ?? false,
      assessmentConsentAt: _parseDate(json['assessment_consent_at']),
      deletionRequestedAt: _parseDate(json['deletion_requested_at']),
      deletionEffectiveAt: _parseDate(json['deletion_effective_at']),
    );
  }

  final String displayName;
  final String email;
  final bool emailVerified;
  final String accountStatus;
  final String termsVersion;
  final String privacyVersion;
  final bool termsCurrent;
  final bool privacyCurrent;
  final String assessmentConsentVersion;
  final bool assessmentConsentCurrent;
  final DateTime? assessmentConsentAt;
  final DateTime? deletionRequestedAt;
  final DateTime? deletionEffectiveAt;

  bool get policiesCurrent => termsCurrent && privacyCurrent;

  static DateTime? _parseDate(Object? value) {
    if (value is DateTime) return value;
    if (value is! String || value.trim().isEmpty) return null;
    return DateTime.tryParse(value)?.toLocal();
  }
}

class ApiService {
  ApiService({FirebaseAuth? firebaseAuth, http.Client? client, String? baseUrl})
    : _firebaseAuth = firebaseAuth ?? FirebaseAuth.instance,
      _client = client ?? http.Client(),
      _ownsClient = client == null,
      _baseUrl = _normalizeBaseUrl(baseUrl ?? AppConfig.apiBaseUrl);

  final FirebaseAuth _firebaseAuth;
  final http.Client _client;
  final bool _ownsClient;
  final String _baseUrl;

  String get baseUrl => _baseUrl;

  String? get currentUserId => _firebaseAuth.currentUser?.uid;

  Future<bool> checkHealth() async {
    final response = await _send('GET', '/health', authenticated: false);
    return response['status'] == 'ok';
  }

  Future<UserProfile> getProfile() async {
    final response = await _send('GET', '/users/me');
    return UserProfile.fromJson(_jsonMap(response['profile']));
  }

  Future<UserProfile> createProfile({
    required String displayName,
    required bool acceptTerms,
    required bool acceptPrivacyPolicy,
  }) async {
    final response = await _send(
      'POST',
      '/users/me/profile',
      body: {
        'display_name': displayName.trim(),
        'accept_terms': acceptTerms,
        'accept_privacy_policy': acceptPrivacyPolicy,
      },
      forceTokenRefresh: true,
    );
    return UserProfile.fromJson(_jsonMap(response['profile']));
  }

  Future<UserProfile> acceptCurrentPolicies() async {
    final response = await _send(
      'POST',
      '/users/me/policies',
      body: const {'accept_terms': true, 'accept_privacy_policy': true},
    );
    return UserProfile.fromJson(_jsonMap(response['profile']));
  }

  Future<UserProfile> updateProfile({required String displayName}) async {
    final response = await _send(
      'PATCH',
      '/users/me/profile',
      body: {'display_name': displayName.trim()},
    );
    return UserProfile.fromJson(_jsonMap(response['profile']));
  }

  Future<UserProfile> acceptAssessmentConsent() async {
    final response = await _send(
      'POST',
      '/users/me/assessment-consent',
      body: const {'accept_assessment_processing': true},
    );
    return UserProfile.fromJson(_jsonMap(response['profile']));
  }

  Future<UserProfile> withdrawAssessmentConsent() async {
    final response = await _send('DELETE', '/users/me/assessment-consent');
    return UserProfile.fromJson(_jsonMap(response['profile']));
  }

  Future<UserProfile> scheduleAccountDeletion() async {
    final response = await _send(
      'DELETE',
      '/users/me',
      body: const {'confirmation': 'DELETE'},
      forceTokenRefresh: true,
    );
    return UserProfile.fromJson(_jsonMap(response['profile']));
  }

  Future<UserProfile> cancelAccountDeletion() async {
    final response = await _send('POST', '/users/me/deletion/cancel');
    return UserProfile.fromJson(_jsonMap(response['profile']));
  }

  Future<List<Map<String, dynamic>>> getHistory({int limit = 50}) async {
    final response = await _send('GET', '/analysis/history?limit=$limit');
    final items = response['items'];
    if (items is! List) {
      return const [];
    }
    return items
        .whereType<Map>()
        .map((item) => item.cast<String, dynamic>())
        .toList(growable: false);
  }

  Future<void> deleteAttempt(String attemptId) async {
    await _send('DELETE', '/analysis/attempts/$attemptId');
  }

  Future<void> clearHistory() async {
    await _send('DELETE', '/analysis/history');
  }

  Future<Map<String, dynamic>> getAttempt(String attemptId) {
    return _send('GET', '/analysis/attempts/$attemptId');
  }

  Future<Map<String, dynamic>> getReasoningTrace(String attemptId) {
    return _send('GET', '/analysis/attempts/$attemptId/trace');
  }

  Future<Map<String, dynamic>> structureIntake(
    String rawUserDescription,
  ) async {
    final body = {'raw_user_description': rawUserDescription.trim()};

    try {
      return await _send(
        'POST',
        '/analysis/structure-intake',
        body: body,
        timeout: const Duration(seconds: 90),
      );
    } on ApiException catch (error) {
      // Step 0 has no persistent side effects and creates no attempt, so one
      // retry after a transport-level interruption cannot duplicate an
      // analysis. HTTP errors such as 401, 422, or 502 are not retried here.
      if (error.statusCode != null) rethrow;
      await Future<void>.delayed(const Duration(milliseconds: 750));
      return _send(
        'POST',
        '/analysis/structure-intake',
        body: body,
        timeout: const Duration(seconds: 90),
      );
    }
  }

  Future<Map<String, dynamic>> submitAnalysis(
    Map<String, dynamic> confirmedIntake, {
    required String attemptId,
  }) {
    return _send(
      'POST',
      '/analysis/analyze',
      body: {'attempt_id': attemptId, 'intake': confirmedIntake},
      timeout: const Duration(seconds: 30),
    );
  }

  Future<Map<String, dynamic>> retryAnalysis(String attemptId) {
    return _send(
      'POST',
      '/analysis/attempts/$attemptId/retry',
      timeout: const Duration(seconds: 30),
    );
  }

  Future<Map<String, dynamic>> _send(
    String method,
    String path, {
    Map<String, dynamic>? body,
    bool authenticated = true,
    bool forceTokenRefresh = false,
    Duration timeout = const Duration(seconds: 60),
  }) async {
    final headers = <String, String>{
      'Accept': 'application/json',
      if (body != null) 'Content-Type': 'application/json',
    };

    if (AppConfig.appCheckEnabled) {
      try {
        final appCheckToken = await FirebaseAppCheck.instance.getToken();
        if (appCheckToken == null || appCheckToken.isEmpty) {
          throw const ApiException(
            message: 'The app could not verify this installation.',
            statusCode: 401,
            kind: ApiFailureKind.authentication,
          );
        }
        headers['X-Firebase-AppCheck'] = appCheckToken;
      } on ApiException {
        rethrow;
      } on FirebaseException {
        throw const ApiException(
          message: 'The app could not verify this installation.',
          statusCode: 401,
          kind: ApiFailureKind.authentication,
        );
      }
    }

    if (authenticated) {
      final user = _firebaseAuth.currentUser;
      if (user == null) {
        throw const ApiException(
          message: 'Your session ended. Please sign in again.',
          statusCode: 401,
          kind: ApiFailureKind.authentication,
        );
      }
      final String? token;
      try {
        token = await user.getIdToken(forceTokenRefresh);
      } on FirebaseAuthException {
        throw const ApiException(
          message: 'Your secure session could not be refreshed. Sign in again.',
          statusCode: 401,
          kind: ApiFailureKind.authentication,
        );
      }
      if (token == null || token.isEmpty) {
        throw const ApiException(
          message: 'Could not create a secure login token.',
          statusCode: 401,
          kind: ApiFailureKind.authentication,
        );
      }
      headers['Authorization'] = 'Bearer $token';
    }

    final uri = Uri.parse('$_baseUrl$path');

    try {
      final http.Response response;
      switch (method) {
        case 'GET':
          response = await _client.get(uri, headers: headers).timeout(timeout);
          break;
        case 'POST':
          response = await _client
              .post(
                uri,
                headers: headers,
                body: body == null ? null : jsonEncode(body),
              )
              .timeout(timeout);
          break;
        case 'PATCH':
          response = await _client
              .patch(
                uri,
                headers: headers,
                body: body == null ? null : jsonEncode(body),
              )
              .timeout(timeout);
          break;
        case 'DELETE':
          response = await _client
              .delete(
                uri,
                headers: headers,
                body: body == null ? null : jsonEncode(body),
              )
              .timeout(timeout);
          break;
        default:
          throw ArgumentError.value(
            method,
            'method',
            'Unsupported HTTP method',
          );
      }

      final decoded = _decodeObject(response.body);
      if (response.statusCode >= 200 && response.statusCode < 300) {
        return decoded;
      }

      throw ApiException(
        message: _errorMessage(decoded, response.statusCode),
        statusCode: response.statusCode,
        kind: _kindForStatus(response.statusCode),
        requestId:
            response.headers['x-request-id'] ??
            decoded['request_id'] as String?,
      );
    } on ApiException {
      rethrow;
    } on TimeoutException {
      throw const ApiException(
        message:
            'The server took too long to respond. Your saved attempt ID makes '
            'it safe to check or retry without creating a duplicate.',
        kind: ApiFailureKind.timeout,
      );
    } on http.ClientException {
      throw ApiException(
        message:
            'We could not reach Himikama. Check your connection and try again.',
        technicalDetails: 'Server: $_baseUrl',
        kind: ApiFailureKind.offline,
      );
    } on FormatException {
      throw const ApiException(
        message: 'The server returned an unreadable response.',
        kind: ApiFailureKind.invalidResponse,
      );
    }
  }

  void close() {
    if (_ownsClient) {
      _client.close();
    }
  }

  static String _normalizeBaseUrl(String value) {
    final normalized = value.trim();
    if (normalized.isEmpty) {
      throw ArgumentError.value(value, 'baseUrl', 'Base URL cannot be empty');
    }
    return normalized.endsWith('/')
        ? normalized.substring(0, normalized.length - 1)
        : normalized;
  }

  static Map<String, dynamic> _decodeObject(String body) {
    if (body.trim().isEmpty) {
      return <String, dynamic>{};
    }
    final decoded = jsonDecode(body);
    if (decoded is! Map) {
      throw const FormatException('Expected a JSON object');
    }
    return decoded.cast<String, dynamic>();
  }

  static Map<String, dynamic> _jsonMap(Object? value) {
    if (value is! Map) {
      return <String, dynamic>{};
    }
    return value.cast<String, dynamic>();
  }

  static String _errorMessage(Map<String, dynamic> response, int statusCode) {
    final detail = response['detail'];
    if (detail is String && detail.trim().isNotEmpty) {
      return detail.trim();
    }
    return switch (statusCode) {
      401 => 'Your session is no longer valid. Please sign in again.',
      403 => 'Your account cannot perform this action yet.',
      404 => 'The requested information was not found.',
      409 => 'This assessment cannot be changed in its current state.',
      422 => 'Please check the information you entered.',
      502 => 'The intake service could not process the description.',
      500 => 'The server could not complete the request.',
      _ => 'The request failed. Please try again.',
    };
  }

  static ApiFailureKind _kindForStatus(int statusCode) {
    return switch (statusCode) {
      401 => ApiFailureKind.authentication,
      403 => ApiFailureKind.authorization,
      404 => ApiFailureKind.notFound,
      409 => ApiFailureKind.conflict,
      422 => ApiFailureKind.validation,
      >= 500 => ApiFailureKind.server,
      _ => ApiFailureKind.unknown,
    };
  }
}
