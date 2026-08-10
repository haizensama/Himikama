class IntakeDraft {
  const IntakeDraft({
    required this.incidentDate,
    required this.incidentLocation,
    required this.actorName,
    required this.actorRole,
    required this.whatHappened,
    required this.harmSuffered,
    required this.userNarrative,
  });

  factory IntakeDraft.fromJson(Map<String, dynamic> json) {
    String? optionalText(String key) {
      final value = json[key];
      if (value is! String || value.trim().isEmpty) return null;
      return value.trim();
    }

    return IntakeDraft(
      incidentDate: optionalText('incident_date'),
      incidentLocation: optionalText('incident_location'),
      actorName: optionalText('actor_name'),
      actorRole: optionalText('actor_role'),
      whatHappened: optionalText('what_happened'),
      harmSuffered: optionalText('harm_suffered'),
      userNarrative: optionalText('user_narrative') ?? '',
    );
  }

  final String? incidentDate;
  final String? incidentLocation;
  final String? actorName;
  final String? actorRole;
  final String? whatHappened;
  final String? harmSuffered;
  final String userNarrative;
}

class StructuredIntakeResponse {
  const StructuredIntakeResponse({
    required this.status,
    required this.canConfirm,
    required this.intake,
    required this.confirmationText,
    required this.missingRequiredFields,
    required this.clarifyingQuestions,
  });

  factory StructuredIntakeResponse.fromJson(Map<String, dynamic> json) {
    final rawIntake = json['intake'];
    final intake = rawIntake is Map
        ? rawIntake.cast<String, dynamic>()
        : <String, dynamic>{};

    List<String> strings(Object? value) {
      if (value is! List) return const [];
      return value
          .whereType<String>()
          .map((item) => item.trim())
          .where((item) => item.isNotEmpty)
          .toList(growable: false);
    }

    return StructuredIntakeResponse(
      status: json['status'] as String? ?? 'needs_clarification',
      canConfirm: json['can_confirm'] as bool? ?? false,
      intake: IntakeDraft.fromJson(intake),
      confirmationText: json['confirmation_text'] as String? ?? '',
      missingRequiredFields: strings(json['missing_required_fields']),
      clarifyingQuestions: strings(json['clarifying_questions']),
    );
  }

  final String status;
  final bool canConfirm;
  final IntakeDraft intake;
  final String confirmationText;
  final List<String> missingRequiredFields;
  final List<String> clarifyingQuestions;
}
