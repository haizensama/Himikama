class ArticleAssessmentView {
  const ArticleAssessmentView({
    required this.article,
    required this.status,
    required this.reason,
    required this.confidence,
    required this.supportingCaseIds,
  });

  final String article;
  final String status;
  final String reason;
  final String confidence;
  final List<String> supportingCaseIds;
}

class SimilarCaseView {
  const SimilarCaseView({
    required this.caseId,
    required this.caseName,
    required this.caseNumber,
    required this.year,
    required this.judgment,
    required this.articlesCited,
  });

  final String caseId;
  final String caseName;
  final String caseNumber;
  final String year;
  final String judgment;
  final String articlesCited;
}

enum AssessmentTone { positive, caution, negative, neutral }

class AnalysisResultViewModel {
  const AnalysisResultViewModel({
    required this.attemptId,
    required this.status,
    required this.overallAssessment,
    required this.confidenceLevel,
    required this.confidenceExplanation,
    required this.confidenceFlags,
    required this.supportedArticles,
    required this.uncertainArticles,
    required this.rejectedArticles,
    required this.articleAssessments,
    required this.keyStrengths,
    required this.keyWeaknesses,
    required this.similarCases,
    required this.fullExplanation,
    required this.disclaimer,
    required this.reasoningAvailable,
    required this.startedAt,
    required this.completedAt,
  });

  final String attemptId;
  final String status;
  final String overallAssessment;
  final String confidenceLevel;
  final String confidenceExplanation;
  final List<String> confidenceFlags;
  final List<String> supportedArticles;
  final List<String> uncertainArticles;
  final List<String> rejectedArticles;
  final List<ArticleAssessmentView> articleAssessments;
  final List<String> keyStrengths;
  final List<String> keyWeaknesses;
  final List<SimilarCaseView> similarCases;
  final String fullExplanation;
  final String disclaimer;
  final bool reasoningAvailable;
  final String startedAt;
  final String completedAt;

  factory AnalysisResultViewModel.fromJson(Map<String, dynamic> json) {
    final summary = _map(json['summary']);
    final structured = _map(
      summary['structured_assessment'] ?? json['structured_assessment'],
    );
    final confidence = _map(json['confidence']);
    final timestamps = _map(json['timestamps']);
    final status = _text(json['status']).toLowerCase();

    var overallAssessment = _firstText([
      summary['overall_assessment'],
      structured['overall_assessment'],
      json['overall_assessment'],
    ]).toLowerCase();
    if (overallAssessment.isEmpty && _hardGateStatuses.contains(status)) {
      overallAssessment = status;
    }

    final supportedArticles = _firstStringList([
      summary['final_potentially_violated_articles'],
      structured['final_potentially_violated_articles'],
      json['final_potentially_violated_articles'],
    ]);
    final uncertainArticles = _firstStringList([
      summary['final_weak_or_uncertain_articles'],
      structured['final_weak_or_uncertain_articles'],
      json['final_weak_or_uncertain_articles'],
    ]);
    final rejectedArticles = _firstStringList([
      summary['final_rejected_articles'],
      structured['final_rejected_articles'],
      json['final_rejected_articles'],
    ]);

    final assessments =
        _firstList([
              summary['article_assessments'],
              structured['article_assessments'],
              json['article_assessments'],
            ])
            .whereType<Map>()
            .map((raw) {
              final item = raw.cast<String, dynamic>();
              return ArticleAssessmentView(
                article: _text(item['article']),
                status: _text(item['status']).toLowerCase(),
                reason: _text(item['reason']),
                confidence: _text(item['confidence']).toLowerCase(),
                supportingCaseIds: _strings(item['supporting_case_ids']),
              );
            })
            .where((item) => item.article.isNotEmpty)
            .toList(growable: false);

    final similarCases =
        _firstList([summary['similar_cases'], json['similar_cases']])
            .whereType<Map>()
            .map((raw) {
              final item = raw.cast<String, dynamic>();
              return SimilarCaseView(
                caseId: _text(item['case_id']),
                caseName: _text(item['case_name']),
                caseNumber: _text(item['case_number']),
                year: _text(item['year']),
                judgment: _text(item['judgment']),
                articlesCited: _stringsOrText(item['articles_cited']),
              );
            })
            .where((item) {
              return item.caseId.isNotEmpty || item.caseName.isNotEmpty;
            })
            .toList(growable: false);

    final answerParts = _splitAnswer(_text(json['main_answer']));

    return AnalysisResultViewModel(
      attemptId: _text(json['attempt_id']),
      status: status,
      overallAssessment: overallAssessment,
      confidenceLevel: _firstText([
        confidence['level'],
        json['confidence_level'],
      ]).toLowerCase(),
      confidenceExplanation: _text(confidence['explanation']),
      confidenceFlags: _strings(confidence['flags'] ?? json['flags']),
      supportedArticles: supportedArticles,
      uncertainArticles: uncertainArticles,
      rejectedArticles: rejectedArticles,
      articleAssessments: assessments,
      keyStrengths: _firstStringList([
        structured['key_strengths'],
        summary['key_strengths'],
        json['key_strengths'],
      ]),
      keyWeaknesses: _firstStringList([
        structured['key_weaknesses'],
        summary['key_weaknesses'],
        json['key_weaknesses'],
      ]),
      similarCases: similarCases,
      fullExplanation: answerParts.explanation,
      disclaimer: answerParts.disclaimer,
      reasoningAvailable: json['reasoning_available'] as bool? ?? false,
      startedAt: _firstText([timestamps['started_at'], json['started_at']]),
      completedAt: _firstText([
        timestamps['completed_at'],
        json['completed_at'],
        json['updated_at'],
        json['created_at'],
      ]),
    );
  }

  static const _hardGateStatuses = {'time_barred', 'not_state_actor'};

  String get overallLabel =>
      assessmentLabel(overallAssessment: overallAssessment, status: status);

  String get overallDescription => assessmentDescription(
    overallAssessment: overallAssessment,
    status: status,
  );

  AssessmentTone get tone =>
      assessmentTone(overallAssessment: overallAssessment, status: status);

  String get statusLabel {
    return switch (status) {
      'processing' || 'pending' || 'running' => 'Analysis in progress',
      'complete' => 'Assessment complete',
      'time_barred' => 'Filing-time concern',
      'not_state_actor' => 'State-action requirement not met',
      'failed' => 'Assessment failed',
      _ => _humanize(status.isEmpty ? 'unknown' : status),
    };
  }

  bool get isProcessing =>
      const {'processing', 'pending', 'running'}.contains(status);

  List<String> get nextSteps {
    return switch (overallAssessment) {
      'time_barred' => const [
        'Speak with a qualified Sri Lankan lawyer promptly about the filing-time issue and whether any other remedy may be available.',
        'Keep the exact incident date and any documents that explain a delay.',
      ],
      'not_state_actor' => const [
        'Ask a qualified lawyer whether the person or organization was exercising state-derived power in your circumstances.',
        'Discuss whether another legal process may be more appropriate.',
      ],
      'likely_viable' => const [
        'Consult a qualified Sri Lankan lawyer promptly, especially because Fundamental Rights filings are time-sensitive.',
        'Collect dates, names, messages, medical records, photographs, and official documents relevant to the incident.',
        'Take this assessment and the listed similar cases to that consultation.',
      ],
      'weak_or_uncertain' => const [
        'Collect the missing facts or evidence highlighted above.',
        'Ask a qualified Sri Lankan lawyer to review the uncertain points and any filing deadline.',
      ],
      'not_viable' => const [
        'Review the weaknesses above and check whether any important fact or document was omitted.',
        'A qualified lawyer can advise whether a different legal remedy may apply.',
      ],
      _ => const [
        'Keep a copy of the incident details and supporting documents.',
        'Consult a qualified Sri Lankan lawyer before taking legal action.',
      ],
    };
  }

  List<ArticleAssessmentView> assessmentsFor(String wantedStatus) {
    final matches = articleAssessments
        .where((item) => item.status == wantedStatus)
        .toList(growable: true);
    final bucket = switch (wantedStatus) {
      'supported' => supportedArticles,
      'weak_or_uncertain' => uncertainArticles,
      'rejected' => rejectedArticles,
      _ => const <String>[],
    };
    final present = matches.map((item) => item.article).toSet();
    for (final article in bucket) {
      if (present.add(article)) {
        matches.add(
          ArticleAssessmentView(
            article: article,
            status: wantedStatus,
            reason: '',
            confidence: '',
            supportingCaseIds: const [],
          ),
        );
      }
    }
    return List.unmodifiable(matches);
  }
}

String assessmentLabel({
  required String overallAssessment,
  required String status,
}) {
  final value = overallAssessment.trim().toLowerCase();
  return switch (value) {
    'likely_viable' => 'Potentially viable claim',
    'weak_or_uncertain' => 'Weak or uncertain claim',
    'not_viable' => 'Not likely viable on the current facts',
    'time_barred' => 'Filing-time concern identified',
    'not_state_actor' => 'State-action requirement not met',
    _
        when status == 'processing' ||
            status == 'pending' ||
            status == 'running' =>
      'Analysis in progress',
    _ when status == 'failed' => 'Assessment could not be completed',
    _ => 'Assessment completed',
  };
}

String assessmentDescription({
  required String overallAssessment,
  required String status,
}) {
  return switch (overallAssessment.trim().toLowerCase()) {
    'likely_viable' =>
      'The facts and retrieved legal materials support further review of a possible Fundamental Rights claim.',
    'weak_or_uncertain' =>
      'Some legal elements may be engaged, but important facts or support remain uncertain.',
    'not_viable' =>
      'The information currently available does not support a strong Fundamental Rights claim.',
    'time_barred' =>
      'The incident appears to fall outside Himikama’s 30-day filing screen, so the legal chain stopped early.',
    'not_state_actor' =>
      'The alleged actor does not appear to meet the state-action requirement used by the Fundamental Rights process.',
    _
        when status == 'processing' ||
            status == 'pending' ||
            status == 'running' =>
      'Himikama is still reviewing the confirmed facts.',
    _ when status == 'failed' =>
      'The backend could not finish this attempt. The attempt ID remains available for review.',
    _ =>
      'Review the factors below and consult a qualified lawyer before acting.',
  };
}

AssessmentTone assessmentTone({
  required String overallAssessment,
  required String status,
}) {
  return switch (overallAssessment.trim().toLowerCase()) {
    'likely_viable' => AssessmentTone.positive,
    'weak_or_uncertain' ||
    'time_barred' ||
    'not_state_actor' => AssessmentTone.caution,
    'not_viable' => AssessmentTone.negative,
    _ when status == 'failed' => AssessmentTone.negative,
    _ => AssessmentTone.neutral,
  };
}

String formatArticleLabel(String article) {
  final value = article.trim();
  if (value.isEmpty) return '';
  return value.toLowerCase().startsWith('article ') ? value : 'Article $value';
}

String formatResultTimestamp(String value) {
  if (value.trim().isEmpty) return '';
  final parsed = DateTime.tryParse(value);
  if (parsed == null) return value;
  final local = parsed.toLocal();
  const months = [
    'Jan',
    'Feb',
    'Mar',
    'Apr',
    'May',
    'Jun',
    'Jul',
    'Aug',
    'Sep',
    'Oct',
    'Nov',
    'Dec',
  ];
  final hour = local.hour == 0
      ? 12
      : local.hour > 12
      ? local.hour - 12
      : local.hour;
  final minute = local.minute.toString().padLeft(2, '0');
  final period = local.hour >= 12 ? 'PM' : 'AM';
  return '${local.day} ${months[local.month - 1]} ${local.year}, '
      '$hour:$minute $period';
}

const _fallbackDisclaimer =
    'This AI-generated assessment provides preliminary legal information only. '
    'It is not legal advice and does not replace consultation with a qualified '
    'lawyer. It is based on the facts provided and a limited case-law corpus.';

({String explanation, String disclaimer}) _splitAnswer(String answer) {
  final trimmed = answer.trim();
  if (trimmed.isEmpty) {
    return (explanation: '', disclaimer: _fallbackDisclaimer);
  }
  final markerIndex = trimmed.toUpperCase().lastIndexOf('DISCLAIMER:');
  if (markerIndex < 0) {
    return (explanation: trimmed, disclaimer: _fallbackDisclaimer);
  }
  final explanation = trimmed.substring(0, markerIndex).trim();
  final disclaimer = trimmed
      .substring(markerIndex + 'DISCLAIMER:'.length)
      .trim();
  return (
    explanation: explanation,
    disclaimer: disclaimer.isEmpty ? _fallbackDisclaimer : disclaimer,
  );
}

Map<String, dynamic> _map(Object? value) {
  if (value is! Map) return <String, dynamic>{};
  return value.cast<String, dynamic>();
}

List<dynamic> _firstList(List<Object?> candidates) {
  for (final value in candidates) {
    if (value is List && value.isNotEmpty) return value;
  }
  return const [];
}

List<String> _firstStringList(List<Object?> candidates) {
  for (final value in candidates) {
    final values = _strings(value);
    if (values.isNotEmpty) return values;
  }
  return const [];
}

List<String> _strings(Object? value) {
  if (value is! List) return const [];
  final seen = <String>{};
  return value
      .map(_text)
      .where((item) => item.isNotEmpty && seen.add(item))
      .toList(growable: false);
}

String _stringsOrText(Object? value) {
  final values = _strings(value);
  return values.isEmpty ? _text(value) : values.join(', ');
}

String _firstText(List<Object?> candidates) {
  for (final value in candidates) {
    final text = _text(value);
    if (text.isNotEmpty) return text;
  }
  return '';
}

String _text(Object? value) => value?.toString().trim() ?? '';

String _humanize(String value) {
  final words = value
      .split('_')
      .where((part) => part.isNotEmpty)
      .map((part) => '${part[0].toUpperCase()}${part.substring(1)}');
  return words.join(' ');
}
