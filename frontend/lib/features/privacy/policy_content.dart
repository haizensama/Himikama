const himikamaPrivacyContact = 'ghc_dilshan@protonmail.com';
const himikamaPrivacyVersion = '1.1';
const himikamaTermsVersion = '1.0';
const himikamaPolicyEffectiveDate = '5 August 2026';

enum PolicyDocumentType { privacy, terms }

class PolicySection {
  const PolicySection({required this.title, required this.body});

  final String title;
  final String body;
}

const privacySections = <PolicySection>[
  PolicySection(
    title: 'Who controls your data',
    body:
        'Himikama is the data controller for information processed through '
        'this app. Privacy, access, correction, objection, consent withdrawal, '
        'and deletion questions can be sent to '
        '$himikamaPrivacyContact.',
  ),
  PolicySection(
    title: 'Information we process',
    body:
        'We process your account email, display name, authentication status, '
        'incident descriptions, extracted facts, assessment results, similar '
        'case references, attempt identifiers, timestamps, consent records, '
        'and limited security or error metadata. Himikama does not need access '
        'to your contacts, device files, or precise device location. Do not '
        'include unnecessary personal details about another person.',
  ),
  PolicySection(
    title: 'Why we process it',
    body:
        'We use this information to create and protect your account, organize '
        'the facts you submit, provide Fundamental Rights triage, save results '
        'for you, restore interrupted assessments, prevent abuse, troubleshoot '
        'failures, and honour privacy or deletion requests.',
  ),
  PolicySection(
    title: 'AI and service providers',
    body:
        'Your incident description and confirmed facts are sent through the '
        'Himikama backend to the configured Google Gemini API so the requested '
        'intake and legal-triage steps can run. Firebase services are used for '
        'authentication and protected storage. These providers may process '
        'data outside Sri Lanka under their applicable service and data '
        'protection terms. Himikama does not sell your legal narrative or use '
        'it for advertising.',
  ),
  PolicySection(
    title: 'Retention and deletion',
    body:
        'Your assessments remain in your private history until you delete one, '
        'clear all history, or delete your account. Account deletion begins '
        'with a seven-day recovery period. During that period analysis is '
        'blocked and you may cancel deletion by signing in. After the deadline, '
        'the account, saved assessments, and active job references are '
        'permanently erased. Minimal server logs exclude legal narratives and '
        'are retained only for operational and security needs.',
  ),
  PolicySection(
    title: 'Your choices and rights',
    body:
        'You can review your saved assessments, correct your display name, '
        'delete individual assessments, clear all history, withdraw consent '
        'for future assessments, or schedule account deletion in Account & '
        'Privacy. Withdrawing consent does not undo processing already '
        'completed and does not delete existing history. Contact Himikama to '
        'request access, correction, erasure, restriction, objection, or a '
        'review of how your data was handled, subject to applicable law.',
  ),
  PolicySection(
    title: 'Security and automated assessment',
    body:
        'Himikama uses verified accounts, owner-scoped backend access, secure '
        'transport, app verification controls, and restricted administrative '
        'credentials. No system can guarantee absolute security. The result is '
        'automated informational triage, not a court decision or legal advice. '
        'You should review it and seek a qualified lawyer for decisions or '
        'urgent deadlines.',
  ),
  PolicySection(
    title: 'Changes and contact',
    body:
        'If this notice materially changes, the app will require the current '
        'version to be reviewed and accepted before further use. Contact: '
        '$himikamaPrivacyContact.',
  ),
];

const termsSections = <PolicySection>[
  PolicySection(
    title: 'Purpose of Himikama',
    body:
        'Himikama provides preliminary educational triage about possible '
        'Fundamental Rights issues in Sri Lanka. It does not provide legal '
        'representation, guarantee a claim, file documents, or replace advice '
        'from a qualified lawyer.',
  ),
  PolicySection(
    title: 'Your responsibilities',
    body:
        'Provide information honestly, review extracted facts before '
        'confirmation, protect your account credentials, use one account for '
        'yourself, and avoid submitting content you have no right to process. '
        'Do not misuse the service, probe another user\'s data, disrupt the '
        'service, or rely on Himikama for an emergency.',
  ),
  PolicySection(
    title: 'Deadlines and professional help',
    body:
        'Fundamental Rights claims can involve strict filing deadlines. A '
        'technical failure, delayed result, or saved assessment does not pause '
        'a legal deadline. Seek urgent professional help where safety, liberty, '
        'evidence preservation, or a filing deadline is involved.',
  ),
  PolicySection(
    title: 'Accuracy and availability',
    body:
        'Results depend on the facts supplied, available case summaries, and '
        'automated models. They may be incomplete or wrong. Features may be '
        'updated, suspended, or temporarily unavailable for security, '
        'maintenance, or legal reasons.',
  ),
  PolicySection(
    title: 'Accounts and termination',
    body:
        'You may stop using Himikama, delete assessments, or schedule account '
        'deletion at any time. Himikama may restrict an account where needed '
        'to protect users, data, the service, or comply with applicable law.',
  ),
  PolicySection(
    title: 'Privacy and contact',
    body:
        'The Privacy Notice explains how personal data is handled. Questions '
        'about these terms or privacy can be sent to '
        '$himikamaPrivacyContact.',
  ),
];
