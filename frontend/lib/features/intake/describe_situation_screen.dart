import 'package:flutter/material.dart';

import '../../core/api_service.dart';
import '../../core/pending_attempt_store.dart';
import 'intake_models.dart';
import 'review_intake_screen.dart';

class DescribeSituationScreen extends StatefulWidget {
  const DescribeSituationScreen({
    required this.apiService,
    super.key,
    this.pendingAttemptStore,
  });

  final ApiService apiService;
  final PendingAttemptStore? pendingAttemptStore;

  @override
  State<DescribeSituationScreen> createState() =>
      _DescribeSituationScreenState();
}

class _DescribeSituationScreenState extends State<DescribeSituationScreen> {
  final _formKey = GlobalKey<FormState>();
  final _descriptionController = TextEditingController();

  bool _submitting = false;
  String? _error;

  @override
  void dispose() {
    _descriptionController.dispose();
    super.dispose();
  }

  Future<void> _structureDescription() async {
    if (!_formKey.currentState!.validate()) return;

    setState(() {
      _submitting = true;
      _error = null;
    });

    try {
      final response = await widget.apiService.structureIntake(
        _descriptionController.text,
      );
      final structured = StructuredIntakeResponse.fromJson(response);
      if (!mounted) return;
      await Navigator.of(context).push(
        MaterialPageRoute<void>(
          builder: (_) => ReviewIntakeScreen(
            apiService: widget.apiService,
            structured: structured,
            pendingAttemptStore: widget.pendingAttemptStore,
          ),
        ),
      );
    } on ApiException catch (error) {
      if (!mounted) return;
      final requestId = error.requestId;
      setState(
        () => _error = requestId == null
            ? error.message
            : '${error.message} (Request ID: $requestId)',
      );
    } finally {
      if (mounted) {
        setState(() => _submitting = false);
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Describe your situation')),
      body: SafeArea(
        child: Form(
          key: _formKey,
          child: ListView(
            padding: const EdgeInsets.all(20),
            children: [
              Text(
                'Tell us what happened',
                style: Theme.of(context).textTheme.headlineSmall?.copyWith(
                  fontWeight: FontWeight.w800,
                ),
              ),
              const SizedBox(height: 8),
              const Text(
                'Write in your own words. Include when and where it happened, '
                'who was involved, what they did, and how it affected you.',
              ),
              const SizedBox(height: 20),
              TextFormField(
                key: const Key('situation-description'),
                controller: _descriptionController,
                minLines: 9,
                maxLines: 14,
                maxLength: 4000,
                textCapitalization: TextCapitalization.sentences,
                decoration: const InputDecoration(
                  labelText: 'Your description',
                  alignLabelWithHint: true,
                  hintText:
                      'Example: The police arrested me yesterday and kept me '
                      'at the station overnight without telling me why.',
                ),
                validator: (value) {
                  final description = value?.trim() ?? '';
                  if (description.length < 10) {
                    return 'Please describe what happened in at least 10 characters.';
                  }
                  return null;
                },
              ),
              const SizedBox(height: 12),
              Card(
                color: Theme.of(context).colorScheme.secondaryContainer,
                child: const Padding(
                  padding: EdgeInsets.all(16),
                  child: Row(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Icon(Icons.fact_check_outlined),
                      SizedBox(width: 12),
                      Expanded(
                        child: Text(
                          'This first step only organizes the facts you provide. '
                          'It does not decide which legal rights may apply. You '
                          'will review and edit every field before analysis.',
                        ),
                      ),
                    ],
                  ),
                ),
              ),
              const SizedBox(height: 12),
              Card(
                color: Theme.of(context).colorScheme.tertiaryContainer,
                child: const Padding(
                  padding: EdgeInsets.all(16),
                  child: Row(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Icon(Icons.filter_1_outlined),
                      SizedBox(width: 12),
                      Expanded(
                        child: Text(
                          'Describe one incident or one continuous event at a '
                          'time. If separate incidents happened on different '
                          'dates or involved different authorities, submit '
                          'them separately.',
                        ),
                      ),
                    ],
                  ),
                ),
              ),
              if (_error case final error?) ...[
                const SizedBox(height: 16),
                Text(
                  error,
                  style: TextStyle(
                    color: Theme.of(context).colorScheme.error,
                    fontWeight: FontWeight.w600,
                  ),
                ),
              ],
              const SizedBox(height: 24),
              FilledButton.icon(
                key: const Key('review-details-button'),
                onPressed: _submitting ? null : _structureDescription,
                icon: _submitting
                    ? const SizedBox.square(
                        dimension: 20,
                        child: CircularProgressIndicator(
                          strokeWidth: 2,
                          color: Colors.white,
                        ),
                      )
                    : const Icon(Icons.arrow_forward),
                label: Text(
                  _submitting ? 'Organizing your details…' : 'Review details',
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}
