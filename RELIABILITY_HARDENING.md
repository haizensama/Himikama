# Himikama Reliability and Release Hardening v1

## What changed

- Flutter creates a UUID before submission and sends it as `attempt_id`.
- Firestore creates the private attempt and global identifier-only job in one
  transaction. Repeating the same UUID and intake returns the existing attempt.
- A lease-based worker starts with FastAPI, reclaims queued or expired jobs,
  renews its lease during analysis, and conditionally saves the result.
- Lease token and generation checks prevent an expired worker from overwriting
  a newer retry or recovery run.
- Failed attempts can be requeued through
  `POST /analysis/attempts/{attempt_id}/retry`; the attempt ID does not change.
- Flutter stores only the Firebase UID and attempt UUID in local preferences.
  It never stores the incident narrative in the recovery record.
- After login, the app offers to resume a pending attempt. A successful terminal
  result clears the local reference; a failed result keeps it available for a
  safe retry.
- Polling now backs off and distinguishes offline, timeout, authentication,
  authorization, server, missing-attempt, and unreadable-response states.

## Recovery model

The attempt document remains the source of the confirmed intake and legal
result. The `analysis_jobs` document contains identifiers and scheduling data
only. If FastAPI stops abruptly, the current lease expires after the configured
lease period and any replacement worker can run the chain again from Step 1.
This version guarantees safe whole-chain reprocessing; it does not checkpoint
and resume from an individual legal step.

Multiple FastAPI processes may run workers concurrently because job claiming
and result completion are transactional. Each worker processes one job at a
time. Scale API processes deliberately because every process also starts a
worker unless `ANALYSIS_WORKER_ENABLED=false`.

## App Check and release builds

Release Flutter builds require an explicit HTTPS endpoint:

```bash
flutter build apk --release \
  --dart-define=API_BASE_URL=https://api.example.com
```

Release builds activate Firebase App Check with Play Integrity on Android and
App Attest with DeviceCheck fallback on Apple platforms. Before enabling the
backend production configuration:

1. Register the Android app under Firebase Console > App Check.
2. Configure Play Integrity and add the release signing SHA-256 fingerprint.
3. Distribute a release build and verify App Check metrics.
4. Then set `FIREBASE_REQUIRE_APP_CHECK=true` on FastAPI.

For Android-emulator development, App Check remains off unless explicitly
enabled. To test the debug provider, run with
`--dart-define=APP_CHECK_ENABLED=true`, copy the printed debug token into the
Firebase Console, and only then set the backend requirement to true.

## Production backend requirements

Use `backend/production.env.example` as a name-only checklist. Production
startup intentionally fails unless project ID, revoked-token checking, verified
email, App Check, and the durable worker are enabled. Prefer workload identity
or Application Default Credentials; do not deploy a service-account JSON with
the application.

Logs contain request IDs, attempt IDs, worker generation, and error types. They
must not contain Firebase tokens, App Check tokens, incident narratives, intake
objects, or model prompts.
