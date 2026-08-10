import 'package:flutter_test/flutter_test.dart';
import 'package:mobile/features/intake/intake_models.dart';

void main() {
  test('parses a complete structured intake response', () {
    final response = StructuredIntakeResponse.fromJson({
      'status': 'needs_confirmation',
      'can_confirm': true,
      'intake': {
        'incident_date': '2026-07-24',
        'incident_location': 'Colombo',
        'actor_name': 'Police',
        'actor_role': 'police officer',
        'what_happened': 'Police arrested the user without explaining why.',
        'harm_suffered': 'Loss of liberty.',
        'user_narrative': 'The police arrested me without explaining why.',
      },
      'confirmation_text': 'Review these details.',
      'missing_required_fields': <String>[],
      'clarifying_questions': <String>[],
    });

    expect(response.canConfirm, isTrue);
    expect(response.intake.incidentDate, '2026-07-24');
    expect(response.intake.actorRole, 'police officer');
    expect(response.missingRequiredFields, isEmpty);
  });

  test('preserves missing fields and clarification questions', () {
    final response = StructuredIntakeResponse.fromJson({
      'status': 'needs_clarification',
      'can_confirm': false,
      'intake': {
        'incident_date': null,
        'incident_location': null,
        'actor_name': null,
        'actor_role': null,
        'what_happened': 'Someone detained the user without explanation.',
        'harm_suffered': null,
        'user_narrative': 'Someone detained me without explaining why.',
      },
      'confirmation_text': 'More information is needed.',
      'missing_required_fields': ['incident_date', 'actor_role'],
      'clarifying_questions': [
        'When did this incident happen?',
        'Who carried out the action?',
      ],
    });

    expect(response.canConfirm, isFalse);
    expect(response.intake.incidentDate, isNull);
    expect(
      response.missingRequiredFields,
      containsAll(['incident_date', 'actor_role']),
    );
    expect(response.clarifyingQuestions, hasLength(2));
  });
}
