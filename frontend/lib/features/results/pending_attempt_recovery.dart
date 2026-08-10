import 'package:flutter/material.dart';

import '../../core/pending_attempt_store.dart';

class PendingAttemptRecovery extends StatefulWidget {
  const PendingAttemptRecovery({
    required this.ownerUid,
    required this.store,
    required this.onResume,
    required this.child,
    super.key,
  });

  final String ownerUid;
  final PendingAttemptStore store;
  final Future<void> Function(String attemptId) onResume;
  final Widget child;

  @override
  State<PendingAttemptRecovery> createState() => _PendingAttemptRecoveryState();
}

class _PendingAttemptRecoveryState extends State<PendingAttemptRecovery> {
  bool _checked = false;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) => _checkPendingAttempt());
  }

  Future<void> _checkPendingAttempt() async {
    if (_checked) return;
    _checked = true;
    final PendingAttemptReference? pending;
    try {
      pending = await widget.store.readForUser(widget.ownerUid);
    } on Exception {
      return;
    }
    if (!mounted || pending == null) return;

    final resume = await showDialog<bool>(
      context: context,
      barrierDismissible: false,
      builder: (dialogContext) => AlertDialog(
        key: const Key('pending-attempt-dialog'),
        icon: const Icon(Icons.restore),
        title: const Text('Resume your assessment?'),
        content: const Text(
          'Himikama found an assessment that had not reached a successful '
          'result when the app last closed. Resume it using the same attempt '
          'ID, or continue to Home and return later.',
        ),
        actions: [
          TextButton(
            key: const Key('pending-attempt-later'),
            onPressed: () => Navigator.of(dialogContext).pop(false),
            child: const Text('Not now'),
          ),
          FilledButton.icon(
            key: const Key('pending-attempt-resume'),
            onPressed: () => Navigator.of(dialogContext).pop(true),
            icon: const Icon(Icons.play_arrow),
            label: const Text('Resume'),
          ),
        ],
      ),
    );
    if (resume == true && mounted) {
      await widget.onResume(pending.attemptId);
    }
  }

  @override
  Widget build(BuildContext context) => widget.child;
}
