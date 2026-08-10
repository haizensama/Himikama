import 'package:flutter_test/flutter_test.dart';
import 'package:mobile/features/intake/intake_review_validation.dart';

void main() {
  final today = DateTime(2026, 8, 2);

  test('valid corrected intake removes stale clarification questions', () {
    final questions = buildActiveIntakeClarifyingQuestions(
      incidentDate: '2026-07-30',
      actorRole: 'police officer',
      whatHappened: 'Police detained the user without explaining why.',
      userNarrative:
          'Police detained me on 30 July 2026 without explaining why.',
      now: today,
    );

    expect(questions, isEmpty);
  });

  test('missing required details remain visible', () {
    final questions = buildActiveIntakeClarifyingQuestions(
      incidentDate: '',
      actorRole: '',
      whatHappened: '',
      userNarrative: 'Someone acted against me, but I need to add the details.',
      now: today,
    );

    expect(questions, hasLength(3));
  });

  test('future dates are rejected', () {
    expect(isValidIntakeIsoDate('2026-08-03', now: today), isFalse);
    expect(isValidIntakeIsoDate('2026-08-02', now: today), isTrue);
  });
}
