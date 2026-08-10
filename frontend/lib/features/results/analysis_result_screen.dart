import 'package:flutter/material.dart';

import '../../core/api_service.dart';
import '../../core/app_theme.dart';
import 'analysis_result_view_model.dart';
import 'reasoning_trace_screen.dart';

class AnalysisResultScreen extends StatelessWidget {
  const AnalysisResultScreen({
    required this.result,
    this.apiService,
    super.key,
  });

  final ApiService? apiService;
  final Map<String, dynamic> result;

  @override
  Widget build(BuildContext context) {
    final view = AnalysisResultViewModel.fromJson(result);
    final supported = view.assessmentsFor('supported');
    final uncertain = view.assessmentsFor('weak_or_uncertain');
    final rejected = view.assessmentsFor('rejected');

    return Scaffold(
      appBar: AppBar(title: const Text('Your assessment')),
      body: SafeArea(
        child: ListView(
          padding: const EdgeInsets.fromLTRB(20, 8, 20, 32),
          children: [
            Text(
              'Himikama assessment',
              style: Theme.of(
                context,
              ).textTheme.headlineSmall?.copyWith(fontWeight: FontWeight.w800),
            ),
            const SizedBox(height: 6),
            Text(
              'A preliminary review of the facts you confirmed.',
              style: Theme.of(context).textTheme.bodyLarge?.copyWith(
                color: Theme.of(context).colorScheme.onSurfaceVariant,
              ),
            ),
            const SizedBox(height: 18),
            _OutcomeCard(view: view),
            if (supported.isNotEmpty) ...[
              const SizedBox(height: 16),
              _ArticleSection(
                key: const Key('supported-articles-section'),
                title: 'Rights that may be engaged',
                subtitle:
                    'These articles remained supported after Himikama’s '
                    'cross-checks. This is not a final legal finding.',
                icon: Icons.balance_outlined,
                foreground: AppPalette.primaryDark,
                background: AppPalette.primaryContainer,
                assessments: supported,
              ),
            ],
            if (uncertain.isNotEmpty) ...[
              const SizedBox(height: 16),
              _ArticleSection(
                key: const Key('uncertain-articles-section'),
                title: 'Rights needing more information',
                subtitle:
                    'These articles may be relevant, but the current facts or '
                    'legal support are not strong enough for a clear result.',
                icon: Icons.help_outline,
                foreground: AppPalette.warning,
                background: AppPalette.warningContainer,
                assessments: uncertain,
              ),
            ],
            if (rejected.isNotEmpty) ...[
              const SizedBox(height: 16),
              _ArticleSection(
                key: const Key('rejected-articles-section'),
                title: 'Rights not supported by this review',
                subtitle:
                    'These articles were considered but did not remain '
                    'supported after the full analysis.',
                icon: Icons.remove_circle_outline,
                foreground: AppPalette.neutral,
                background: AppPalette.neutralContainer,
                assessments: rejected,
                initiallyExpanded: false,
              ),
            ],
            if (view.keyStrengths.isNotEmpty ||
                view.keyWeaknesses.isNotEmpty) ...[
              const SizedBox(height: 16),
              _FactorsSection(view: view),
            ],
            if (view.similarCases.isNotEmpty) ...[
              const SizedBox(height: 16),
              _SimilarCasesSection(cases: view.similarCases),
            ],
            if (view.confidenceLevel.isNotEmpty ||
                view.confidenceExplanation.isNotEmpty ||
                view.confidenceFlags.isNotEmpty) ...[
              const SizedBox(height: 16),
              _ConfidenceCard(view: view),
            ],
            const SizedBox(height: 16),
            _NextStepsCard(steps: view.nextSteps),
            if (view.fullExplanation.isNotEmpty) ...[
              const SizedBox(height: 16),
              _FullExplanationCard(text: view.fullExplanation),
            ],
            const SizedBox(height: 16),
            _AssessmentDetailsCard(view: view),
            if (view.reasoningAvailable &&
                view.attemptId.isNotEmpty &&
                apiService != null) ...[
              const SizedBox(height: 16),
              OutlinedButton.icon(
                key: const Key('show-reasoning-button'),
                onPressed: () {
                  Navigator.of(context).push(
                    MaterialPageRoute<void>(
                      builder: (_) => ReasoningTraceScreen(
                        apiService: apiService!,
                        attemptId: view.attemptId,
                      ),
                    ),
                  );
                },
                icon: const Icon(Icons.account_tree_outlined),
                label: const Text('Show detailed reasoning'),
              ),
            ],
            const SizedBox(height: 16),
            _DisclaimerCard(disclaimer: view.disclaimer),
            const SizedBox(height: 20),
            FilledButton.icon(
              onPressed: () {
                Navigator.of(context).popUntil((route) => route.isFirst);
              },
              icon: const Icon(Icons.home_outlined),
              label: const Text('Return home'),
            ),
          ],
        ),
      ),
    );
  }
}

class _OutcomeCard extends StatelessWidget {
  const _OutcomeCard({required this.view});

  final AnalysisResultViewModel view;

  @override
  Widget build(BuildContext context) {
    final style = _toneStyle(view.tone);
    return Card(
      key: const Key('assessment-outcome-card'),
      color: style.background,
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(18),
        side: BorderSide(color: style.foreground.withValues(alpha: 0.3)),
      ),
      child: Padding(
        padding: const EdgeInsets.all(20),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Container(
                  width: 46,
                  height: 46,
                  decoration: BoxDecoration(
                    color: Colors.white.withValues(alpha: 0.7),
                    borderRadius: BorderRadius.circular(14),
                  ),
                  child: Icon(style.icon, color: style.foreground),
                ),
                const SizedBox(width: 14),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        view.overallLabel,
                        key: const Key('overall-assessment-label'),
                        style: Theme.of(context).textTheme.titleLarge?.copyWith(
                          color: style.foreground,
                          fontWeight: FontWeight.w800,
                        ),
                      ),
                      const SizedBox(height: 6),
                      Text(view.overallDescription),
                    ],
                  ),
                ),
              ],
            ),
            if (view.confidenceLevel.isNotEmpty) ...[
              const SizedBox(height: 16),
              _LabelPill(
                icon: Icons.insights_outlined,
                label: '${_titleCase(view.confidenceLevel)} confidence',
                foreground: style.foreground,
                background: Colors.white.withValues(alpha: 0.75),
              ),
            ],
          ],
        ),
      ),
    );
  }
}

class _ArticleSection extends StatelessWidget {
  const _ArticleSection({
    required this.title,
    required this.subtitle,
    required this.icon,
    required this.foreground,
    required this.background,
    required this.assessments,
    this.initiallyExpanded = true,
    super.key,
  });

  final String title;
  final String subtitle;
  final IconData icon;
  final Color foreground;
  final Color background;
  final List<ArticleAssessmentView> assessments;
  final bool initiallyExpanded;

  @override
  Widget build(BuildContext context) {
    return Card(
      child: ExpansionTile(
        initiallyExpanded: initiallyExpanded,
        tilePadding: const EdgeInsets.fromLTRB(18, 8, 18, 8),
        childrenPadding: const EdgeInsets.fromLTRB(18, 0, 18, 18),
        leading: Container(
          width: 42,
          height: 42,
          decoration: BoxDecoration(
            color: background,
            borderRadius: BorderRadius.circular(12),
          ),
          child: Icon(icon, color: foreground),
        ),
        title: Text(title, style: const TextStyle(fontWeight: FontWeight.w800)),
        subtitle: Text('${assessments.length} article(s)'),
        children: [
          Align(alignment: Alignment.centerLeft, child: Text(subtitle)),
          const SizedBox(height: 14),
          ...assessments.map(
            (assessment) => Padding(
              padding: const EdgeInsets.only(bottom: 10),
              child: _ArticleAssessmentCard(
                assessment: assessment,
                foreground: foreground,
                background: background,
              ),
            ),
          ),
        ],
      ),
    );
  }
}

class _ArticleAssessmentCard extends StatelessWidget {
  const _ArticleAssessmentCard({
    required this.assessment,
    required this.foreground,
    required this.background,
  });

  final ArticleAssessmentView assessment;
  final Color foreground;
  final Color background;

  @override
  Widget build(BuildContext context) {
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.all(14),
      decoration: BoxDecoration(
        color: background.withValues(alpha: 0.45),
        borderRadius: BorderRadius.circular(14),
        border: Border.all(color: foreground.withValues(alpha: 0.22)),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Wrap(
            spacing: 8,
            runSpacing: 8,
            crossAxisAlignment: WrapCrossAlignment.center,
            children: [
              Text(
                formatArticleLabel(assessment.article),
                style: Theme.of(context).textTheme.titleMedium?.copyWith(
                  color: foreground,
                  fontWeight: FontWeight.w800,
                ),
              ),
              if (assessment.confidence.isNotEmpty)
                _LabelPill(
                  label: '${_titleCase(assessment.confidence)} confidence',
                  foreground: foreground,
                  background: Colors.white.withValues(alpha: 0.8),
                ),
            ],
          ),
          if (assessment.reason.isNotEmpty) ...[
            const SizedBox(height: 8),
            Text(assessment.reason),
          ],
          if (assessment.supportingCaseIds.isNotEmpty) ...[
            const SizedBox(height: 8),
            Text(
              'Supporting case IDs: ${assessment.supportingCaseIds.join(', ')}',
              style: Theme.of(context).textTheme.bodySmall,
            ),
          ],
        ],
      ),
    );
  }
}

class _FactorsSection extends StatelessWidget {
  const _FactorsSection({required this.view});

  final AnalysisResultViewModel view;

  @override
  Widget build(BuildContext context) {
    return _SectionCard(
      title: 'Key factors in this assessment',
      icon: Icons.fact_check_outlined,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          if (view.keyStrengths.isNotEmpty)
            _BulletGroup(
              title: 'Factors supporting the claim',
              icon: Icons.check_circle_outline,
              color: AppPalette.success,
              items: view.keyStrengths,
            ),
          if (view.keyStrengths.isNotEmpty && view.keyWeaknesses.isNotEmpty)
            const Padding(
              padding: EdgeInsets.symmetric(vertical: 12),
              child: Divider(),
            ),
          if (view.keyWeaknesses.isNotEmpty)
            _BulletGroup(
              title: 'Weaknesses or uncertainties',
              icon: Icons.warning_amber_outlined,
              color: AppPalette.warning,
              items: view.keyWeaknesses,
            ),
        ],
      ),
    );
  }
}

class _BulletGroup extends StatelessWidget {
  const _BulletGroup({
    required this.title,
    required this.icon,
    required this.color,
    required this.items,
  });

  final String title;
  final IconData icon;
  final Color color;
  final List<String> items;

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Row(
          children: [
            Icon(icon, size: 20, color: color),
            const SizedBox(width: 8),
            Expanded(
              child: Text(
                title,
                style: Theme.of(
                  context,
                ).textTheme.titleSmall?.copyWith(fontWeight: FontWeight.w800),
              ),
            ),
          ],
        ),
        const SizedBox(height: 8),
        ...items.map(
          (item) => Padding(
            padding: const EdgeInsets.only(bottom: 8),
            child: Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Padding(
                  padding: const EdgeInsets.only(top: 7),
                  child: Container(
                    width: 6,
                    height: 6,
                    decoration: BoxDecoration(
                      color: color,
                      shape: BoxShape.circle,
                    ),
                  ),
                ),
                const SizedBox(width: 10),
                Expanded(child: Text(item)),
              ],
            ),
          ),
        ),
      ],
    );
  }
}

class _SimilarCasesSection extends StatelessWidget {
  const _SimilarCasesSection({required this.cases});

  final List<SimilarCaseView> cases;

  @override
  Widget build(BuildContext context) {
    return _SectionCard(
      title: 'Similar cases retrieved',
      icon: Icons.menu_book_outlined,
      subtitle:
          'These cases informed the comparison. Similarity does not guarantee '
          'the same legal outcome.',
      child: Column(
        children: cases
            .map(
              (item) => Padding(
                padding: const EdgeInsets.only(bottom: 10),
                child: _SimilarCaseCard(item: item),
              ),
            )
            .toList(growable: false),
      ),
    );
  }
}

class _SimilarCaseCard extends StatelessWidget {
  const _SimilarCaseCard({required this.item});

  final SimilarCaseView item;

  @override
  Widget build(BuildContext context) {
    final title = item.caseName.isNotEmpty
        ? item.caseName
        : 'Case ${item.caseId}';
    final citation = [
      item.caseNumber,
      item.year,
    ].where((value) => value.isNotEmpty).join(' • ');

    return Container(
      width: double.infinity,
      padding: const EdgeInsets.all(14),
      decoration: BoxDecoration(
        color: Theme.of(context).colorScheme.surfaceContainerLow,
        borderRadius: BorderRadius.circular(14),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(title, style: const TextStyle(fontWeight: FontWeight.w800)),
          if (citation.isNotEmpty) ...[
            const SizedBox(height: 4),
            Text(citation),
          ],
          if (item.judgment.isNotEmpty || item.articlesCited.isNotEmpty) ...[
            const SizedBox(height: 8),
            Wrap(
              spacing: 8,
              runSpacing: 8,
              children: [
                if (item.judgment.isNotEmpty)
                  _LabelPill(
                    label: _judgmentLabel(item.judgment),
                    foreground: AppPalette.neutral,
                    background: AppPalette.neutralContainer,
                  ),
                if (item.articlesCited.isNotEmpty)
                  _LabelPill(
                    label: 'Articles ${item.articlesCited}',
                    foreground: AppPalette.primaryDark,
                    background: AppPalette.primaryContainer,
                  ),
              ],
            ),
          ],
        ],
      ),
    );
  }
}

class _ConfidenceCard extends StatelessWidget {
  const _ConfidenceCard({required this.view});

  final AnalysisResultViewModel view;

  @override
  Widget build(BuildContext context) {
    return _SectionCard(
      title: 'Confidence in this assessment',
      icon: Icons.insights_outlined,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          if (view.confidenceLevel.isNotEmpty)
            _LabelPill(
              label: '${_titleCase(view.confidenceLevel)} confidence',
              foreground: AppPalette.primaryDark,
              background: AppPalette.primaryContainer,
            ),
          if (view.confidenceExplanation.isNotEmpty) ...[
            const SizedBox(height: 10),
            Text(view.confidenceExplanation),
          ],
          if (view.confidenceFlags.isNotEmpty) ...[
            const SizedBox(height: 12),
            Text(
              'Important checks',
              style: Theme.of(
                context,
              ).textTheme.titleSmall?.copyWith(fontWeight: FontWeight.w800),
            ),
            const SizedBox(height: 8),
            Wrap(
              spacing: 8,
              runSpacing: 8,
              children: view.confidenceFlags
                  .map((flag) => Chip(label: Text(_humanize(flag))))
                  .toList(growable: false),
            ),
          ],
        ],
      ),
    );
  }
}

class _NextStepsCard extends StatelessWidget {
  const _NextStepsCard({required this.steps});

  final List<String> steps;

  @override
  Widget build(BuildContext context) {
    return _SectionCard(
      key: const Key('next-steps-section'),
      title: 'What you can do next',
      icon: Icons.route_outlined,
      child: Column(
        children: [
          for (var index = 0; index < steps.length; index++)
            Padding(
              padding: EdgeInsets.only(
                bottom: index == steps.length - 1 ? 0 : 12,
              ),
              child: Row(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Container(
                    width: 28,
                    height: 28,
                    alignment: Alignment.center,
                    decoration: const BoxDecoration(
                      color: AppPalette.primaryContainer,
                      shape: BoxShape.circle,
                    ),
                    child: Text(
                      '${index + 1}',
                      style: const TextStyle(
                        color: AppPalette.primaryDark,
                        fontWeight: FontWeight.w800,
                      ),
                    ),
                  ),
                  const SizedBox(width: 12),
                  Expanded(child: Text(steps[index])),
                ],
              ),
            ),
        ],
      ),
    );
  }
}

class _FullExplanationCard extends StatelessWidget {
  const _FullExplanationCard({required this.text});

  final String text;

  @override
  Widget build(BuildContext context) {
    return Card(
      child: ExpansionTile(
        key: const Key('full-explanation-expansion'),
        initiallyExpanded: false,
        leading: const Icon(Icons.description_outlined),
        title: const Text(
          'View full explanation',
          style: TextStyle(fontWeight: FontWeight.w800),
        ),
        subtitle: const Text('Read Himikama’s complete plain-language answer'),
        childrenPadding: const EdgeInsets.fromLTRB(18, 0, 18, 18),
        children: [
          Align(alignment: Alignment.centerLeft, child: SelectableText(text)),
        ],
      ),
    );
  }
}

class _AssessmentDetailsCard extends StatelessWidget {
  const _AssessmentDetailsCard({required this.view});

  final AnalysisResultViewModel view;

  @override
  Widget build(BuildContext context) {
    final completedAt = formatResultTimestamp(view.completedAt);
    final startedAt = formatResultTimestamp(view.startedAt);
    return Card(
      child: ExpansionTile(
        initiallyExpanded: false,
        leading: const Icon(Icons.info_outline),
        title: const Text(
          'Assessment details',
          style: TextStyle(fontWeight: FontWeight.w800),
        ),
        subtitle: Text(view.statusLabel),
        childrenPadding: const EdgeInsets.fromLTRB(18, 0, 18, 18),
        children: [
          if (completedAt.isNotEmpty)
            _DetailRow(label: 'Completed', value: completedAt),
          if (startedAt.isNotEmpty)
            _DetailRow(label: 'Started', value: startedAt),
          if (view.attemptId.isNotEmpty)
            _DetailRow(
              label: 'Attempt ID',
              value: view.attemptId,
              selectable: true,
            ),
        ],
      ),
    );
  }
}

class _DetailRow extends StatelessWidget {
  const _DetailRow({
    required this.label,
    required this.value,
    this.selectable = false,
  });

  final String label;
  final String value;
  final bool selectable;

  @override
  Widget build(BuildContext context) {
    final valueWidget = selectable ? SelectableText(value) : Text(value);
    return Padding(
      padding: const EdgeInsets.only(bottom: 10),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          SizedBox(
            width: 88,
            child: Text(
              label,
              style: const TextStyle(fontWeight: FontWeight.w700),
            ),
          ),
          const SizedBox(width: 10),
          Expanded(child: valueWidget),
        ],
      ),
    );
  }
}

class _DisclaimerCard extends StatelessWidget {
  const _DisclaimerCard({required this.disclaimer});

  final String disclaimer;

  @override
  Widget build(BuildContext context) {
    return Container(
      key: const Key('legal-disclaimer'),
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: AppPalette.neutralContainer,
        borderRadius: BorderRadius.circular(16),
        border: Border.all(color: AppPalette.outline),
      ),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const Icon(Icons.gavel_outlined, color: AppPalette.neutral),
          const SizedBox(width: 12),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                const Text(
                  'Important legal notice',
                  style: TextStyle(fontWeight: FontWeight.w800),
                ),
                const SizedBox(height: 6),
                Text(disclaimer),
              ],
            ),
          ),
        ],
      ),
    );
  }
}

class _SectionCard extends StatelessWidget {
  const _SectionCard({
    required this.title,
    required this.icon,
    required this.child,
    this.subtitle,
    super.key,
  });

  final String title;
  final String? subtitle;
  final IconData icon;
  final Widget child;

  @override
  Widget build(BuildContext context) {
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(18),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Icon(icon, color: AppPalette.primaryDark),
                const SizedBox(width: 10),
                Expanded(
                  child: Text(
                    title,
                    style: Theme.of(context).textTheme.titleMedium?.copyWith(
                      fontWeight: FontWeight.w800,
                    ),
                  ),
                ),
              ],
            ),
            if (subtitle case final text?) ...[
              const SizedBox(height: 8),
              Text(text),
            ],
            const SizedBox(height: 14),
            child,
          ],
        ),
      ),
    );
  }
}

class _LabelPill extends StatelessWidget {
  const _LabelPill({
    required this.label,
    required this.foreground,
    required this.background,
    this.icon,
  });

  final String label;
  final Color foreground;
  final Color background;
  final IconData? icon;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
      decoration: BoxDecoration(
        color: background,
        borderRadius: BorderRadius.circular(999),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          if (icon case final value?) ...[
            Icon(value, size: 16, color: foreground),
            const SizedBox(width: 6),
          ],
          Text(
            label,
            style: TextStyle(
              color: foreground,
              fontSize: 12,
              fontWeight: FontWeight.w800,
            ),
          ),
        ],
      ),
    );
  }
}

({Color foreground, Color background, IconData icon}) _toneStyle(
  AssessmentTone tone,
) {
  return switch (tone) {
    AssessmentTone.positive => (
      foreground: AppPalette.success,
      background: AppPalette.successContainer,
      icon: Icons.check_circle_outline,
    ),
    AssessmentTone.caution => (
      foreground: AppPalette.warning,
      background: AppPalette.warningContainer,
      icon: Icons.warning_amber_outlined,
    ),
    AssessmentTone.negative => (
      foreground: const Color(0xFF9C2F2F),
      background: const Color(0xFFFCE1E1),
      icon: Icons.error_outline,
    ),
    AssessmentTone.neutral => (
      foreground: AppPalette.primaryDark,
      background: AppPalette.primaryContainer,
      icon: Icons.fact_check_outlined,
    ),
  };
}

String _titleCase(String value) {
  if (value.isEmpty) return value;
  return '${value[0].toUpperCase()}${value.substring(1)}';
}

String _humanize(String value) {
  return value
      .split('_')
      .where((part) => part.isNotEmpty)
      .map(_titleCase)
      .join(' ');
}

String _judgmentLabel(String value) {
  return switch (value.trim().toUpperCase()) {
    'VIOLATED' => 'Violation found',
    'NOT_VIOLATED' => 'No violation found',
    'PARTIAL' => 'Partial outcome',
    _ => _humanize(value.toLowerCase()),
  };
}
