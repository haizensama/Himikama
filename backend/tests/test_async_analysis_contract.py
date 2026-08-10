"""Contract tests for idempotent, durable analysis attempts."""

from __future__ import annotations

import os
import unittest
from datetime import date, datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch
from uuid import UUID


os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("GEMINI_API_KEY", "test-only-key")
os.environ.setdefault("DB_PATH", ".")
os.environ.setdefault("FIREBASE_REQUIRE_APP_CHECK", "false")
os.environ.setdefault("ANALYSIS_WORKER_ENABLED", "false")

from fastapi.testclient import TestClient  # noqa: E402

from api.analysis_worker import AnalysisWorker  # noqa: E402
from api.auth import AuthenticatedUser, require_assessment_consent_user  # noqa: E402
from api.firebase import (  # noqa: E402
    AttemptConflictError,
    AttemptSubmission,
    ClaimedAnalysisJob,
    _claim_is_current,
    _intake_fingerprint,
    _job_is_claimable,
    _new_job_document,
)
from api.main import app  # noqa: E402
from api.routes.analysis import build_main_response  # noqa: E402


USER = AuthenticatedUser(
    uid="firebase-async-user",
    email="async@example.test",
    email_verified=True,
    auth_time=1,
    issued_at=1,
    provider="password",
    claims={},
)
ATTEMPT_ID = "b9dd68f9-e2bb-4aa5-8889-6bca4c8dab42"


async def authenticated_user() -> AuthenticatedUser:
    return USER


def valid_request() -> dict[str, object]:
    return {
        "attempt_id": ATTEMPT_ID,
        "intake": {
            "incident_date": date.today().isoformat(),
            "incident_location": "Colombo",
            "actor_name": "Police",
            "actor_role": "police officer",
            "what_happened": (
                "Police detained the user without explaining the reason."
            ),
            "harm_suffered": "Loss of liberty.",
            "user_narrative": (
                "Police detained me today without explaining the reason."
            ),
        },
    }


class DurableSubmissionContractTests(unittest.TestCase):
    def setUp(self) -> None:
        app.dependency_overrides.clear()
        app.dependency_overrides[require_assessment_consent_user] = authenticated_user
        self.client = TestClient(app)

    def tearDown(self) -> None:
        app.dependency_overrides.clear()

    def test_submit_uses_client_idempotency_key(self) -> None:
        submission = AttemptSubmission(
            attempt_id=ATTEMPT_ID,
            status="processing",
            created=True,
        )
        with patch(
            "api.routes.analysis.create_attempt",
            new=AsyncMock(return_value=submission),
        ) as create_mock:
            response = self.client.post(
                "/analysis/analyze",
                json=valid_request(),
            )

        self.assertEqual(response.status_code, 202)
        self.assertEqual(
            response.json(),
            {
                "success": True,
                "attempt_id": ATTEMPT_ID,
                "status": "processing",
                "idempotent_replay": False,
                "poll_url": f"/analysis/attempts/{ATTEMPT_ID}",
            },
        )
        self.assertEqual(
            create_mock.await_args.kwargs["attempt_id"],
            UUID(ATTEMPT_ID),
        )

    def test_duplicate_submission_returns_existing_attempt(self) -> None:
        submission = AttemptSubmission(
            attempt_id=ATTEMPT_ID,
            status="processing",
            created=False,
        )
        with patch(
            "api.routes.analysis.create_attempt",
            new=AsyncMock(return_value=submission),
        ):
            response = self.client.post(
                "/analysis/analyze",
                json=valid_request(),
            )

        self.assertEqual(response.status_code, 202)
        self.assertTrue(response.json()["idempotent_replay"])
        self.assertEqual(response.json()["attempt_id"], ATTEMPT_ID)

    def test_same_id_with_different_data_is_rejected(self) -> None:
        with patch(
            "api.routes.analysis.create_attempt",
            new=AsyncMock(side_effect=AttemptConflictError("conflict")),
        ):
            response = self.client.post(
                "/analysis/analyze",
                json=valid_request(),
            )

        self.assertEqual(response.status_code, 409)

    def test_failed_attempt_retry_keeps_the_same_id(self) -> None:
        submission = AttemptSubmission(
            attempt_id=ATTEMPT_ID,
            status="processing",
            created=False,
        )
        with patch(
            "api.routes.analysis.retry_attempt",
            new=AsyncMock(return_value=submission),
        ) as retry_mock:
            response = self.client.post(
                f"/analysis/attempts/{ATTEMPT_ID}/retry"
            )

        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json()["attempt_id"], ATTEMPT_ID)
        retry_mock.assert_awaited_once_with(
            user_id=USER.uid,
            attempt_id=UUID(ATTEMPT_ID),
        )

    def test_processing_attempt_is_not_reported_as_terminal(self) -> None:
        response = build_main_response(
            attempt_id=ATTEMPT_ID,
            source={"status": "processing"},
        )
        self.assertFalse(response["is_terminal"])
        self.assertEqual(response["main_answer"], "")

    def test_gate_outcomes_are_successful_terminal_results(self) -> None:
        for result_status in ("complete", "time_barred", "not_state_actor"):
            with self.subTest(status=result_status):
                response = build_main_response(
                    attempt_id=ATTEMPT_ID,
                    source={"status": result_status},
                )
                self.assertTrue(response["success"])
                self.assertTrue(response["is_terminal"])


class DurableJobDataTests(unittest.TestCase):
    def test_job_record_contains_no_intake_or_narrative(self) -> None:
        job = _new_job_document(
            uid=USER.uid,
            attempt_id=ATTEMPT_ID,
            now=datetime.now(timezone.utc),
        )
        self.assertNotIn("intake", job)
        self.assertNotIn("intake_object", job)
        self.assertNotIn("user_narrative", job)
        self.assertEqual(job["attempt_id"], ATTEMPT_ID)
        self.assertEqual(job["owner_uid"], USER.uid)

    def test_intake_fingerprint_is_stable_and_content_sensitive(self) -> None:
        first = {"actor_role": "police", "user_narrative": "Example facts"}
        reordered = {
            "user_narrative": "Example facts",
            "actor_role": "police",
        }
        changed = {**first, "actor_role": "army"}
        self.assertEqual(_intake_fingerprint(first), _intake_fingerprint(reordered))
        self.assertNotEqual(_intake_fingerprint(first), _intake_fingerprint(changed))

    def test_expired_lease_is_reclaimable(self) -> None:
        now = datetime.now(timezone.utc)
        self.assertTrue(
            _job_is_claimable(
                {
                    "status": "leased",
                    "lease_expires_at": now - timedelta(seconds=1),
                },
                now=now,
            )
        )
        self.assertFalse(
            _job_is_claimable(
                {
                    "status": "leased",
                    "lease_expires_at": now + timedelta(seconds=30),
                },
                now=now,
            )
        )

    def test_stale_generation_cannot_match_current_lease(self) -> None:
        stale_claim = ClaimedAnalysisJob(
            attempt_id=ATTEMPT_ID,
            owner_uid=USER.uid,
            lease_token="old-token",
            generation=1,
        )
        current_job = {
            "status": "leased",
            "owner_uid": USER.uid,
            "lease_token": "new-token",
            "generation": 2,
        }
        self.assertFalse(_claim_is_current(current_job, stale_claim))


class WorkerContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_successful_claim_is_completed_conditionally(self) -> None:
        claim = ClaimedAnalysisJob(
            attempt_id=ATTEMPT_ID,
            owner_uid=USER.uid,
            lease_token="lease-token",
            generation=2,
        )
        result = {"status": "complete", "step_results": {}}
        worker = AnalysisWorker(worker_id="test-worker")
        with (
            patch(
                "api.analysis_worker.get_attempt_intake",
                new=AsyncMock(return_value=valid_request()["intake"]),
            ),
            patch(
                "api.analysis_worker.run_full_chain",
                new=AsyncMock(return_value=result),
            ),
            patch(
                "api.analysis_worker.complete_claimed_analysis_job",
                new=AsyncMock(return_value=True),
            ) as complete_mock,
            patch(
                "api.analysis_worker.fail_claimed_analysis_job",
                new=AsyncMock(),
            ) as fail_mock,
        ):
            await worker._process_claim(claim)

        complete_mock.assert_awaited_once_with(claim=claim, result=result)
        fail_mock.assert_not_awaited()

    async def test_worker_failure_is_persisted_for_safe_retry(self) -> None:
        claim = ClaimedAnalysisJob(
            attempt_id=ATTEMPT_ID,
            owner_uid=USER.uid,
            lease_token="lease-token",
            generation=1,
        )
        worker = AnalysisWorker(worker_id="test-worker")
        with (
            patch(
                "api.analysis_worker.get_attempt_intake",
                new=AsyncMock(return_value=valid_request()["intake"]),
            ),
            patch(
                "api.analysis_worker.run_full_chain",
                new=AsyncMock(side_effect=RuntimeError("model failure")),
            ),
            patch(
                "api.analysis_worker.fail_claimed_analysis_job",
                new=AsyncMock(return_value=True),
            ) as fail_mock,
        ):
            await worker._process_claim(claim)

        fail_mock.assert_awaited_once_with(
            claim=claim,
            error_code="analysis_failed",
        )


if __name__ == "__main__":
    unittest.main()
