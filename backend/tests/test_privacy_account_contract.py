"""Privacy, history-erasure, and recoverable account-deletion contracts."""

from __future__ import annotations

import os
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch
from uuid import UUID


os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("GEMINI_API_KEY", "test-only-key")
os.environ.setdefault("DB_PATH", ".")
os.environ.setdefault("FIREBASE_REQUIRE_APP_CHECK", "false")
os.environ.setdefault("ANALYSIS_WORKER_ENABLED", "false")
os.environ.setdefault("ACCOUNT_DELETION_WORKER_ENABLED", "false")

from fastapi.testclient import TestClient  # noqa: E402

from api.account_deletion_worker import AccountDeletionWorker  # noqa: E402
from api.auth import (  # noqa: E402
    AuthenticatedUser,
    require_active_user,
    require_assessment_consent_user,
    require_recent_user,
    require_verified_user,
)
from api.firebase import (  # noqa: E402
    ClaimedAccountDeletion,
    _deletion_claim_is_current,
    _deletion_is_claimable,
)
from api.main import app  # noqa: E402


USER = AuthenticatedUser(
    uid="firebase-privacy-user",
    email="privacy@example.test",
    email_verified=True,
    auth_time=int(datetime.now(timezone.utc).timestamp()),
    issued_at=1,
    provider="password",
    claims={},
)
ATTEMPT_ID = "b9dd68f9-e2bb-4aa5-8889-6bca4c8dab42"


async def authenticated_user() -> AuthenticatedUser:
    return USER


def active_profile(**overrides):
    now = datetime.now(timezone.utc)
    profile = {
        "owner_uid": USER.uid,
        "display_name": "Privacy User",
        "account_status": "active",
        "terms_version": "1.0",
        "privacy_version": "1.1",
        "assessment_consent_version": "1.0",
        "created_at": now,
        "updated_at": now,
    }
    profile.update(overrides)
    return profile


class PrivacyRouteContractTests(unittest.TestCase):
    def setUp(self) -> None:
        app.dependency_overrides.clear()
        self.client = TestClient(app)

    def tearDown(self) -> None:
        app.dependency_overrides.clear()

    def test_assessment_consent_is_bound_to_verified_uid(self) -> None:
        app.dependency_overrides[require_verified_user] = authenticated_user
        with patch(
            "api.routes.users.accept_assessment_consent",
            new=AsyncMock(return_value=active_profile()),
        ) as mocked:
            response = self.client.post(
                "/users/me/assessment-consent",
                json={"accept_assessment_processing": True},
            )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["profile"]["assessment_consent_current"])
        mocked.assert_awaited_once_with(uid=USER.uid)

    def test_assessment_consent_must_be_literal_true(self) -> None:
        app.dependency_overrides[require_verified_user] = authenticated_user
        response = self.client.post(
            "/users/me/assessment-consent",
            json={"accept_assessment_processing": False},
        )
        self.assertEqual(response.status_code, 422)

    def test_individual_deletion_uses_verified_owner_uid(self) -> None:
        app.dependency_overrides[require_active_user] = authenticated_user
        with patch(
            "api.routes.analysis.delete_attempt",
            new=AsyncMock(return_value=None),
        ) as mocked:
            response = self.client.delete(
                f"/analysis/attempts/{ATTEMPT_ID}?user_id=someone-else"
            )

        self.assertEqual(response.status_code, 200)
        mocked.assert_awaited_once_with(
            user_id=USER.uid,
            attempt_id=UUID(ATTEMPT_ID),
        )

    def test_clear_history_uses_verified_owner_uid(self) -> None:
        app.dependency_overrides[require_active_user] = authenticated_user
        with patch(
            "api.routes.analysis.delete_user_history",
            new=AsyncMock(return_value=None),
        ) as mocked:
            response = self.client.delete("/analysis/history")

        self.assertEqual(response.status_code, 200)
        mocked.assert_awaited_once_with(user_id=USER.uid)

    def test_account_deletion_is_scheduled_not_immediate(self) -> None:
        app.dependency_overrides[require_recent_user] = authenticated_user
        deadline = datetime.now(timezone.utc) + timedelta(days=7)
        scheduled = active_profile(
            account_status="deletion_scheduled",
            deletion_requested_at=datetime.now(timezone.utc),
            deletion_effective_at=deadline,
        )
        with patch(
            "api.routes.users.schedule_account_deletion",
            new=AsyncMock(return_value=scheduled),
        ) as mocked:
            response = self.client.request(
                "DELETE",
                "/users/me",
                json={"confirmation": "DELETE"},
            )

        self.assertEqual(response.status_code, 202)
        self.assertEqual(
            response.json()["profile"]["account_status"],
            "deletion_scheduled",
        )
        self.assertEqual(response.json()["recovery_days"], 7)
        mocked.assert_awaited_once_with(uid=USER.uid)

    def test_new_assessment_requires_specific_processing_consent(self) -> None:
        app.dependency_overrides.pop(require_assessment_consent_user, None)
        with (
            patch("api.auth.require_active_user", return_value=USER),
            patch(
                "api.auth.get_user_profile",
                new=AsyncMock(
                    return_value=active_profile(
                        assessment_consent_version=""
                    )
                ),
            ),
        ):
            # Exercise the dependency directly because FastAPI resolves its
            # nested dependency before route dispatch.
            import asyncio

            from fastapi import HTTPException

            with self.assertRaises(HTTPException) as caught:
                asyncio.run(require_assessment_consent_user(user=USER))
        self.assertEqual(caught.exception.status_code, 403)


class DeletionStateContractTests(unittest.TestCase):
    def test_recovery_period_blocks_early_cleanup(self) -> None:
        now = datetime.now(timezone.utc)
        self.assertFalse(
            _deletion_is_claimable(
                {
                    "account_status": "deletion_scheduled",
                    "deletion_effective_at": now + timedelta(days=1),
                },
                now=now,
            )
        )
        self.assertTrue(
            _deletion_is_claimable(
                {
                    "account_status": "deletion_scheduled",
                    "deletion_effective_at": now - timedelta(seconds=1),
                },
                now=now,
            )
        )

    def test_stale_deletion_worker_cannot_finalize_profile(self) -> None:
        claim = ClaimedAccountDeletion(
            owner_uid=USER.uid,
            lease_token="old-token",
        )
        self.assertFalse(
            _deletion_claim_is_current(
                {
                    "owner_uid": USER.uid,
                    "account_status": "deletion_processing",
                    "deletion_lease_token": "new-token",
                },
                claim,
            )
        )

    def test_firestore_client_access_remains_deny_all(self) -> None:
        rules = (Path(__file__).parents[1] / "firestore.rules").read_text()
        self.assertIn("allow read, write: if false", rules)


class AccountDeletionWorkerTests(unittest.IsolatedAsyncioTestCase):
    async def test_worker_erases_data_auth_and_profile_in_order(self) -> None:
        claim = ClaimedAccountDeletion(USER.uid, "lease-token")
        calls: list[str] = []

        async def erase(*, uid: str) -> None:
            self.assertEqual(uid, USER.uid)
            calls.append("data")

        async def delete_auth(uid: str) -> None:
            self.assertEqual(uid, USER.uid)
            calls.append("auth")

        async def complete(*, claim: ClaimedAccountDeletion) -> bool:
            calls.append("profile")
            return True

        worker = AccountDeletionWorker(worker_id="test-worker")
        with (
            patch(
                "api.account_deletion_worker.erase_user_assessment_data",
                new=erase,
            ),
            patch(
                "api.account_deletion_worker.delete_firebase_auth_user_if_exists",
                new=delete_auth,
            ),
            patch(
                "api.account_deletion_worker.complete_account_deletion",
                new=complete,
            ),
        ):
            await worker._process_claim(claim)

        self.assertEqual(calls, ["data", "auth", "profile"])

    async def test_worker_releases_claim_after_failure(self) -> None:
        claim = ClaimedAccountDeletion(USER.uid, "lease-token")
        with (
            patch(
                "api.account_deletion_worker.erase_user_assessment_data",
                new=AsyncMock(side_effect=RuntimeError("temporary")),
            ),
            patch(
                "api.account_deletion_worker.release_account_deletion",
                new=AsyncMock(return_value=True),
            ) as release,
        ):
            await AccountDeletionWorker(worker_id="test")._process_claim(claim)
        release.assert_awaited_once_with(claim=claim)


if __name__ == "__main__":
    unittest.main()
