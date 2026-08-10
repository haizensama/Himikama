enum AnalysisAttemptState { active, succeeded, failed, unknown }

AnalysisAttemptState classifyAnalysisStatus(Object? rawStatus) {
  final status = rawStatus?.toString().trim().toLowerCase() ?? '';
  switch (status) {
    case 'pending':
    case 'processing':
    case 'running':
      return AnalysisAttemptState.active;
    case 'complete':
    case 'time_barred':
    case 'not_state_actor':
      return AnalysisAttemptState.succeeded;
    case 'failed':
      return AnalysisAttemptState.failed;
    default:
      return AnalysisAttemptState.unknown;
  }
}

String readableAnalysisStatus(Object? rawStatus) {
  final status = rawStatus?.toString().trim().toLowerCase() ?? '';
  switch (status) {
    case 'pending':
      return 'Waiting to start';
    case 'processing':
    case 'running':
      return 'Analysis in progress';
    case 'complete':
      return 'Analysis complete';
    case 'time_barred':
      return 'Timeliness assessment complete';
    case 'not_state_actor':
      return 'State-actor assessment complete';
    case 'failed':
      return 'Analysis failed';
    default:
      return status.isEmpty ? 'Checking status' : 'Status: $status';
  }
}
