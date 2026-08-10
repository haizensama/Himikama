import 'package:flutter/material.dart';

import '../../core/api_service.dart';
import '../../core/app_theme.dart';

class ReasoningTraceScreen extends StatefulWidget {
  const ReasoningTraceScreen({
    required this.apiService,
    required this.attemptId,
    super.key,
  });

  final ApiService apiService;
  final String attemptId;

  @override
  State<ReasoningTraceScreen> createState() => _ReasoningTraceScreenState();
}

class _ReasoningTraceScreenState extends State<ReasoningTraceScreen> {
  late final Future<Map<String, dynamic>> _trace;

  @override
  void initState() {
    super.initState();
    _trace = widget.apiService.getReasoningTrace(widget.attemptId);
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('How Himikama reasoned')),
      body: SafeArea(
        child: FutureBuilder<Map<String, dynamic>>(
          future: _trace,
          builder: (context, snapshot) {
            if (snapshot.connectionState == ConnectionState.waiting) {
              return const Center(child: CircularProgressIndicator());
            }
            if (snapshot.hasError) {
              final error = snapshot.error;
              final message = error is ApiException
                  ? error.message
                  : 'Could not load the reasoning trace.';
              return Center(
                child: Padding(
                  padding: const EdgeInsets.all(24),
                  child: Text(message, textAlign: TextAlign.center),
                ),
              );
            }

            final rawTrace = snapshot.data?['reasoning_trace'];
            final steps = rawTrace is List
                ? rawTrace.whereType<Map>().toList(growable: false)
                : const <Map>[];
            if (steps.isEmpty) {
              return const Center(
                child: Padding(
                  padding: EdgeInsets.all(24),
                  child: Text(
                    'No reasoning trace is available for this attempt.',
                  ),
                ),
              );
            }

            return ListView.separated(
              padding: const EdgeInsets.all(20),
              itemCount: steps.length,
              separatorBuilder: (_, _) => const SizedBox(height: 12),
              itemBuilder: (context, index) {
                final step = steps[index].cast<String, dynamic>();
                final passed = step['passed'] as bool? ?? false;
                return Card(
                  child: ExpansionTile(
                    leading: Icon(
                      passed ? Icons.check_circle_outline : Icons.info_outline,
                      color: passed
                          ? AppPalette.success
                          : Theme.of(context).colorScheme.error,
                    ),
                    title: Text(
                      step['title'] as String? ?? 'Reasoning step',
                      style: const TextStyle(fontWeight: FontWeight.w700),
                    ),
                    subtitle: Text(passed ? 'Completed' : 'Not passed'),
                    childrenPadding: const EdgeInsets.fromLTRB(16, 0, 16, 16),
                    children: [
                      if ((step['explanation'] as String? ?? '').isNotEmpty)
                        Align(
                          alignment: Alignment.centerLeft,
                          child: Text(step['explanation'] as String),
                        ),
                      if ((step['details'] as String? ?? '').isNotEmpty) ...[
                        const SizedBox(height: 10),
                        Align(
                          alignment: Alignment.centerLeft,
                          child: Text(step['details'] as String),
                        ),
                      ],
                    ],
                  ),
                );
              },
            );
          },
        ),
      ),
    );
  }
}
