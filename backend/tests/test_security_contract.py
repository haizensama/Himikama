"""Security-contract tests for the authenticated Himikama API.

Run after copying the replacement files into the real backend:
    python -m unittest discover -s tests -v
"""

from __future__ import annotations

import os
import unittest
from unittest.mock import AsyncMock, patch


os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("GEMINI_API_KEY", "test-only-key")
os.environ.setdefault("DB_PATH", ".")
os.environ.setdefault("FIREBASE_REQUIRE_APP_CHECK", "false")

from fastapi.testclient import TestClient  # noqa: E402

from api.auth import (  # noqa: E402
    AuthenticatedUser,
    require_active_user,
)
from api.main import app  # noqa: E402
from api.routes.analysis import build_reasoning_trace  # noqa: E402
from api import firebase as firebase_module  # noqa: E402


USER_A = AuthenticatedUser(
    uid="firebase-user-a",
    email="user-a@example.test",
    email_verified=True,
    auth_time=1,
    issued_at=1,
    provider="password",
    claims={},
)


async def authenticated_user_a() -> AuthenticatedUser:
    return USER_A


class SecurityContractTests(unittest.TestCase):
    def setUp(self) -> None:
        app.dependency_overrides.clear()
        self.client = TestClient(app)

    def tearDown(self) -> None:
        app.dependency_overrides.clear()

    def test_protected_history_rejects_missing_token(self) -> None:
        response = self.client.get("/analysis/history")
        self.assertEqual(response.status_code, 401)

    def test_unverified_email_is_rejected(self) -> None:
        claims = {
            "uid": "firebase-user-a",
            "sub": "firebase-user-a",
            "email": "user-a@example.test",
            "email_verified": False,
            "auth_time": 1,
            "iat": 1,
            "firebase": {"sign_in_provider": "password"},
        }
        with (
            patch("api.auth.get_firebase_app", return_value=object()),
            patch("api.auth.firebase_auth.verify_id_token", return_value=claims),
        ):
            response = self.client.get(
                "/users/me",
                headers={"Authorization": "Bearer test-token"},
            )
        self.assertEqual(response.status_code, 403)

    def test_invalid_token_is_rejected_without_internal_error_details(self) -> None:
        with (
            patch("api.auth.get_firebase_app", return_value=object()),
            patch(
                "api.auth.firebase_auth.verify_id_token",
                side_effect=ValueError("sensitive internal verifier detail"),
            ),
        ):
            response = self.client.get(
                "/users/me",
                headers={"Authorization": "Bearer invalid-token"},
            )
        self.assertEqual(response.status_code, 401)
        self.assertNotIn("sensitive", response.text)

    def test_query_parameter_cannot_override_verified_uid(self) -> None:
        app.dependency_overrides[require_active_user] = authenticated_user_a

        with patch(
            "api.routes.analysis.get_user_history",
            new=AsyncMock(return_value=[]),
        ) as mocked_history:
            response = self.client.get(
                "/analysis/history?user_id=firebase-user-b&limit=20"
            )

        self.assertEqual(response.status_code, 200)
        mocked_history.assert_awaited_once_with(
            user_id="firebase-user-a",
            limit=20,
        )
        self.assertNotIn("user_id", response.json())

    def test_attempt_lookup_uses_verified_uid(self) -> None:
        app.dependency_overrides[require_active_user] = authenticated_user_a
        attempt_id = "6b77ab99-9882-4841-9683-26d55e0db5f3"

        with patch(
            "api.routes.analysis.get_attempt",
            new=AsyncMock(side_effect=FileNotFoundError),
        ) as mocked_get_attempt:
            response = self.client.get(
                f"/analysis/attempts/{attempt_id}?user_id=firebase-user-b"
            )

        self.assertEqual(response.status_code, 404)
        mocked_get_attempt.assert_awaited_once()
        self.assertEqual(
            mocked_get_attempt.await_args.kwargs["user_id"],
            "firebase-user-a",
        )

    def test_malformed_attempt_id_is_rejected_before_storage(self) -> None:
        app.dependency_overrides[require_active_user] = authenticated_user_a
        response = self.client.get("/analysis/attempts/not-a-uuid")
        self.assertEqual(response.status_code, 422)

    def test_history_limit_is_bounded(self) -> None:
        app.dependency_overrides[require_active_user] = authenticated_user_a
        response = self.client.get("/analysis/history?limit=100000")
        self.assertEqual(response.status_code, 422)

    def test_reasoning_trace_excludes_raw_internal_data(self) -> None:
        trace = build_reasoning_trace(
            {
                "step_1": {
                    "passed": True,
                    "answer": "Within time",
                    "explanation": "The supplied date is within the limit.",
                    "data": {"internal_only": "must-not-leak"},
                }
            }
        )
        self.assertEqual(len(trace), 1)
        self.assertNotIn("data", trace[0])
        self.assertNotIn("must-not-leak", str(trace))

    def test_storage_rejects_an_owner_uid_mismatch(self) -> None:
        class Snapshot:
            exists = True

            @staticmethod
            def to_dict():
                return {
                    "attempt_id": "6b77ab99-9882-4841-9683-26d55e0db5f3",
                    "owner_uid": "firebase-user-b",
                }

        class Reference:
            @staticmethod
            def get():
                return Snapshot()

        with patch("api.firebase._attempt_ref", return_value=Reference()):
            with self.assertRaises(FileNotFoundError):
                firebase_module._owned_attempt_sync(
                    "firebase-user-a",
                    "6b77ab99-9882-4841-9683-26d55e0db5f3",
                )


if __name__ == "__main__":
    unittest.main()
