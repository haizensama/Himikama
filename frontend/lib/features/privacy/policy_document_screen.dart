import 'package:flutter/material.dart';

import 'policy_content.dart';

class PolicyDocumentScreen extends StatelessWidget {
  const PolicyDocumentScreen({required this.type, super.key});

  final PolicyDocumentType type;

  @override
  Widget build(BuildContext context) {
    final isPrivacy = type == PolicyDocumentType.privacy;
    final title = isPrivacy ? 'Privacy Notice' : 'Terms of Use';
    final version = isPrivacy ? himikamaPrivacyVersion : himikamaTermsVersion;
    final sections = isPrivacy ? privacySections : termsSections;

    return Scaffold(
      appBar: AppBar(title: Text(title)),
      body: SafeArea(
        child: ListView(
          key: ValueKey(isPrivacy ? 'privacy-notice' : 'terms-of-use'),
          padding: const EdgeInsets.fromLTRB(20, 16, 20, 32),
          children: [
            Text(
              title,
              style: Theme.of(
                context,
              ).textTheme.headlineSmall?.copyWith(fontWeight: FontWeight.w800),
            ),
            const SizedBox(height: 6),
            Text('Version $version · Effective $himikamaPolicyEffectiveDate'),
            if (isPrivacy) ...[
              const SizedBox(height: 12),
              SelectableText(
                'Privacy contact: $himikamaPrivacyContact',
                style: TextStyle(
                  color: Theme.of(context).colorScheme.primary,
                  fontWeight: FontWeight.w700,
                ),
              ),
            ],
            const SizedBox(height: 24),
            ...sections.map(
              (section) => Padding(
                padding: const EdgeInsets.only(bottom: 22),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      section.title,
                      style: Theme.of(context).textTheme.titleMedium?.copyWith(
                        fontWeight: FontWeight.w800,
                      ),
                    ),
                    const SizedBox(height: 7),
                    Text(section.body, style: const TextStyle(height: 1.45)),
                  ],
                ),
              ),
            ),
            if (isPrivacy)
              Card(
                color: Theme.of(context).colorScheme.primaryContainer,
                child: const Padding(
                  padding: EdgeInsets.all(16),
                  child: Text(
                    'This notice describes the app\'s implemented data '
                    'handling. It is not a substitute for an independent '
                    'legal and data-protection review before public release.',
                  ),
                ),
              ),
          ],
        ),
      ),
    );
  }
}
