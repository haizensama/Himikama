# Himikama secure backend replacement

This package upgrades the existing Himikama FastAPI backend so that every
legal analysis is owned by a verified Firebase Authentication user.

It does not modify the ten-step reasoning chain. Copy the supplied files over
the matching paths in the existing project and keep the current `chain/`,
`ingestion/`, and `db/` directories.

## Security model

1. Flutter creates or signs in the account with Firebase Authentication.
2. Flutter sends the Firebase ID token in `Authorization: Bearer <token>`.
3. FastAPI verifies the token, revocation status, email verification, and
   non-anonymous provider.
4. FastAPI derives the UID from the verified token. No endpoint accepts a UID
   supplied by the client.
5. Firestore records are stored under
   `users/{verified_uid}/attempts/{attempt_id}` and contain a second
   `owner_uid` integrity field.
6. The persistence layer rechecks the account state immediately before a new
   attempt write, closing route-level account-deletion race windows.
7. Firestore rules deny all direct mobile/web reads and writes. Only the
   backend Admin SDK accesses legal records.

## Files supplied

- `backend/api/auth.py` - Firebase ID token, optional App Check, active-profile,
  and recent-login dependencies.
- `backend/api/firebase.py` - backend-only Firebase initialization, user
  profiles, owned attempts, history, and account-data deletion.
- `backend/api/routes/analysis.py` - authenticated analysis, history, result,
  and sanitized trace endpoints.
- `backend/api/routes/users.py` - profile, policy consent, and deletion routes.
- `backend/api/config.py` - centralized safe configuration.
- `backend/api/schemas.py` - strict field, size, date, and control-character
  validation.
- `backend/api/main.py` - safer CORS, request-size handling, generic errors,
  no-store headers, and protected router wiring.
- `backend/tests/test_security_contract.py` - focused authorization tests.
- `firestore.rules` - deny-all direct Firestore access.
- `.gitignore` and `backend/.env.example`.

The existing `firebase-admin==7.4.0`, FastAPI, Pydantic, HTTPX, and
python-dotenv dependencies already listed in the uploaded requirements file
cover this package. Remove the duplicate unpinned `firebase-admin` line from
the top of the current requirements file, retaining the pinned entry.

## Installation

From the existing project root, make a non-secret backup before replacing
files. Do not copy the `backend/secrets` directory into the backup if the
backup might be shared.

Copy the contents of this package over the project so that, for example,
`backend/api/auth.py` lands at `~/Documents/himikama/backend/api/auth.py`.

Keep the existing service account only at:

```text
backend/secrets/firebase-service-account.json
```

Never put that JSON file in Flutter, a ZIP sent to someone else, or Git.

Copy the example configuration locally:

```bash
cd ~/Documents/himikama/backend
cp .env.example .env
```

Then edit `.env` locally and provide the real Gemini key and Firebase project
ID. Do not paste the resulting `.env` into chat or source control.

## Firebase Console

Under Authentication, enable Email/Password, require a strong password policy,
and configure the email-verification and password-reset templates. Do not
enable anonymous authentication.

Keep the Firestore rules in this package unchanged. They deliberately deny all
direct Firestore client access. The Firebase Admin SDK on FastAPI bypasses
these rules, which is why backend authorization remains mandatory.

## Account lifecycle

The upcoming Flutter registration flow must:

1. Create the Firebase email/password account.
2. Send the verification email.
3. Block the dashboard until `emailVerified` is true.
4. Refresh the Firebase user and ID token after verification.
5. Call `POST /users/me/profile` with the verified token, display name, and
   accepted policy booleans.
6. Use the returned authenticated session for all analysis routes.

## Protected API contract

Every route below requires `Authorization: Bearer <firebase-id-token>`:

```text
POST   /users/me/profile
GET    /users/me
PATCH  /users/me/profile
POST   /users/me/policies
DELETE /users/me
POST   /analysis/validate-intake
POST   /analysis/analyze
GET    /analysis/history?limit=20
GET    /analysis/attempts/{attempt_id}
GET    /analysis/attempts/{attempt_id}/trace
```

Only `/`, `/health`, and development API documentation are public.

The account must have a verified email and an active Himikama profile with the
current policy versions before it can access analysis routes. Account deletion
requires a Firebase sign-in no older than the configured recent-auth window.

## Run tests

Activate the existing backend virtual environment and run:

```bash
cd ~/Documents/himikama/backend
python -m unittest discover -s tests -v
```

The tests check missing-token rejection, UID spoofing resistance, owner-bound
attempt lookup, UUID validation, bounded history, and sanitized trace output.

## Run locally

```bash
cd ~/Documents/himikama/backend
uvicorn api.main:app --reload --host 127.0.0.1 --port 8000
```

Use `127.0.0.1` during backend-only development. When testing from a physical
phone on the same network, bind to a private LAN interface only and configure
the operating-system firewall. Do not expose the development server directly
to the public internet.

## Before production

- Configure Flutter App Check, then set
  `FIREBASE_REQUIRE_APP_CHECK=true` on the backend.
- Put FastAPI behind HTTPS and a managed reverse proxy/API gateway.
- Add distributed rate limiting keyed by verified UID and IP address.
- Store production credentials with workload identity or a managed secret
  service instead of a long-lived JSON key file.
- Define and enforce a legal-report retention period.
- Test account deletion against every subcollection added in the future.
- Run two-account isolation tests in a separate Firebase development project.
