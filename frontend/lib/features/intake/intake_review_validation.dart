bool isValidIntakeIsoDate(String value, {DateTime? now}) {
  final normalized = value.trim();
  if (!RegExp(r'^\d{4}-\d{2}-\d{2}$').hasMatch(normalized)) {
    return false;
  }
  final parsed = DateTime.tryParse(normalized);
  if (parsed == null) return false;
  final canonical = parsed.toIso8601String().split('T').first;
  final current = now ?? DateTime.now();
  final today = DateTime(current.year, current.month, current.day);
  return canonical == normalized && !parsed.isAfter(today);
}

List<String> buildActiveIntakeClarifyingQuestions({
  required String incidentDate,
  required String actorRole,
  required String whatHappened,
  required String userNarrative,
  DateTime? now,
}) {
  final questions = <String>[];
  if (!isValidIntakeIsoDate(incidentDate, now: now)) {
    questions.add(
      'When did this incident happen? Please provide the exact date.',
    );
  }
  if (actorRole.trim().length < 2 || actorRole.trim().length > 120) {
    questions.add(
      'Who carried out the action? Please state their role or institution, '
      'such as police officer or government department.',
    );
  }
  if (whatHappened.trim().length < 10 || whatHappened.trim().length > 2000) {
    questions.add(
      'What happened? Please briefly describe the action or omission.',
    );
  }
  if (userNarrative.trim().length < 10 || userNarrative.trim().length > 4000) {
    questions.add('Please provide your description of what happened.');
  }
  return questions;
}
