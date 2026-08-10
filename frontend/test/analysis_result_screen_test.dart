import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:mobile/core/app_theme.dart';
import 'package:mobile/features/results/analysis_result_screen.dart';

void main() {
  testWidgets('shows structured summary before the full explanation', (
    tester,
  ) async {
    await tester.pumpWidget(
      MaterialApp(
        theme: AppTheme.light,
        home: AnalysisResultScreen(result: _result()),
      ),
    );

    expect(find.text('Potentially viable claim'), findsOneWidget);
    expect(find.text('Rights that may be engaged'), findsOneWidget);
    expect(find.text('Article 13(1)'), findsOneWidget);
    expect(find.textContaining('SECTION 1'), findsNothing);

    await tester.scrollUntilVisible(
      find.byKey(const Key('full-explanation-expansion')),
      300,
    );
    await tester.tap(find.byKey(const Key('full-explanation-expansion')));
    await tester.pumpAndSettle();

    expect(find.textContaining('SECTION 1'), findsOneWidget);
  });

  testWidgets('always presents a legal notice for a hard-gate result', (
    tester,
  ) async {
    await tester.pumpWidget(
      MaterialApp(
        theme: AppTheme.light,
        home: const AnalysisResultScreen(
          result: {
            'attempt_id': 'gate-attempt',
            'status': 'time_barred',
            'summary': {
              'overall_assessment': 'time_barred',
              'structured_assessment': {
                'key_weaknesses': [
                  'The incident appears outside the filing window.',
                ],
              },
            },
          },
        ),
      ),
    );

    expect(find.text('Filing-time concern identified'), findsOneWidget);
    expect(find.text('Rights that may be engaged'), findsNothing);

    await tester.scrollUntilVisible(
      find.byKey(const Key('legal-disclaimer')),
      350,
    );
    expect(find.text('Important legal notice'), findsOneWidget);
  });

  test('the app theme uses the new medium-blue palette', () {
    final theme = AppTheme.light;

    expect(theme.colorScheme.primary, AppPalette.primary);
    expect(theme.scaffoldBackgroundColor, AppPalette.scaffold);
    expect(theme.colorScheme.primary, isNot(const Color(0xFF0F766E)));
  });
}

Map<String, dynamic> _result() {
  return {
    'attempt_id': 'result-attempt',
    'status': 'complete',
    'main_answer':
        'SECTION 1 — RIGHTS ASSESSMENT:\nArticle 13(1) may be engaged.\n\n'
        'DISCLAIMER:\nThis is not legal advice.',
    'summary': {
      'overall_assessment': 'likely_viable',
      'final_potentially_violated_articles': ['13(1)'],
      'article_assessments': [
        {
          'article': '13(1)',
          'status': 'supported',
          'reason': 'The arrest may not have had lawful grounds.',
        },
      ],
      'structured_assessment': {
        'key_strengths': ['The alleged actor was a police officer.'],
        'key_weaknesses': ['The reason for arrest remains unclear.'],
      },
    },
  };
}
