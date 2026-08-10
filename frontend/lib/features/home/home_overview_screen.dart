import 'package:flutter/material.dart';

import '../../core/app_theme.dart';

class HomeOverviewScreen extends StatelessWidget {
  const HomeOverviewScreen({
    required this.displayName,
    required this.onStartAssessment,
    super.key,
  });

  final String displayName;
  final VoidCallback onStartAssessment;

  @override
  Widget build(BuildContext context) {
    final colors = Theme.of(context).colorScheme;
    final normalizedName = displayName.trim();
    final firstName = normalizedName.isEmpty
        ? null
        : normalizedName.split(RegExp(r'\s+')).first;

    return SafeArea(
      top: false,
      child: ListView(
        key: const PageStorageKey('home-overview-scroll'),
        padding: const EdgeInsets.fromLTRB(20, 12, 20, 28),
        children: [
          const _BrandHeader(key: Key('home-himikama-logo')),
          const SizedBox(height: 28),
          Text(
            firstName == null || firstName.isEmpty
                ? 'Welcome to Himikama'
                : 'Welcome, $firstName',
            style: Theme.of(context).textTheme.headlineSmall,
          ),
          const SizedBox(height: 7),
          Text(
            'A clear, guided way to consider how Sri Lankan Fundamental '
            'Rights may relate to what happened.',
            style: Theme.of(
              context,
            ).textTheme.bodyLarge?.copyWith(color: colors.onSurfaceVariant),
          ),
          const SizedBox(height: 22),
          _AssessmentCallToAction(onPressed: onStartAssessment),
          const SizedBox(height: 28),
          Text(
            'Before you begin',
            style: Theme.of(context).textTheme.titleLarge,
          ),
          const SizedBox(height: 6),
          Text(
            'You do not need legal language. A few practical details will help '
            'Himikama organize your account accurately.',
            style: Theme.of(
              context,
            ).textTheme.bodyMedium?.copyWith(color: colors.onSurfaceVariant),
          ),
          const SizedBox(height: 14),
          const _PreparationPanel(),
          const SizedBox(height: 22),
          const _TrustPanel(),
          const SizedBox(height: 20),
          Container(
            padding: const EdgeInsets.all(15),
            decoration: BoxDecoration(
              color: colors.surfaceContainerLow,
              borderRadius: BorderRadius.circular(14),
              border: Border.all(color: colors.outlineVariant),
            ),
            child: Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Icon(Icons.info_outline, color: colors.primary, size: 21),
                const SizedBox(width: 11),
                const Expanded(
                  child: Text(
                    'Himikama provides preliminary legal information. It does '
                    'not replace advice from a qualified lawyer or urgent '
                    'professional assistance.',
                  ),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}

class _BrandHeader extends StatelessWidget {
  const _BrandHeader({super.key});

  @override
  Widget build(BuildContext context) {
    final colors = Theme.of(context).colorScheme;
    return Row(
      children: [
        Container(
          width: 46,
          height: 46,
          decoration: BoxDecoration(
            color: colors.secondaryContainer,
            borderRadius: BorderRadius.circular(14),
            border: Border.all(color: AppPalette.gold.withValues(alpha: 0.3)),
          ),
          child: const Icon(
            Icons.balance_outlined,
            color: AppPalette.gold,
            size: 25,
          ),
        ),
        const SizedBox(width: 13),
        Expanded(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(
                'HIMIKAMA',
                style: Theme.of(context).textTheme.titleMedium?.copyWith(
                  color: colors.primary,
                  fontWeight: FontWeight.w700,
                  letterSpacing: 1.6,
                ),
              ),
              const SizedBox(height: 2),
              Text(
                'Fundamental Rights guidance',
                style: Theme.of(context).textTheme.bodySmall,
              ),
            ],
          ),
        ),
      ],
    );
  }
}

class _AssessmentCallToAction extends StatelessWidget {
  const _AssessmentCallToAction({required this.onPressed});

  final VoidCallback onPressed;

  @override
  Widget build(BuildContext context) {
    return Container(
      decoration: BoxDecoration(
        color: AppPalette.primary,
        borderRadius: BorderRadius.circular(20),
        boxShadow: [
          BoxShadow(
            color: AppPalette.primary.withValues(alpha: 0.14),
            blurRadius: 22,
            offset: const Offset(0, 10),
          ),
        ],
      ),
      child: Padding(
        padding: const EdgeInsets.all(20),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            const Align(
              alignment: Alignment.centerLeft,
              child: Icon(
                Icons.edit_note_outlined,
                color: AppPalette.goldContainer,
                size: 30,
              ),
            ),
            const SizedBox(height: 18),
            Text(
              'Start a new assessment',
              style: Theme.of(
                context,
              ).textTheme.titleLarge?.copyWith(color: Colors.white),
            ),
            const SizedBox(height: 8),
            Text(
              'Describe one incident in your own words. You will review the '
              'organized facts before any legal analysis begins.',
              style: Theme.of(context).textTheme.bodyMedium?.copyWith(
                color: Colors.white.withValues(alpha: 0.82),
              ),
            ),
            const SizedBox(height: 18),
            FilledButton.icon(
              key: const Key('home-start-assessment'),
              style: FilledButton.styleFrom(
                backgroundColor: Colors.white,
                foregroundColor: AppPalette.primary,
              ),
              onPressed: onPressed,
              icon: const Icon(Icons.arrow_forward),
              label: const Text('Describe your situation'),
            ),
          ],
        ),
      ),
    );
  }
}

class _PreparationPanel extends StatelessWidget {
  const _PreparationPanel();

  @override
  Widget build(BuildContext context) {
    return Card(
      child: Padding(
        padding: const EdgeInsets.fromLTRB(17, 16, 17, 8),
        child: Column(
          children: const [
            _PreparationItem(
              icon: Icons.calendar_today_outlined,
              text: 'When and where the incident occurred',
            ),
            _PreparationItem(
              icon: Icons.account_balance_outlined,
              text: 'The authority or official who was involved',
            ),
            _PreparationItem(
              icon: Icons.description_outlined,
              text: 'What happened and how it affected you',
            ),
          ],
        ),
      ),
    );
  }
}

class _PreparationItem extends StatelessWidget {
  const _PreparationItem({required this.icon, required this.text});

  final IconData icon;
  final String text;

  @override
  Widget build(BuildContext context) {
    final colors = Theme.of(context).colorScheme;
    return Padding(
      padding: const EdgeInsets.only(bottom: 12),
      child: Row(
        children: [
          Container(
            width: 36,
            height: 36,
            decoration: BoxDecoration(
              color: colors.primaryContainer,
              borderRadius: BorderRadius.circular(10),
            ),
            child: Icon(icon, color: colors.primary, size: 19),
          ),
          const SizedBox(width: 12),
          Expanded(child: Text(text)),
        ],
      ),
    );
  }
}

class _TrustPanel extends StatelessWidget {
  const _TrustPanel();

  @override
  Widget build(BuildContext context) {
    final colors = Theme.of(context).colorScheme;
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 14),
      decoration: BoxDecoration(
        color: AppPalette.primaryContainer.withValues(alpha: 0.58),
        borderRadius: BorderRadius.circular(14),
      ),
      child: Row(
        children: [
          Icon(Icons.verified_user_outlined, color: colors.primary, size: 23),
          const SizedBox(width: 12),
          const Expanded(
            child: Text(
              'Your assessment stays linked to your verified account, and you '
              'can review or delete it from your private history.',
            ),
          ),
        ],
      ),
    );
  }
}
