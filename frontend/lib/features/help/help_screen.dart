import 'package:flutter/material.dart';

class HelpScreen extends StatelessWidget {
  const HelpScreen({super.key});

  @override
  Widget build(BuildContext context) {
    final colors = Theme.of(context).colorScheme;
    return SafeArea(
      top: false,
      child: ListView(
        key: const PageStorageKey('help-scroll'),
        padding: const EdgeInsets.fromLTRB(20, 16, 20, 28),
        children: [
          Text(
            'How to use Himikama',
            style: Theme.of(
              context,
            ).textTheme.headlineSmall?.copyWith(fontWeight: FontWeight.w800),
          ),
          const SizedBox(height: 8),
          Text(
            'Follow this basic flow to create and review an assessment.',
            style: Theme.of(
              context,
            ).textTheme.bodyLarge?.copyWith(color: colors.onSurfaceVariant),
          ),
          const SizedBox(height: 20),
          const _HelpStep(
            number: 1,
            title: 'Open Assessments',
            description:
                'Use the Assessments tab and select “Describe your situation”.',
          ),
          const _HelpStep(
            number: 2,
            title: 'Describe one incident',
            description:
                'Write naturally. Include the date, place, person or authority '
                'involved, what happened, and any harm suffered.',
          ),
          const _HelpStep(
            number: 3,
            title: 'Review the extracted details',
            description:
                'Check each field carefully. Correct inaccuracies and complete '
                'any required information before confirming.',
          ),
          const _HelpStep(
            number: 4,
            title: 'Run the assessment',
            description:
                'Confirm once. Himikama creates an attempt and continues the '
                'analysis while the processing screen updates.',
          ),
          const _HelpStep(
            number: 5,
            title: 'Read the result',
            description:
                'Review the overall assessment, potentially engaged articles, '
                'strengths, uncertainties, similar cases, and next steps.',
          ),
          const _HelpStep(
            number: 6,
            title: 'Return through history',
            description:
                'Load your private history in Assessments to reopen a saved or '
                'processing attempt.',
          ),
          const SizedBox(height: 22),
          Card(
            color: colors.primaryContainer,
            child: Padding(
              padding: const EdgeInsets.all(18),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Row(
                    children: [
                      Icon(
                        Icons.tips_and_updates_outlined,
                        color: colors.onPrimaryContainer,
                      ),
                      const SizedBox(width: 10),
                      Text(
                        'Helpful description tips',
                        style: Theme.of(context).textTheme.titleMedium
                            ?.copyWith(
                              color: colors.onPrimaryContainer,
                              fontWeight: FontWeight.w800,
                            ),
                      ),
                    ],
                  ),
                  const SizedBox(height: 12),
                  const _Tip(text: 'Describe one incident at a time.'),
                  const _Tip(text: 'Use the most accurate date you know.'),
                  const _Tip(
                    text:
                        'Identify whether the person was a police officer, '
                        'public official, government body, or another actor.',
                  ),
                  const _Tip(
                    text:
                        'State only what you know; mark uncertain details rather '
                        'than guessing.',
                  ),
                ],
              ),
            ),
          ),
          const SizedBox(height: 16),
          Container(
            padding: const EdgeInsets.all(16),
            decoration: BoxDecoration(
              color: colors.surfaceContainerLow,
              borderRadius: BorderRadius.circular(16),
              border: Border.all(color: colors.outlineVariant),
            ),
            child: const Text(
              'If the matter is urgent, involves immediate danger, or a legal '
              'deadline may be approaching, seek help from a qualified lawyer '
              'or the appropriate authority promptly.',
            ),
          ),
        ],
      ),
    );
  }
}

class _HelpStep extends StatelessWidget {
  const _HelpStep({
    required this.number,
    required this.title,
    required this.description,
  });

  final int number;
  final String title;
  final String description;

  @override
  Widget build(BuildContext context) {
    final colors = Theme.of(context).colorScheme;
    return Padding(
      padding: const EdgeInsets.only(bottom: 10),
      child: Card(
        child: Padding(
          padding: const EdgeInsets.all(16),
          child: Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              CircleAvatar(
                radius: 18,
                backgroundColor: colors.primaryContainer,
                foregroundColor: colors.onPrimaryContainer,
                child: Text(
                  '$number',
                  style: const TextStyle(fontWeight: FontWeight.w800),
                ),
              ),
              const SizedBox(width: 14),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      title,
                      style: Theme.of(context).textTheme.titleMedium?.copyWith(
                        fontWeight: FontWeight.w700,
                      ),
                    ),
                    const SizedBox(height: 5),
                    Text(description, style: const TextStyle(height: 1.35)),
                  ],
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

class _Tip extends StatelessWidget {
  const _Tip({required this.text});

  final String text;

  @override
  Widget build(BuildContext context) {
    final color = Theme.of(context).colorScheme.onPrimaryContainer;
    return Padding(
      padding: const EdgeInsets.only(bottom: 8),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Icon(Icons.check_circle_outline, size: 19, color: color),
          const SizedBox(width: 9),
          Expanded(
            child: Text(text, style: TextStyle(color: color)),
          ),
        ],
      ),
    );
  }
}
