import 'dart:async';

import 'package:flutter/material.dart';

import '../../core/api_service.dart';
import '../../core/pending_attempt_store.dart';
import 'analysis_result_screen.dart';
import 'analysis_status.dart';

class AnalysisProcessingScreen extends StatefulWidget {
  const AnalysisProcessingScreen({
    required this.apiService,
    required this.attemptId,
    super.key,
    this.ownerUid,
    this.pendingAttemptStore,
  });

  final ApiService apiService;
  final String attemptId;
  final String? ownerUid;
  final PendingAttemptStore? pendingAttemptStore;

  @override
  State<AnalysisProcessingScreen> createState() =>
      _AnalysisProcessingScreenState();
}

class _AnalysisProcessingScreenState extends State<AnalysisProcessingScreen>
    with WidgetsBindingObserver {
  static const _activePollDelays = <Duration>[
    Duration(seconds: 4),
    Duration(seconds: 6),
    Duration(seconds: 10),
    Duration(seconds: 15),
    Duration(seconds: 20),
    Duration(seconds: 30),
  ];
  static const _errorPollDelays = <Duration>[
    Duration(seconds: 5),
    Duration(seconds: 10),
    Duration(seconds: 20),
    Duration(seconds: 30),
  ];

  late final PendingAttemptStore _pendingAttemptStore;
  Timer? _pollTimer;
  String _status = 'processing';
  String? _message;
  ApiFailureKind? _failureKind;
  bool _checking = false;
  bool _retrying = false;
  bool _navigating = false;
  bool _stopPolling = false;
  int _activePollIndex = 0;
  int _errorPollIndex = 0;

  String? get _ownerUid => widget.ownerUid ?? widget.apiService.currentUserId;

  @override
  void initState() {
    super.initState();
    _pendingAttemptStore =
        widget.pendingAttemptStore ?? SharedPreferencesPendingAttemptStore();
    WidgetsBinding.instance.addObserver(this);
    WidgetsBinding.instance.addPostFrameCallback((_) => _prepareAndCheck());
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    _pollTimer?.cancel();
    super.dispose();
  }

  Future<void> _prepareAndCheck() async {
    final ownerUid = _ownerUid;
    if (ownerUid != null && ownerUid.isNotEmpty) {
      try {
        await _pendingAttemptStore.save(
          PendingAttemptReference(
            ownerUid: ownerUid,
            attemptId: widget.attemptId,
          ),
        );
      } on Exception {
        // The server attempt still exists and remains reachable through
        // History even if local recovery storage is unavailable.
      }
    }
    await _checkAttempt();
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state == AppLifecycleState.resumed && !_navigating) {
      _manualCheck();
    } else if (state != AppLifecycleState.resumed) {
      _pollTimer?.cancel();
    }
  }

  Duration _nextActiveDelay() {
    final index = _activePollIndex
        .clamp(0, _activePollDelays.length - 1)
        .toInt();
    _activePollIndex++;
    return _activePollDelays[index];
  }

  Duration _nextErrorDelay() {
    final index = _errorPollIndex.clamp(0, _errorPollDelays.length - 1).toInt();
    _errorPollIndex++;
    return _errorPollDelays[index];
  }

  void _schedulePoll(Duration delay) {
    if (!mounted || _stopPolling || _navigating) return;
    _pollTimer?.cancel();
    _pollTimer = Timer(delay, _checkAttempt);
  }

  void _manualCheck() {
    if (_checking || _retrying || _navigating) return;
    _stopPolling = false;
    _activePollIndex = 0;
    _errorPollIndex = 0;
    _pollTimer?.cancel();
    unawaited(_checkAttempt());
  }

  Future<void> _clearPendingReference() async {
    final ownerUid = _ownerUid;
    if (ownerUid == null || ownerUid.isEmpty) return;
    try {
      await _pendingAttemptStore.clear(
        ownerUid: ownerUid,
        attemptId: widget.attemptId,
      );
    } on Exception {
      // A stale prompt can be dismissed later; never hide a completed result.
    }
  }

  Future<void> _checkAttempt() async {
    if (!mounted || _checking || _navigating || _stopPolling) return;
    _pollTimer?.cancel();
    setState(() => _checking = true);

    Duration? nextDelay;
    try {
      final result = await widget.apiService.getAttempt(widget.attemptId);
      if (!mounted) return;

      _errorPollIndex = 0;
      final rawStatus = result['status'];
      final state = classifyAnalysisStatus(rawStatus);
      _status = rawStatus?.toString() ?? '';

      switch (state) {
        case AnalysisAttemptState.succeeded:
          _stopPolling = true;
          _navigating = true;
          await _clearPendingReference();
          if (!mounted) return;
          await Navigator.of(context).pushReplacement(
            MaterialPageRoute<void>(
              builder: (_) => AnalysisResultScreen(
                apiService: widget.apiService,
                result: result,
              ),
            ),
          );
          break;
        case AnalysisAttemptState.failed:
          _stopPolling = true;
          setState(() {
            _failureKind = null;
            _message =
                'The analysis stopped before a legal result was produced. '
                'You can safely retry this same attempt without creating a '
                'duplicate assessment.';
          });
          break;
        case AnalysisAttemptState.active:
          setState(() {
            _failureKind = null;
            _message = null;
          });
          nextDelay = _nextActiveDelay();
          break;
        case AnalysisAttemptState.unknown:
          setState(() {
            _failureKind = ApiFailureKind.invalidResponse;
            _message =
                'The server returned an unfamiliar status. Himikama will '
                'check this saved attempt again without creating another one.';
          });
          nextDelay = _nextErrorDelay();
          break;
      }
    } on ApiException catch (error) {
      if (!mounted) return;
      final permanent = const {
        ApiFailureKind.authentication,
        ApiFailureKind.authorization,
        ApiFailureKind.notFound,
      }.contains(error.kind);
      _stopPolling = permanent;
      setState(() {
        _failureKind = error.kind;
        _message = _messageFor(error);
      });
      if (!permanent) nextDelay = _nextErrorDelay();
    } finally {
      if (mounted && !_navigating) {
        setState(() => _checking = false);
        if (nextDelay != null) _schedulePoll(nextDelay);
      }
    }
  }

  String _messageFor(ApiException error) {
    return switch (error.kind) {
      ApiFailureKind.offline =>
        'The device appears offline or cannot reach Himikama. The assessment '
            'remains saved and will be checked again automatically.',
      ApiFailureKind.timeout =>
        'The server did not respond in time. The assessment remains saved and '
            'Himikama will check again with a slower retry interval.',
      ApiFailureKind.server =>
        'The Himikama server is temporarily unavailable. The durable worker '
            'can continue or recover the assessment after the server restarts.',
      ApiFailureKind.authentication =>
        'Your session ended. Sign in again to resume this private assessment.',
      ApiFailureKind.authorization =>
        'This account cannot access the saved assessment.',
      ApiFailureKind.notFound =>
        'The saved attempt could not be found for this account.',
      ApiFailureKind.invalidResponse =>
        'The server response could not be read. No new attempt was created.',
      ApiFailureKind.conflict =>
        'The attempt may already have restarted. Himikama will check the same '
            'attempt ID instead of creating another assessment.',
      _ => error.message,
    };
  }

  Future<void> _retryFailedAttempt() async {
    if (_retrying) return;
    var shouldCheck = false;
    setState(() {
      _retrying = true;
      _message = null;
      _failureKind = null;
    });
    try {
      final response = await widget.apiService.retryAnalysis(widget.attemptId);
      if (!mounted) return;
      if (response['attempt_id'] != widget.attemptId) {
        throw const ApiException(
          message: 'The server returned an invalid retry reference.',
          kind: ApiFailureKind.invalidResponse,
        );
      }
      final ownerUid = _ownerUid;
      if (ownerUid != null && ownerUid.isNotEmpty) {
        try {
          await _pendingAttemptStore.save(
            PendingAttemptReference(
              ownerUid: ownerUid,
              attemptId: widget.attemptId,
            ),
          );
        } on Exception {
          // History remains the server-side recovery path.
        }
      }
      if (!mounted) return;
      setState(() {
        _status = 'processing';
        _stopPolling = false;
        _activePollIndex = 0;
        _errorPollIndex = 0;
      });
      shouldCheck = true;
    } on ApiException catch (error) {
      if (!mounted) return;
      final submissionMayHaveSucceeded =
          error.kind == ApiFailureKind.conflict || error.isTransient;
      setState(() {
        _failureKind = error.kind;
        _message = _messageFor(error);
        if (submissionMayHaveSucceeded) {
          _stopPolling = false;
        }
      });
      shouldCheck = submissionMayHaveSucceeded;
    } finally {
      if (mounted) setState(() => _retrying = false);
    }
    if (shouldCheck && mounted) _manualCheck();
  }

  Future<void> _forgetUnavailableAttempt() async {
    await _clearPendingReference();
    if (!mounted) return;
    Navigator.of(context).popUntil((route) => route.isFirst);
  }

  @override
  Widget build(BuildContext context) {
    final state = classifyAnalysisStatus(_status);
    final failed = state == AnalysisAttemptState.failed;
    final hasConnectionProblem = _failureKind != null;

    return PopScope(
      canPop: true,
      child: Scaffold(
        appBar: AppBar(title: const Text('Assessment progress')),
        body: SafeArea(
          child: ListView(
            padding: const EdgeInsets.all(20),
            children: [
              Center(
                child: failed
                    ? Icon(
                        Icons.error_outline,
                        size: 58,
                        color: Theme.of(context).colorScheme.error,
                      )
                    : hasConnectionProblem
                    ? Icon(
                        _failureKind == ApiFailureKind.offline
                            ? Icons.cloud_off_outlined
                            : Icons.sync_problem_outlined,
                        size: 58,
                        color: Theme.of(context).colorScheme.secondary,
                      )
                    : const SizedBox.square(
                        dimension: 54,
                        child: CircularProgressIndicator(),
                      ),
              ),
              const SizedBox(height: 24),
              Text(
                failed
                    ? 'Analysis needs attention'
                    : readableAnalysisStatus(_status),
                textAlign: TextAlign.center,
                style: Theme.of(context).textTheme.headlineSmall?.copyWith(
                  fontWeight: FontWeight.w800,
                ),
              ),
              const SizedBox(height: 12),
              const Text(
                'Your confirmed information and attempt ID were saved before '
                'processing began. You may leave this screen; Himikama can '
                'resume the same assessment after an app or server restart.',
                textAlign: TextAlign.center,
              ),
              const SizedBox(height: 20),
              Card(
                child: Padding(
                  padding: const EdgeInsets.all(16),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      const Text(
                        'Attempt ID',
                        style: TextStyle(fontWeight: FontWeight.w700),
                      ),
                      const SizedBox(height: 6),
                      SelectableText(widget.attemptId),
                    ],
                  ),
                ),
              ),
              if (_message case final message?) ...[
                const SizedBox(height: 16),
                Card(
                  color: failed
                      ? Theme.of(context).colorScheme.errorContainer
                      : Theme.of(context).colorScheme.secondaryContainer,
                  child: Padding(
                    padding: const EdgeInsets.all(16),
                    child: Text(message),
                  ),
                ),
              ],
              const SizedBox(height: 20),
              if (failed)
                FilledButton.icon(
                  key: const Key('retry-assessment-button'),
                  onPressed: _retrying ? null : _retryFailedAttempt,
                  icon: _retrying
                      ? const SizedBox.square(
                          dimension: 18,
                          child: CircularProgressIndicator(strokeWidth: 2),
                        )
                      : const Icon(Icons.restart_alt),
                  label: Text(
                    _retrying ? 'Retrying safely…' : 'Retry assessment',
                  ),
                )
              else
                OutlinedButton.icon(
                  key: const Key('check-again-button'),
                  onPressed: _checking || _retrying ? null : _manualCheck,
                  icon: _checking
                      ? const SizedBox.square(
                          dimension: 18,
                          child: CircularProgressIndicator(strokeWidth: 2),
                        )
                      : const Icon(Icons.refresh),
                  label: const Text('Check again'),
                ),
              if (_failureKind == ApiFailureKind.notFound) ...[
                const SizedBox(height: 10),
                TextButton(
                  onPressed: _forgetUnavailableAttempt,
                  child: const Text('Remove saved reference'),
                ),
              ],
              const SizedBox(height: 10),
              FilledButton.tonal(
                onPressed: () {
                  Navigator.of(context).popUntil((route) => route.isFirst);
                },
                child: const Text('Return home'),
              ),
            ],
          ),
        ),
      ),
    );
  }
}
