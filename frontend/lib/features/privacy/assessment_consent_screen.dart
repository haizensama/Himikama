import 'package:flutter/material.dart';

import '../../core/api_service.dart';
import 'policy_content.dart';
import 'policy_document_screen.dart';

class AssessmentConsentScreen extends StatefulWidget {
  const AssessmentConsentScreen({required this.apiService, super.key});

  final ApiService apiService;

  @override
  State<AssessmentConsentScreen> createState() =>
      _AssessmentConsentScreenState();
}

class _AssessmentConsentScreenState extends State<AssessmentConsentScreen> {
  bool _accepted = false;
  bool _submitting = false;
  String? _error;

  Future<void> _continue() async {
    if (!_accepted) {
      setState(() => _error = 'Confirm your choice before continuing.');
      return;
    }
    setState(() {
      _submitting = true;
      _error = null;
    });
    try {
      await widget.apiService.acceptAssessmentConsent();
      if (mounted) Navigator.of(context).pop(true);
    } on ApiException catch (error) {
      if (mounted) setState(() => _error = error.message);
    } finally {
      if (mounted) setState(() => _submitting = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Before your first assessment')),
      body: SafeArea(
        child: ListView(
          padding: const EdgeInsets.all(20),
          children: [
            Icon(
              Icons.privacy_tip_outlined,
              size: 54,
              color: Theme.of(context).colorScheme.primary,
            ),
            const SizedBox(height: 16),
            Text(
              'Choose whether Himikama may process your incident details',
              style: Theme.of(
                context,
              ).textTheme.headlineSmall?.copyWith(fontWeight: FontWeight.w800),
            ),
            const SizedBox(height: 12),
            const Text(
              'To run an assessment, your description and confirmed facts '
              'must be processed by the Himikama backend and the configured '
              'Google Gemini API. The result is saved in your private history '
              'until you delete it.',
              style: TextStyle(height: 1.45),
            ),
            const SizedBox(height: 18),
            const _ConsentPoint(
              icon: Icons.lock_outline,
              text: 'Only your verified account can open saved assessments.',
            ),
            const _ConsentPoint(
              icon: Icons.delete_outline,
              text: 'Delete one assessment, all history, or your account.',
            ),
            const _ConsentPoint(
              icon: Icons.undo_outlined,
              text:
                  'Withdraw consent for future assessments at any time. '
                  'Existing history is not deleted automatically.',
            ),
            const SizedBox(height: 10),
            TextButton.icon(
              onPressed: () => Navigator.of(context).push(
                MaterialPageRoute<void>(
                  builder: (_) => const PolicyDocumentScreen(
                    type: PolicyDocumentType.privacy,
                  ),
                ),
              ),
              icon: const Icon(Icons.description_outlined),
              label: const Text('Read the full Privacy Notice'),
            ),
            SelectableText(
              'Privacy contact: $himikamaPrivacyContact',
              textAlign: TextAlign.center,
            ),
            const SizedBox(height: 12),
            CheckboxListTile(
              key: const Key('assessment-consent-checkbox'),
              contentPadding: EdgeInsets.zero,
              controlAffinity: ListTileControlAffinity.leading,
              value: _accepted,
              onChanged: _submitting
                  ? null
                  : (value) => setState(() => _accepted = value ?? false),
              title: const Text(
                'I consent to processing my submitted incident information '
                'to provide and save a Himikama assessment.',
              ),
            ),
            if (_error case final error?) ...[
              const SizedBox(height: 8),
              Text(
                error,
                style: TextStyle(
                  color: Theme.of(context).colorScheme.error,
                  fontWeight: FontWeight.w600,
                ),
              ),
            ],
            const SizedBox(height: 16),
            FilledButton(
              key: const Key('accept-assessment-consent'),
              onPressed: _submitting ? null : _continue,
              child: _submitting
                  ? const SizedBox.square(
                      dimension: 20,
                      child: CircularProgressIndicator(strokeWidth: 2),
                    )
                  : const Text('Accept and continue'),
            ),
            TextButton(
              onPressed: _submitting
                  ? null
                  : () => Navigator.of(context).pop(false),
              child: const Text('Not now'),
            ),
          ],
        ),
      ),
    );
  }
}

class _ConsentPoint extends StatelessWidget {
  const _ConsentPoint({required this.icon, required this.text});

  final IconData icon;
  final String text;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 12),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Icon(icon, size: 22, color: Theme.of(context).colorScheme.primary),
          const SizedBox(width: 12),
          Expanded(child: Text(text)),
        ],
      ),
    );
  }
}
