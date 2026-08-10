import 'package:flutter/material.dart';

import '../../core/api_service.dart';
import '../../core/pending_attempt_store.dart';
import '../results/analysis_processing_screen.dart';
import 'intake_models.dart';
import 'intake_review_validation.dart';

class ReviewIntakeScreen extends StatefulWidget {
  const ReviewIntakeScreen({
    required this.apiService,
    required this.structured,
    super.key,
    this.pendingAttemptStore,
  });

  final ApiService apiService;
  final StructuredIntakeResponse structured;
  final PendingAttemptStore? pendingAttemptStore;

  @override
  State<ReviewIntakeScreen> createState() => _ReviewIntakeScreenState();
}

class _ReviewIntakeScreenState extends State<ReviewIntakeScreen> {
  final _formKey = GlobalKey<FormState>();
  late final TextEditingController _date;
  late final TextEditingController _location;
  late final TextEditingController _actorName;
  late final TextEditingController _actorRole;
  late final TextEditingController _whatHappened;
  late final TextEditingController _harm;
  late final TextEditingController _narrative;
  late final List<TextEditingController> _controllers;
  late final PendingAttemptStore _pendingAttemptStore;

  bool _analyzing = false;
  String? _error;
  String? _attemptId;

  @override
  void initState() {
    super.initState();
    _pendingAttemptStore =
        widget.pendingAttemptStore ?? SharedPreferencesPendingAttemptStore();
    final intake = widget.structured.intake;
    _date = TextEditingController(text: intake.incidentDate ?? '');
    _location = TextEditingController(text: intake.incidentLocation ?? '');
    _actorName = TextEditingController(text: intake.actorName ?? '');
    _actorRole = TextEditingController(text: intake.actorRole ?? '');
    _whatHappened = TextEditingController(text: intake.whatHappened ?? '');
    _harm = TextEditingController(text: intake.harmSuffered ?? '');
    _narrative = TextEditingController(text: intake.userNarrative);
    _controllers = [
      _date,
      _location,
      _actorName,
      _actorRole,
      _whatHappened,
      _harm,
      _narrative,
    ];
    for (final controller in _controllers) {
      controller.addListener(_refresh);
    }
  }

  void _refresh() {
    if (mounted) setState(() {});
  }

  @override
  void dispose() {
    for (final controller in _controllers) {
      controller
        ..removeListener(_refresh)
        ..dispose();
    }
    super.dispose();
  }

  bool get _canAnalyze {
    return isValidIntakeIsoDate(_date.text) &&
        _actorRole.text.trim().length >= 2 &&
        _actorRole.text.trim().length <= 120 &&
        _whatHappened.text.trim().length >= 10 &&
        _whatHappened.text.trim().length <= 2000 &&
        _narrative.text.trim().length >= 10 &&
        _narrative.text.trim().length <= 4000;
  }

  List<String> get _activeClarifyingQuestions {
    return buildActiveIntakeClarifyingQuestions(
      incidentDate: _date.text,
      actorRole: _actorRole.text,
      whatHappened: _whatHappened.text,
      userNarrative: _narrative.text,
    );
  }

  String? _requiredText(
    String? value, {
    required String label,
    required int minimum,
    required int maximum,
  }) {
    final text = value?.trim() ?? '';
    if (text.isEmpty) return '$label is required.';
    if (text.length < minimum) {
      return '$label must contain at least $minimum characters.';
    }
    if (text.length > maximum) {
      return '$label cannot exceed $maximum characters.';
    }
    return null;
  }

  String? _optionalText(
    String? value, {
    required String label,
    required int maximum,
  }) {
    final text = value?.trim() ?? '';
    if (text.length > maximum) {
      return '$label cannot exceed $maximum characters.';
    }
    return null;
  }

  Future<void> _selectDate() async {
    final parsed = DateTime.tryParse(_date.text.trim());
    final now = DateTime.now();
    final selected = await showDatePicker(
      context: context,
      initialDate: parsed == null || parsed.isAfter(now) ? now : parsed,
      firstDate: DateTime(1900),
      lastDate: now,
      helpText: 'Select the incident date',
    );
    if (selected == null) return;
    _date.text = selected.toIso8601String().split('T').first;
  }

  String? _nullable(String value) {
    final normalized = value.trim();
    return normalized.isEmpty ? null : normalized;
  }

  Future<void> _runAnalysis() async {
    if (!_formKey.currentState!.validate() || !_canAnalyze) return;
    setState(() {
      _analyzing = true;
      _error = null;
    });

    final confirmedIntake = <String, dynamic>{
      'incident_date': _date.text.trim(),
      'incident_location': _nullable(_location.text),
      'actor_name': _nullable(_actorName.text),
      'actor_role': _actorRole.text.trim(),
      'what_happened': _whatHappened.text.trim(),
      'harm_suffered': _nullable(_harm.text),
      'user_narrative': _narrative.text.trim(),
    };

    try {
      final ownerUid = widget.apiService.currentUserId;
      if (ownerUid == null || ownerUid.isEmpty) {
        throw const ApiException(
          message: 'Your session ended. Please sign in again.',
          statusCode: 401,
          kind: ApiFailureKind.authentication,
        );
      }
      final attemptId = _attemptId ??= createAttemptUuid();
      try {
        await _pendingAttemptStore.save(
          PendingAttemptReference(ownerUid: ownerUid, attemptId: attemptId),
        );
      } on Exception {
        throw const ApiException(
          message:
              'Himikama could not save the local recovery reference. Free '
              'device storage and try again before starting the assessment.',
        );
      }
      final submission = await widget.apiService.submitAnalysis(
        confirmedIntake,
        attemptId: attemptId,
      );
      if (!mounted) return;
      final returnedAttemptId = submission['attempt_id'] as String? ?? '';
      if (returnedAttemptId != attemptId) {
        throw const ApiException(
          message: 'The server returned an invalid attempt reference.',
          kind: ApiFailureKind.invalidResponse,
        );
      }
      await Navigator.of(context).pushReplacement(
        MaterialPageRoute<void>(
          builder: (_) => AnalysisProcessingScreen(
            apiService: widget.apiService,
            attemptId: attemptId,
            ownerUid: ownerUid,
            pendingAttemptStore: _pendingAttemptStore,
          ),
        ),
      );
    } on ApiException catch (error) {
      if (!mounted) return;
      if (error.kind == ApiFailureKind.validation ||
          error.kind == ApiFailureKind.conflict) {
        final attemptId = _attemptId;
        final ownerUid = widget.apiService.currentUserId;
        if (attemptId != null && ownerUid != null) {
          try {
            await _pendingAttemptStore.clear(
              ownerUid: ownerUid,
              attemptId: attemptId,
            );
          } on Exception {
            // The next valid UUID will overwrite this unusable reference.
          }
        }
        _attemptId = null;
      }
      final requestId = error.requestId;
      setState(
        () => _error = requestId == null
            ? error.message
            : '${error.message} (Request ID: $requestId)',
      );
    } finally {
      if (mounted) setState(() => _analyzing = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final questions = _activeClarifyingQuestions;
    return Scaffold(
      appBar: AppBar(title: const Text('Review your details')),
      body: SafeArea(
        child: Form(
          key: _formKey,
          child: ListView(
            padding: const EdgeInsets.all(20),
            children: [
              Text(
                'Check what we understood',
                style: Theme.of(context).textTheme.headlineSmall?.copyWith(
                  fontWeight: FontWeight.w800,
                ),
              ),
              const SizedBox(height: 8),
              const Text(
                'Correct anything that is incomplete or inaccurate. Fields '
                'marked required must be complete before legal analysis.',
              ),
              if (questions.isNotEmpty) ...[
                const SizedBox(height: 16),
                Card(
                  color: Theme.of(context).colorScheme.errorContainer,
                  child: Padding(
                    padding: const EdgeInsets.all(16),
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        const Text(
                          'More information is needed',
                          style: TextStyle(fontWeight: FontWeight.w700),
                        ),
                        const SizedBox(height: 8),
                        ...questions.map(
                          (question) => Padding(
                            padding: const EdgeInsets.only(bottom: 6),
                            child: Text('• $question'),
                          ),
                        ),
                      ],
                    ),
                  ),
                ),
              ],
              const SizedBox(height: 20),
              TextFormField(
                key: const Key('incident-date'),
                controller: _date,
                readOnly: true,
                onTap: _analyzing ? null : _selectDate,
                decoration: const InputDecoration(
                  labelText: 'Incident date *',
                  hintText: 'YYYY-MM-DD',
                  prefixIcon: Icon(Icons.calendar_today_outlined),
                ),
                validator: (value) => isValidIntakeIsoDate(value ?? '')
                    ? null
                    : 'Select a valid incident date that is not in the future.',
              ),
              const SizedBox(height: 14),
              TextFormField(
                controller: _location,
                maxLength: 200,
                decoration: const InputDecoration(
                  labelText: 'Incident location',
                  prefixIcon: Icon(Icons.location_on_outlined),
                ),
                validator: (value) => _optionalText(
                  value,
                  label: 'Incident location',
                  maximum: 200,
                ),
              ),
              const SizedBox(height: 14),
              TextFormField(
                controller: _actorName,
                maxLength: 200,
                decoration: const InputDecoration(
                  labelText: 'Actor name or institution',
                  prefixIcon: Icon(Icons.account_balance_outlined),
                ),
                validator: (value) =>
                    _optionalText(value, label: 'Actor name', maximum: 200),
              ),
              const SizedBox(height: 14),
              TextFormField(
                key: const Key('actor-role'),
                controller: _actorRole,
                maxLength: 120,
                decoration: const InputDecoration(
                  labelText: 'Actor role or institution type *',
                  hintText: 'Example: police officer',
                  prefixIcon: Icon(Icons.badge_outlined),
                ),
                validator: (value) => _requiredText(
                  value,
                  label: 'Actor role',
                  minimum: 2,
                  maximum: 120,
                ),
              ),
              const SizedBox(height: 14),
              TextFormField(
                key: const Key('what-happened'),
                controller: _whatHappened,
                minLines: 4,
                maxLines: 8,
                maxLength: 2000,
                decoration: const InputDecoration(
                  labelText: 'What happened *',
                  alignLabelWithHint: true,
                ),
                validator: (value) => _requiredText(
                  value,
                  label: 'What happened',
                  minimum: 10,
                  maximum: 2000,
                ),
              ),
              const SizedBox(height: 14),
              TextFormField(
                controller: _harm,
                minLines: 3,
                maxLines: 6,
                maxLength: 1000,
                decoration: const InputDecoration(
                  labelText: 'Harm suffered',
                  alignLabelWithHint: true,
                ),
                validator: (value) =>
                    _optionalText(value, label: 'Harm suffered', maximum: 1000),
              ),
              const SizedBox(height: 14),
              TextFormField(
                key: const Key('user-narrative'),
                controller: _narrative,
                readOnly: true,
                minLines: 5,
                maxLines: 10,
                maxLength: 4000,
                decoration: const InputDecoration(
                  labelText: 'Your original description *',
                  alignLabelWithHint: true,
                  helperText:
                      'This is preserved exactly. Go back and organize the '
                      'description again if it needs to change.',
                  filled: true,
                ),
                validator: (value) => _requiredText(
                  value,
                  label: 'Your description',
                  minimum: 10,
                  maximum: 4000,
                ),
              ),
              if (_analyzing) ...[
                const SizedBox(height: 16),
                const Card(
                  child: Padding(
                    padding: EdgeInsets.all(16),
                    child: Row(
                      children: [
                        CircularProgressIndicator(),
                        SizedBox(width: 16),
                        Expanded(
                          child: Text(
                            'Saving your assessment and starting the legal '
                            'analysis…',
                          ),
                        ),
                      ],
                    ),
                  ),
                ),
              ],
              if (_error case final error?) ...[
                const SizedBox(height: 16),
                Text(
                  error,
                  style: TextStyle(
                    color: Theme.of(context).colorScheme.error,
                    fontWeight: FontWeight.w600,
                  ),
                ),
              ],
              const SizedBox(height: 24),
              FilledButton.icon(
                key: const Key('run-analysis-button'),
                onPressed: !_canAnalyze || _analyzing ? null : _runAnalysis,
                icon: _analyzing
                    ? const SizedBox.square(
                        dimension: 20,
                        child: CircularProgressIndicator(
                          strokeWidth: 2,
                          color: Colors.white,
                        ),
                      )
                    : const Icon(Icons.gavel_outlined),
                label: Text(
                  _analyzing
                      ? 'Starting safely…'
                      : _attemptId == null
                      ? 'Confirm and run analysis'
                      : 'Retry saved submission',
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}
