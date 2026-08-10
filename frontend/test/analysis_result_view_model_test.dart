import 'package:flutter_test/flutter_test.dart';
import 'package:mobile/features/results/analysis_result_view_model.dart';

void main() {
  test('parses the complete structured result contract', () {
    final view = AnalysisResultViewModel.fromJson(_completeResult());

    expect(view.overallAssessment, 'likely_viable');
    expect(view.overallLabel, 'Potentially viable claim');
    expect(view.supportedArticles, ['13(1)', '13(2)']);
    expect(view.uncertainArticles, isEmpty);
    expect(view.keyStrengths, hasLength(2));
    expect(view.keyWeaknesses, hasLength(1));
    expect(view.articleAssessments, hasLength(2));
    expect(view.similarCases.single.caseName, 'Gunasekera v. De Fonseka');
    expect(view.fullExplanation, contains('SECTION 1'));
    expect(view.fullExplanation, isNot(contains('DISCLAIMER:')));
    expect(view.disclaimer, contains('does not constitute legal advice'));
    expect(view.reasoningAvailable, isTrue);
  });

  test('hard-gate result remains understandable without Step 10 output', () {
    final view = AnalysisResultViewModel.fromJson({
      'attempt_id': 'gate-attempt',
      'status': 'time_barred',
      'summary': {
        'structured_assessment': {
          'overall_assessment': 'time_barred',
          'key_weaknesses': [
            'The incident appears outside the 30-day filing window.',
          ],
        },
      },
      'reasoning_available': true,
    });

    expect(view.overallLabel, 'Filing-time concern identified');
    expect(view.tone, AssessmentTone.caution);
    expect(view.keyWeaknesses, hasLength(1));
    expect(view.disclaimer, isNotEmpty);
    expect(view.nextSteps.first, contains('lawyer promptly'));
  });

  test('history summaries parse top-level fields safely', () {
    final view = AnalysisResultViewModel.fromJson({
      'attempt_id': 'history-attempt',
      'status': 'complete',
      'overall_assessment': 'weak_or_uncertain',
      'final_weak_or_uncertain_articles': ['12(1)'],
      'confidence_level': 'low',
      'created_at': '2026-08-03T08:30:00Z',
    });

    expect(view.overallLabel, 'Weak or uncertain claim');
    expect(view.uncertainArticles, ['12(1)']);
    expect(view.confidenceLevel, 'low');
    expect(view.completedAt, '2026-08-03T08:30:00Z');
  });

  test('format helpers produce layperson-friendly labels', () {
    expect(formatArticleLabel('13(1)'), 'Article 13(1)');
    expect(formatArticleLabel('Article 11'), 'Article 11');
    expect(formatResultTimestamp('not-a-date'), 'not-a-date');
  });
}

Map<String, dynamic> _completeResult() {
  return {
    'attempt_id': '33e013b0-5e1a-4c23-b9a0-2eb5491b8860',
    'status': 'complete',
    'main_answer':
        'SECTION 1 — RIGHTS ASSESSMENT:\nArticle 13(1) may be engaged.\n\n'
        'DISCLAIMER:\nThis assessment does not constitute legal advice.',
    'confidence': {
      'level': 'high',
      'flags': <String>[],
      'explanation': 'The hard gates and cross-checks passed.',
    },
    'summary': {
      'final_potentially_violated_articles': ['13(1)', '13(2)'],
      'final_weak_or_uncertain_articles': <String>[],
      'final_rejected_articles': <String>[],
      'overall_assessment': 'likely_viable',
      'article_assessments': [
        {
          'article': '13(1)',
          'status': 'supported',
          'reason': 'The arrest may not have had lawful grounds.',
          'confidence': 'high',
          'supporting_case_ids': ['72'],
        },
        {
          'article': '13(2)',
          'status': 'supported',
          'reason': 'The reasons for arrest may not have been communicated.',
          'confidence': 'medium',
          'supporting_case_ids': ['72'],
        },
      ],
      'structured_assessment': {
        'overall_assessment': 'likely_viable',
        'key_strengths': [
          'The incident falls within the filing screen.',
          'The alleged actors were police officers.',
        ],
        'key_weaknesses': ['Intent is not fully established.'],
      },
      'similar_cases': [
        {
          'case_id': '72',
          'case_name': 'Gunasekera v. De Fonseka',
          'case_number': 'SC APPLICATION NO. 411/71',
          'year': 1972,
          'judgment': 'VIOLATED',
          'articles_cited': '13(1),13(2)',
        },
      ],
    },
    'reasoning_available': true,
    'timestamps': {
      'started_at': '2026-08-03T08:30:00Z',
      'completed_at': '2026-08-03T08:31:30Z',
    },
  };
}
