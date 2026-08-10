import 'package:firebase_auth/firebase_auth.dart';
import 'package:flutter/material.dart';

import '../../core/api_service.dart';
import '../../core/app_config.dart';
import '../../core/app_theme.dart';
import '../../core/pending_attempt_store.dart';
import '../intake/describe_situation_screen.dart';
import '../privacy/assessment_consent_screen.dart';
import '../results/analysis_processing_screen.dart';
import '../results/analysis_result_screen.dart';
import '../results/analysis_result_view_model.dart';
import '../results/analysis_status.dart';

class AssessmentsScreen extends StatefulWidget {
  const AssessmentsScreen({
    required this.firebaseAuth,
    required this.apiService,
    required this.user,
    required this.profile,
    super.key,
    this.onAssessmentConsentAccepted,
    this.pendingAttemptStore,
    this.showDeveloperTools = AppConfig.showDeveloperTools,
  });

  final FirebaseAuth firebaseAuth;
  final ApiService apiService;
  final User user;
  final UserProfile profile;
  final VoidCallback? onAssessmentConsentAccepted;
  final PendingAttemptStore? pendingAttemptStore;
  final bool showDeveloperTools;

  @override
  State<AssessmentsScreen> createState() => _AssessmentsScreenState();
}

class _AssessmentsScreenState extends State<AssessmentsScreen> {
  final _attemptController = TextEditingController();
  late final PendingAttemptStore _pendingAttemptStore;

  bool _checkingHealth = false;
  bool _loadingHistory = false;
  bool _checkingAttempt = false;
  bool? _healthy;
  String? _openingAttemptId;
  List<Map<String, dynamic>>? _history;
  String? _historyError;
  String? _attemptResult;
  late bool _assessmentConsentCurrent;

  @override
  void initState() {
    super.initState();
    _pendingAttemptStore =
        widget.pendingAttemptStore ?? SharedPreferencesPendingAttemptStore();
    _assessmentConsentCurrent = widget.profile.assessmentConsentCurrent;
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (mounted) _loadHistory();
    });
  }

  @override
  void dispose() {
    _attemptController.dispose();
    super.dispose();
  }

  Future<void> _checkHealth() async {
    setState(() {
      _checkingHealth = true;
      _healthy = null;
    });
    try {
      final healthy = await widget.apiService.checkHealth();
      if (!mounted) return;
      setState(() => _healthy = healthy);
    } on ApiException {
      if (!mounted) return;
      setState(() => _healthy = false);
    } finally {
      if (mounted) {
        setState(() => _checkingHealth = false);
      }
    }
  }

  Future<void> _startAssessment() async {
    if (!_assessmentConsentCurrent) {
      final accepted = await Navigator.of(context).push<bool>(
        MaterialPageRoute<bool>(
          builder: (_) =>
              AssessmentConsentScreen(apiService: widget.apiService),
        ),
      );
      if (accepted != true || !mounted) return;
      setState(() => _assessmentConsentCurrent = true);
      widget.onAssessmentConsentAccepted?.call();
    }
    if (!mounted) return;
    await Navigator.of(context).push(
      MaterialPageRoute<void>(
        builder: (_) => DescribeSituationScreen(
          apiService: widget.apiService,
          pendingAttemptStore: _pendingAttemptStore,
        ),
      ),
    );
  }

  Future<void> _loadHistory() async {
    setState(() {
      _loadingHistory = true;
      _historyError = null;
    });
    try {
      final history = await widget.apiService.getHistory();
      if (!mounted) return;
      setState(() => _history = history);
    } on ApiException catch (error) {
      if (!mounted) return;
      setState(() => _historyError = _withRequestId(error));
    } finally {
      if (mounted) {
        setState(() => _loadingHistory = false);
      }
    }
  }

  Future<void> _checkAttemptOwnership() async {
    final attemptId = _attemptController.text.trim();
    if (attemptId.isEmpty) {
      setState(() => _attemptResult = 'Enter an attempt ID first.');
      return;
    }

    setState(() {
      _checkingAttempt = true;
      _attemptResult = null;
    });
    try {
      final attempt = await widget.apiService.getAttempt(attemptId);
      if (!mounted) return;
      setState(
        () => _attemptResult =
            'Accessible to this account: '
            '${attempt['attempt_id'] ?? attemptId}',
      );
    } on ApiException catch (error) {
      if (!mounted) return;
      final result = error.statusCode == 404
          ? 'Not accessible to this account (404 Attempt not found).'
          : _withRequestId(error);
      setState(() => _attemptResult = result);
    } finally {
      if (mounted) {
        setState(() => _checkingAttempt = false);
      }
    }
  }

  Future<void> _openSavedAttempt(Map<String, dynamic> summary) async {
    final attemptId = summary['attempt_id'] as String? ?? '';
    if (attemptId.isEmpty) return;

    setState(() {
      _openingAttemptId = attemptId;
      _historyError = null;
    });

    try {
      final result = await widget.apiService.getAttempt(attemptId);
      if (!mounted) return;
      final attemptState = classifyAnalysisStatus(result['status']);
      await Navigator.of(context).push(
        MaterialPageRoute<void>(
          builder: (_) => attemptState == AnalysisAttemptState.succeeded
              ? AnalysisResultScreen(
                  apiService: widget.apiService,
                  result: result,
                )
              : AnalysisProcessingScreen(
                  apiService: widget.apiService,
                  attemptId: attemptId,
                  ownerUid: widget.user.uid,
                  pendingAttemptStore: _pendingAttemptStore,
                ),
        ),
      );
      if (mounted) await _loadHistory();
    } on ApiException catch (error) {
      if (!mounted) return;
      setState(() => _historyError = _withRequestId(error));
    } finally {
      if (mounted) {
        setState(() => _openingAttemptId = null);
      }
    }
  }

  Future<void> _deleteSavedAttempt(Map<String, dynamic> summary) async {
    final attemptId = summary['attempt_id'] as String? ?? '';
    if (attemptId.isEmpty) return;
    final confirmed =
        await showDialog<bool>(
          context: context,
          builder: (dialogContext) => AlertDialog(
            title: const Text('Delete this assessment?'),
            content: const Text(
              'The submitted incident, result, reasoning, and processing job '
              'will be permanently deleted. This cannot be undone.',
            ),
            actions: [
              TextButton(
                onPressed: () => Navigator.of(dialogContext).pop(false),
                child: const Text('Cancel'),
              ),
              FilledButton(
                style: FilledButton.styleFrom(
                  backgroundColor: Theme.of(context).colorScheme.error,
                ),
                onPressed: () => Navigator.of(dialogContext).pop(true),
                child: const Text('Delete'),
              ),
            ],
          ),
        ) ??
        false;
    if (!confirmed) return;

    try {
      await widget.apiService.deleteAttempt(attemptId);
      await _pendingAttemptStore.clear(
        ownerUid: widget.user.uid,
        attemptId: attemptId,
      );
      if (!mounted) return;
      setState(() {
        _history = _history
            ?.where((item) => item['attempt_id'] != attemptId)
            .toList(growable: false);
      });
      ScaffoldMessenger.of(
        context,
      ).showSnackBar(const SnackBar(content: Text('Assessment deleted.')));
    } on ApiException catch (error) {
      if (mounted) setState(() => _historyError = _withRequestId(error));
    }
  }

  Future<void> _clearAllHistory() async {
    final confirmed =
        await showDialog<bool>(
          context: context,
          builder: (dialogContext) => AlertDialog(
            title: const Text('Delete all assessment history?'),
            content: const Text(
              'Every saved assessment and active processing job will be '
              'permanently deleted. This cannot be undone.',
            ),
            actions: [
              TextButton(
                onPressed: () => Navigator.of(dialogContext).pop(false),
                child: const Text('Cancel'),
              ),
              FilledButton(
                style: FilledButton.styleFrom(
                  backgroundColor: Theme.of(context).colorScheme.error,
                ),
                onPressed: () => Navigator.of(dialogContext).pop(true),
                child: const Text('Delete all'),
              ),
            ],
          ),
        ) ??
        false;
    if (!confirmed) return;
    setState(() {
      _loadingHistory = true;
      _historyError = null;
    });
    try {
      await widget.apiService.clearHistory();
      final pending = await _pendingAttemptStore.readForUser(widget.user.uid);
      if (pending != null) {
        await _pendingAttemptStore.clear(
          ownerUid: widget.user.uid,
          attemptId: pending.attemptId,
        );
      }
      if (!mounted) return;
      setState(() => _history = const []);
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('All assessment history was deleted.')),
      );
    } on ApiException catch (error) {
      if (mounted) setState(() => _historyError = _withRequestId(error));
    } finally {
      if (mounted) setState(() => _loadingHistory = false);
    }
  }

  String _withRequestId(ApiException error) {
    if (error.requestId == null) return error.message;
    return '${error.message} (Request ID: ${error.requestId})';
  }

  @override
  Widget build(BuildContext context) {
    return SafeArea(
      top: false,
      child: ListView(
        padding: const EdgeInsets.all(20),
        children: [
          Text(
            'Your assessments',
            style: Theme.of(context).textTheme.headlineSmall,
          ),
          const SizedBox(height: 6),
          Text(
            'Start a new assessment or return to one saved in your private '
            'history.',
            style: Theme.of(context).textTheme.bodyLarge?.copyWith(
              color: Theme.of(context).colorScheme.onSurfaceVariant,
            ),
          ),
          const SizedBox(height: 20),
          if (widget.showDeveloperTools) ...[
            _AccountCard(user: widget.user, profile: widget.profile),
            const SizedBox(height: 16),
          ],
          Card(
            color: Theme.of(context).colorScheme.primaryContainer,
            child: Padding(
              padding: const EdgeInsets.all(20),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  Icon(
                    Icons.balance_outlined,
                    size: 42,
                    color: Theme.of(context).colorScheme.onPrimaryContainer,
                  ),
                  const SizedBox(height: 12),
                  Text(
                    'Start a new assessment',
                    style: Theme.of(context).textTheme.titleLarge,
                  ),
                  const SizedBox(height: 8),
                  const Text(
                    'First, Himikama will organize your description into '
                    'facts for you to review. Legal analysis begins only '
                    'after you confirm those details.',
                  ),
                  const SizedBox(height: 18),
                  FilledButton.icon(
                    key: const Key('start-assessment-button'),
                    onPressed: _startAssessment,
                    icon: const Icon(Icons.edit_note_outlined),
                    label: const Text('Describe your situation'),
                  ),
                ],
              ),
            ),
          ),
          if (widget.showDeveloperTools) ...[
            const SizedBox(height: 16),
            Card(
              child: Padding(
                padding: const EdgeInsets.all(18),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                    Text(
                      'Backend connection',
                      style: Theme.of(context).textTheme.titleMedium,
                    ),
                    const SizedBox(height: 8),
                    SelectableText(
                      widget.apiService.baseUrl,
                      style: Theme.of(context).textTheme.bodySmall,
                    ),
                    if (_healthy case final healthy?) ...[
                      const SizedBox(height: 12),
                      Text(
                        healthy
                            ? 'Backend health check passed.'
                            : 'Backend health check failed.',
                        style: TextStyle(
                          color: healthy
                              ? AppPalette.success
                              : Theme.of(context).colorScheme.error,
                          fontWeight: FontWeight.w600,
                        ),
                      ),
                    ],
                    const SizedBox(height: 14),
                    OutlinedButton.icon(
                      onPressed: _checkingHealth ? null : _checkHealth,
                      icon: _checkingHealth
                          ? const SizedBox.square(
                              dimension: 18,
                              child: CircularProgressIndicator(strokeWidth: 2),
                            )
                          : const Icon(Icons.health_and_safety_outlined),
                      label: const Text('Check backend'),
                    ),
                  ],
                ),
              ),
            ),
          ],
          const SizedBox(height: 16),
          Card(
            child: Padding(
              padding: const EdgeInsets.all(18),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  Row(
                    children: [
                      Expanded(
                        child: Text(
                          'Private history',
                          style: Theme.of(context).textTheme.titleMedium,
                        ),
                      ),
                      IconButton(
                        tooltip: 'Refresh history',
                        onPressed: _loadingHistory ? null : _loadHistory,
                        icon: _loadingHistory
                            ? const SizedBox.square(
                                dimension: 18,
                                child: CircularProgressIndicator(
                                  strokeWidth: 2,
                                ),
                              )
                            : const Icon(Icons.refresh),
                      ),
                    ],
                  ),
                  const SizedBox(height: 4),
                  const Text(
                    'Only assessments saved to this signed-in account appear '
                    'here. Tap an item to continue or review its result.',
                  ),
                  if (_loadingHistory && _history == null) ...[
                    const SizedBox(height: 20),
                    const Center(
                      child: Column(
                        children: [
                          CircularProgressIndicator(),
                          SizedBox(height: 12),
                          Text('Loading your private history…'),
                        ],
                      ),
                    ),
                  ],
                  if (_historyError case final error?) ...[
                    const SizedBox(height: 12),
                    Container(
                      padding: const EdgeInsets.all(12),
                      decoration: BoxDecoration(
                        color: Theme.of(context).colorScheme.errorContainer,
                        borderRadius: BorderRadius.circular(12),
                      ),
                      child: Row(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Icon(
                            Icons.error_outline,
                            color: Theme.of(
                              context,
                            ).colorScheme.onErrorContainer,
                          ),
                          const SizedBox(width: 10),
                          Expanded(child: Text(error)),
                        ],
                      ),
                    ),
                  ],
                  if (_history case final history?) ...[
                    if (history.isEmpty && !_loadingHistory) ...[
                      const SizedBox(height: 16),
                      const _EmptyHistory(),
                    ] else if (history.isNotEmpty) ...[
                      const SizedBox(height: 12),
                      Text(
                        '${history.length} saved '
                        '${history.length == 1 ? 'assessment' : 'assessments'}',
                        style: Theme.of(context).textTheme.labelLarge,
                      ),
                      const SizedBox(height: 10),
                      ...history.map((attempt) {
                        final attemptId =
                            attempt['attempt_id'] as String? ?? '';
                        final isOpening = _openingAttemptId == attemptId;
                        final view = AnalysisResultViewModel.fromJson(attempt);
                        return Padding(
                          padding: const EdgeInsets.only(bottom: 10),
                          child: _HistoryAttemptCard(
                            view: view,
                            opening: isOpening,
                            onTap: isOpening
                                ? null
                                : () => _openSavedAttempt(attempt),
                            onDelete: isOpening
                                ? null
                                : () => _deleteSavedAttempt(attempt),
                          ),
                        );
                      }),
                    ],
                  ],
                  if (_history?.isNotEmpty == true) ...[
                    const SizedBox(height: 2),
                    TextButton.icon(
                      key: const Key('clear-all-history'),
                      onPressed: _loadingHistory ? null : _clearAllHistory,
                      icon: const Icon(Icons.delete_sweep_outlined),
                      label: const Text('Delete all history'),
                    ),
                  ],
                ],
              ),
            ),
          ),
          if (widget.showDeveloperTools) ...[
            const SizedBox(height: 16),
            Card(
              child: ExpansionTile(
                title: const Text('Developer access check'),
                subtitle: const Text(
                  'Check whether this account owns a specific attempt.',
                ),
                childrenPadding: const EdgeInsets.fromLTRB(18, 0, 18, 18),
                children: [
                  const Align(
                    alignment: Alignment.centerLeft,
                    child: Text(
                      'The two-user isolation test has passed. This control '
                      'remains available for checking a saved attempt ID.',
                    ),
                  ),
                  const SizedBox(height: 18),
                  TextField(
                    controller: _attemptController,
                    autocorrect: false,
                    decoration: const InputDecoration(
                      labelText: 'Attempt ID',
                      hintText: 'Paste User A or User B attempt ID',
                      prefixIcon: Icon(Icons.fingerprint),
                    ),
                  ),
                  const SizedBox(height: 12),
                  OutlinedButton(
                    onPressed: _checkingAttempt ? null : _checkAttemptOwnership,
                    child: _checkingAttempt
                        ? const SizedBox.square(
                            dimension: 18,
                            child: CircularProgressIndicator(strokeWidth: 2),
                          )
                        : const Text('Check attempt access'),
                  ),
                  if (_attemptResult case final result?) ...[
                    const SizedBox(height: 12),
                    SelectableText(
                      result,
                      style: const TextStyle(fontWeight: FontWeight.w600),
                    ),
                  ],
                ],
              ),
            ),
            const SizedBox(height: 20),
            FilledButton.tonalIcon(
              onPressed: widget.firebaseAuth.signOut,
              icon: const Icon(Icons.logout),
              label: const Text('Sign out'),
            ),
          ],
          const SizedBox(height: 12),
          Text(
            'Himikama provides legal information and triage support, not '
            'legal advice or representation.',
            textAlign: TextAlign.center,
            style: Theme.of(context).textTheme.bodySmall,
          ),
        ],
      ),
    );
  }
}

class _EmptyHistory extends StatelessWidget {
  const _EmptyHistory();

  @override
  Widget build(BuildContext context) {
    final colors = Theme.of(context).colorScheme;
    return Container(
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: colors.surfaceContainerLow,
        borderRadius: BorderRadius.circular(14),
        border: Border.all(color: colors.outlineVariant),
      ),
      child: Row(
        children: [
          Icon(Icons.history_toggle_off, color: colors.primary),
          const SizedBox(width: 12),
          const Expanded(
            child: Text(
              'No saved assessments yet. Your completed and processing '
              'assessments will appear here.',
            ),
          ),
        ],
      ),
    );
  }
}

class _HistoryAttemptCard extends StatelessWidget {
  const _HistoryAttemptCard({
    required this.view,
    required this.opening,
    required this.onTap,
    required this.onDelete,
  });

  final AnalysisResultViewModel view;
  final bool opening;
  final VoidCallback? onTap;
  final VoidCallback? onDelete;

  @override
  Widget build(BuildContext context) {
    final style = _historyTone(view);
    final date = formatResultTimestamp(view.completedAt);
    final articles = <String>{
      ...view.supportedArticles,
      ...view.uncertainArticles,
    }.take(3).toList(growable: false);

    return Material(
      color: Theme.of(context).colorScheme.surface,
      borderRadius: BorderRadius.circular(14),
      child: InkWell(
        onTap: onTap,
        borderRadius: BorderRadius.circular(14),
        child: Container(
          padding: const EdgeInsets.all(14),
          decoration: BoxDecoration(
            borderRadius: BorderRadius.circular(14),
            border: Border.all(color: Theme.of(context).colorScheme.outline),
          ),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Container(
                    width: 40,
                    height: 40,
                    decoration: BoxDecoration(
                      color: style.background,
                      borderRadius: BorderRadius.circular(12),
                    ),
                    child: opening
                        ? const Padding(
                            padding: EdgeInsets.all(10),
                            child: CircularProgressIndicator(strokeWidth: 2),
                          )
                        : Icon(style.icon, color: style.foreground),
                  ),
                  const SizedBox(width: 12),
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(
                          view.overallLabel,
                          style: Theme.of(context).textTheme.titleSmall
                              ?.copyWith(fontWeight: FontWeight.w700),
                        ),
                        if (date.isNotEmpty) ...[
                          const SizedBox(height: 3),
                          Text(
                            date,
                            style: Theme.of(context).textTheme.bodySmall,
                          ),
                        ],
                      ],
                    ),
                  ),
                  const SizedBox(width: 8),
                  IconButton(
                    key: ValueKey('delete-attempt-${view.attemptId}'),
                    tooltip: 'Delete assessment',
                    onPressed: onDelete,
                    icon: const Icon(Icons.delete_outline),
                  ),
                ],
              ),
              const SizedBox(height: 10),
              Wrap(
                spacing: 7,
                runSpacing: 7,
                children: [
                  _HistoryPill(
                    label: view.statusLabel,
                    foreground: style.foreground,
                  ),
                  if (view.confidenceLevel.isNotEmpty)
                    _HistoryPill(
                      label: '${view.confidenceLevel} confidence',
                      foreground: AppPalette.neutral,
                    ),
                  ...articles.map(
                    (article) => _HistoryPill(
                      label: formatArticleLabel(article),
                      foreground: AppPalette.primaryDark,
                    ),
                  ),
                ],
              ),
            ],
          ),
        ),
      ),
    );
  }
}

class _HistoryPill extends StatelessWidget {
  const _HistoryPill({required this.label, required this.foreground});

  final String label;
  final Color foreground;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 9, vertical: 5),
      decoration: BoxDecoration(
        color: foreground.withValues(alpha: 0.08),
        borderRadius: BorderRadius.circular(999),
      ),
      child: Text(
        label,
        style: TextStyle(
          color: foreground,
          fontSize: 11,
          fontWeight: FontWeight.w700,
        ),
      ),
    );
  }
}

({Color foreground, Color background, IconData icon}) _historyTone(
  AnalysisResultViewModel view,
) {
  if (view.isProcessing) {
    return (
      foreground: AppPalette.primaryDark,
      background: AppPalette.primaryContainer.withValues(alpha: 0.55),
      icon: Icons.hourglass_top_outlined,
    );
  }
  return switch (view.tone) {
    AssessmentTone.positive => (
      foreground: AppPalette.primary,
      background: AppPalette.primaryContainer,
      icon: Icons.balance_outlined,
    ),
    AssessmentTone.caution => (
      foreground: AppPalette.warning,
      background: AppPalette.warningContainer.withValues(alpha: 0.72),
      icon: Icons.warning_amber_outlined,
    ),
    AssessmentTone.negative => (
      foreground: const Color(0xFF9C2F2F),
      background: const Color(0xFFFCE8E8),
      icon: Icons.error_outline,
    ),
    AssessmentTone.neutral => (
      foreground: AppPalette.neutral,
      background: AppPalette.neutralContainer,
      icon: Icons.fact_check_outlined,
    ),
  };
}

class _AccountCard extends StatelessWidget {
  const _AccountCard({required this.user, required this.profile});

  final User user;
  final UserProfile profile;

  @override
  Widget build(BuildContext context) {
    return Card(
      color: Theme.of(context).colorScheme.primaryContainer,
      child: Padding(
        padding: const EdgeInsets.all(18),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Icon(
                  Icons.verified_user_outlined,
                  color: Theme.of(context).colorScheme.onPrimaryContainer,
                ),
                const SizedBox(width: 10),
                Text(
                  'Verified account',
                  style: Theme.of(context).textTheme.titleMedium?.copyWith(
                    color: Theme.of(context).colorScheme.onPrimaryContainer,
                    fontWeight: FontWeight.w700,
                  ),
                ),
              ],
            ),
            const SizedBox(height: 12),
            SelectableText(profile.email),
            const SizedBox(height: 4),
            SelectableText(
              'UID: ${user.uid}',
              style: Theme.of(context).textTheme.bodySmall,
            ),
          ],
        ),
      ),
    );
  }
}
