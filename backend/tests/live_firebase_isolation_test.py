#!/usr/bin/env python3
"""Live Firebase/FastAPI isolation test for Himikama.

Run this file from the Himikama backend directory while the API is available at
http://127.0.0.1:8000. It uses Firebase Authentication's client REST API, just
as a mobile client would, and never writes passwords or tokens to disk.

First run:
    Creates/signs in two test accounts and sends verification emails when
    necessary. Verify both addresses, then run the script again.

Second run:
    Creates the two Himikama profiles, runs one analysis as User A, and proves
    that User B cannot see or retrieve User A's attempt.
"""

from __future__ import annotations

import getpass
import sys
from dataclasses import dataclass
from datetime import date
from typing import Any
from urllib.parse import quote

import httpx


DEFAULT_API_BASE = "http://127.0.0.1:8000"
FIREBASE_AUTH_BASE = "https://identitytoolkit.googleapis.com/v1"


class TestFailure(RuntimeError):
    """A safe, user-facing live-test failure."""


@dataclass(frozen=True)
class TestAccount:
    label: str
    email: str
    password: str
    display_name: str
    uid: str = ""
    id_token: str = ""
    email_verified: bool = False


def _json_object(response: httpx.Response) -> dict[str, Any]:
    try:
        value = response.json()
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def _firebase_error(response: httpx.Response) -> str:
    payload = _json_object(response)
    error = payload.get("error")
    if isinstance(error, dict):
        message = str(error.get("message") or "").strip()
        if message:
            return message
    return f"Firebase returned HTTP {response.status_code}"


def _firebase_post(
    client: httpx.Client,
    *,
    api_key: str,
    endpoint: str,
    body: dict[str, Any],
) -> httpx.Response:
    return client.post(
        f"{FIREBASE_AUTH_BASE}/{endpoint}?key={quote(api_key, safe='')}",
        json=body,
        headers={"Content-Type": "application/json"},
    )


def _send_verification_email(
    client: httpx.Client,
    *,
    api_key: str,
    id_token: str,
) -> None:
    response = _firebase_post(
        client,
        api_key=api_key,
        endpoint="accounts:sendOobCode",
        body={"requestType": "VERIFY_EMAIL", "idToken": id_token},
    )
    if response.status_code != 200:
        raise TestFailure(
            "Could not send the verification email: "
            f"{_firebase_error(response)}"
        )


def _lookup_email_verified(
    client: httpx.Client,
    *,
    api_key: str,
    id_token: str,
) -> bool:
    response = _firebase_post(
        client,
        api_key=api_key,
        endpoint="accounts:lookup",
        body={"idToken": id_token},
    )
    if response.status_code != 200:
        raise TestFailure(
            "Could not read the Firebase account state: "
            f"{_firebase_error(response)}"
        )
    users = _json_object(response).get("users")
    if not isinstance(users, list) or not users or not isinstance(users[0], dict):
        raise TestFailure("Firebase did not return the expected user record")
    return users[0].get("emailVerified") is True


def _register_or_sign_in(
    client: httpx.Client,
    *,
    api_key: str,
    account: TestAccount,
) -> TestAccount:
    signup = _firebase_post(
        client,
        api_key=api_key,
        endpoint="accounts:signUp",
        body={
            "email": account.email,
            "password": account.password,
            "returnSecureToken": True,
        },
    )

    created = signup.status_code == 200
    if created:
        auth_payload = _json_object(signup)
        print(f"[INFO] {account.label}: Firebase test account created")
    else:
        signup_error = _firebase_error(signup)
        if not signup_error.startswith("EMAIL_EXISTS"):
            raise TestFailure(
                f"{account.label} could not be registered: {signup_error}"
            )

        signin = _firebase_post(
            client,
            api_key=api_key,
            endpoint="accounts:signInWithPassword",
            body={
                "email": account.email,
                "password": account.password,
                "returnSecureToken": True,
            },
        )
        if signin.status_code != 200:
            raise TestFailure(
                f"{account.label} could not sign in: {_firebase_error(signin)}"
            )
        auth_payload = _json_object(signin)
        print(f"[INFO] {account.label}: signed in to existing Firebase account")

    id_token = str(auth_payload.get("idToken") or "").strip()
    uid = str(auth_payload.get("localId") or "").strip()
    if not id_token or not uid:
        raise TestFailure(
            f"{account.label}: Firebase did not return an ID token and UID"
        )

    verified = _lookup_email_verified(
        client,
        api_key=api_key,
        id_token=id_token,
    )
    if not verified:
        _send_verification_email(
            client,
            api_key=api_key,
            id_token=id_token,
        )
        print(
            f"[ACTION REQUIRED] {account.label}: verification email sent to "
            f"{account.email}"
        )
    else:
        print(f"[PASS] {account.label}: email is verified")

    return TestAccount(
        label=account.label,
        email=account.email,
        password=account.password,
        display_name=account.display_name,
        uid=uid,
        id_token=id_token,
        email_verified=verified,
    )


def _api_headers(account: TestAccount) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {account.id_token}",
        "Content-Type": "application/json",
    }


def _api_error(response: httpx.Response) -> str:
    payload = _json_object(response)
    detail = str(payload.get("detail") or "").strip()
    return detail or f"Himikama API returned HTTP {response.status_code}"


def _require_status(
    response: httpx.Response,
    expected: set[int],
    operation: str,
) -> dict[str, Any]:
    if response.status_code not in expected:
        raise TestFailure(f"{operation} failed: {_api_error(response)}")
    return _json_object(response)


def _create_profile(
    client: httpx.Client,
    *,
    api_base: str,
    account: TestAccount,
) -> None:
    response = client.post(
        f"{api_base}/users/me/profile",
        headers=_api_headers(account),
        json={
            "display_name": account.display_name,
            "accept_terms": True,
            "accept_privacy_policy": True,
        },
    )
    payload = _require_status(
        response,
        {200, 201},
        f"Creating {account.label}'s Himikama profile",
    )
    profile = payload.get("profile")
    if not isinstance(profile, dict) or profile.get("account_status") != "active":
        raise TestFailure(f"{account.label}'s profile is not active")
    print(f"[PASS] {account.label}: active Himikama profile is available")


def _history(
    client: httpx.Client,
    *,
    api_base: str,
    account: TestAccount,
    extra_query: str = "",
) -> list[dict[str, Any]]:
    response = client.get(
        f"{api_base}/analysis/history?limit=50{extra_query}",
        headers=_api_headers(account),
    )
    payload = _require_status(
        response,
        {200},
        f"Reading {account.label}'s history",
    )
    items = payload.get("items")
    if not isinstance(items, list):
        raise TestFailure(f"{account.label}'s history response is malformed")
    return [item for item in items if isinstance(item, dict)]


def _attempt_ids(items: list[dict[str, Any]]) -> set[str]:
    return {
        str(item.get("attempt_id") or "").strip()
        for item in items
        if str(item.get("attempt_id") or "").strip()
    }


def _run_analysis(
    client: httpx.Client,
    *,
    api_base: str,
    account: TestAccount,
) -> str:
    today = date.today().isoformat()
    response = client.post(
        f"{api_base}/analysis/analyze",
        headers=_api_headers(account),
        json={
            "intake": {
                "incident_date": today,
                "incident_location": "Colombo",
                "actor_name": "Sri Lanka Police",
                "actor_role": "police officers",
                "what_happened": (
                    "During this fictional integration test, police officers "
                    "detained the test user without explaining the legal reason."
                ),
                "harm_suffered": (
                    "The fictional test user experienced a temporary loss of "
                    "liberty and emotional distress."
                ),
                "user_narrative": (
                    f"On {today}, during a fictional software integration test "
                    "in Colombo, police officers detained me without explaining "
                    "the legal reason and released me later that day."
                ),
            }
        },
    )
    payload = _require_status(response, {200}, "Running User A's analysis")
    attempt_id = str(payload.get("attempt_id") or "").strip()
    if not attempt_id:
        raise TestFailure("The analysis response did not include an attempt ID")
    print(f"[PASS] User A: analysis completed (attempt {attempt_id})")
    return attempt_id


def _perform_isolation_test(
    client: httpx.Client,
    *,
    api_base: str,
    user_a: TestAccount,
    user_b: TestAccount,
) -> None:
    if user_a.uid == user_b.uid:
        raise TestFailure("User A and User B resolved to the same Firebase UID")

    health = client.get(f"{api_base}/health")
    _require_status(health, {200}, "Checking the Himikama API")
    print("[PASS] Himikama API is reachable")

    _create_profile(client, api_base=api_base, account=user_a)
    _create_profile(client, api_base=api_base, account=user_b)

    answer = input(
        "This will run one Gemini-backed Himikama analysis and may incur a "
        "small API charge. Continue? [y/N]: "
    ).strip().lower()
    if answer not in {"y", "yes"}:
        raise TestFailure("Test stopped before the analysis; no isolation result")

    attempt_id = _run_analysis(
        client,
        api_base=api_base,
        account=user_a,
    )

    a_items = _history(client, api_base=api_base, account=user_a)
    if attempt_id not in _attempt_ids(a_items):
        raise TestFailure("User A's new attempt is missing from User A's history")
    print("[PASS] User A can see User A's new attempt")

    # Try the UID-spoofing query as User B. The backend must ignore it and use
    # the UID from User B's verified token.
    b_items = _history(
        client,
        api_base=api_base,
        account=user_b,
        extra_query=f"&user_id={quote(user_a.uid, safe='')}",
    )
    if attempt_id in _attempt_ids(b_items):
        raise TestFailure("ISOLATION FAILURE: User B saw User A's history item")
    print("[PASS] User B cannot see User A's attempt in history")

    a_result = client.get(
        f"{api_base}/analysis/attempts/{attempt_id}",
        headers=_api_headers(user_a),
    )
    _require_status(a_result, {200}, "Reading User A's saved result as User A")
    print("[PASS] User A can retrieve User A's saved result")

    b_result = client.get(
        f"{api_base}/analysis/attempts/{attempt_id}"
        f"?user_id={quote(user_a.uid, safe='')}",
        headers=_api_headers(user_b),
    )
    if b_result.status_code != 404:
        raise TestFailure(
            "ISOLATION FAILURE: User B's direct request for User A's attempt "
            f"returned HTTP {b_result.status_code}, expected 404"
        )
    print("[PASS] User B receives 404 for User A's saved result")

    print("\nLIVE ISOLATION TEST: PASSED")
    print("The two Firebase users have distinct, owner-isolated Himikama data.")


def _prompt_account(label: str, display_name: str) -> TestAccount:
    email = input(f"{label} email: ").strip().lower()
    if not email or "@" not in email:
        raise TestFailure(f"{label} email is invalid")
    password = getpass.getpass(f"{label} password (input is hidden): ")
    if not password:
        raise TestFailure(f"{label} password cannot be empty")
    return TestAccount(
        label=label,
        email=email,
        password=password,
        display_name=display_name,
    )


def main() -> int:
    print("Himikama live Firebase User A/User B isolation test")
    print("Passwords and Firebase tokens remain only in memory.\n")

    api_base = input(
        f"Himikama API base URL [{DEFAULT_API_BASE}]: "
    ).strip() or DEFAULT_API_BASE
    api_base = api_base.rstrip("/")

    api_key = getpass.getpass(
        "Firebase Web API key (input is hidden and not saved): "
    ).strip()
    if not api_key:
        raise TestFailure("Firebase Web API key cannot be empty")

    user_a_input = _prompt_account("User A", "Himikama Test User A")
    user_b_input = _prompt_account("User B", "Himikama Test User B")
    if user_a_input.email == user_b_input.email:
        raise TestFailure("User A and User B must use different email addresses")

    timeout = httpx.Timeout(900.0, connect=15.0)
    with httpx.Client(timeout=timeout, follow_redirects=False) as client:
        user_a = _register_or_sign_in(
            client,
            api_key=api_key,
            account=user_a_input,
        )
        user_b = _register_or_sign_in(
            client,
            api_key=api_key,
            account=user_b_input,
        )

        if not user_a.email_verified or not user_b.email_verified:
            print("\nVerification is required before the test can continue.")
            print("Open both Firebase verification emails and click their links.")
            print("Then run this same script again with the same credentials.")
            return 2

        _perform_isolation_test(
            client,
            api_base=api_base,
            user_a=user_a,
            user_b=user_b,
        )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nTest cancelled. No credentials were saved.", file=sys.stderr)
        raise SystemExit(130) from None
    except (httpx.HTTPError, TestFailure) as exc:
        print(f"\nTEST STOPPED: {exc}", file=sys.stderr)
        raise SystemExit(1) from None
