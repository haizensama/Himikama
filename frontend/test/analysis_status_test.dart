import 'package:flutter_test/flutter_test.dart';
import 'package:mobile/features/results/analysis_status.dart';

void main() {
  test('active statuses keep polling', () {
    for (final status in ['pending', 'processing', 'running']) {
      expect(classifyAnalysisStatus(status), AnalysisAttemptState.active);
    }
  });

  test('normal and hard-gate outcomes open the result screen', () {
    for (final status in ['complete', 'time_barred', 'not_state_actor']) {
      expect(classifyAnalysisStatus(status), AnalysisAttemptState.succeeded);
    }
  });

  test('failed and unknown statuses remain distinct', () {
    expect(classifyAnalysisStatus('failed'), AnalysisAttemptState.failed);
    expect(classifyAnalysisStatus('unexpected'), AnalysisAttemptState.unknown);
  });
}
