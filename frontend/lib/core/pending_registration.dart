class PendingRegistration {
  const PendingRegistration({
    required this.uid,
    required this.displayName,
    required this.acceptedTerms,
    required this.acceptedPrivacyPolicy,
  });

  final String uid;
  final String displayName;
  final bool acceptedTerms;
  final bool acceptedPrivacyPolicy;
}

abstract final class PendingRegistrationStore {
  static final Map<String, PendingRegistration> _registrations = {};

  static void save(PendingRegistration registration) {
    _registrations[registration.uid] = registration;
  }

  static PendingRegistration? read(String uid) => _registrations[uid];

  static void remove(String uid) {
    _registrations.remove(uid);
  }
}
