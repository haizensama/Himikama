"""Contract tests for authenticated Step 0 intake structuring."""

from __future__ import annotations

import os
import unittest
from datetime import date, timedelta
from unittest.mock import AsyncMock, patch


os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("GEMINI_API_KEY", "test-only-key")
os.environ.setdefault("DB_PATH", ".")
os.environ.setdefault("FIREBASE_REQUIRE_APP_CHECK", "false")

from fastapi.testclient import TestClient  # noqa: E402

from api.auth import AuthenticatedUser, require_assessment_consent_user  # noqa: E402
from api.main import app  # noqa: E402
from chain.intake import _validate_and_clean  # noqa: E402


USER = AuthenticatedUser(
    uid="firebase-intake-user",
    email="intake@example.test",
    email_verified=True,
    auth_time=1,
    issued_at=1,
    provider="password",
    claims={},
)


async def authenticated_user() -> AuthenticatedUser:
    return USER


class IntakeContractTests(unittest.TestCase):
    def setUp(self) -> None:
        app.dependency_overrides.clear()
        self.client = TestClient(app)

    def tearDown(self) -> None:
        app.dependency_overrides.clear()

    def test_structure_intake_requires_authentication(self) -> None:
        response = self.client.post(
            "/analysis/structure-intake",
            json={
                "raw_user_description": (
                    "Police arrested me yesterday without explaining why."
                )
            },
        )
        self.assertEqual(response.status_code, 401)

    def test_complete_extraction_can_be_confirmed(self) -> None:
        app.dependency_overrides[require_assessment_consent_user] = authenticated_user
        narrative = (
            "Police arrested me on 2026-07-24 without explaining why."
        )
        extracted = {
            "incident_date": "2026-07-24",
            "incident_location": None,
            "actor_name": "Police",
            "actor_role": "police officer",
            "what_happened": (
                "Police arrested the user without explaining the reason."
            ),
            "harm_suffered": "Loss of liberty.",
            "user_narrative": narrative,
        }

        with patch(
            "api.routes.analysis.extract_intake",
            new=AsyncMock(return_value=(extracted, "Review these details.")),
        ):
            response = self.client.post(
                "/analysis/structure-intake",
                json={"raw_user_description": narrative},
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "needs_confirmation")
        self.assertTrue(payload["can_confirm"])
        self.assertEqual(payload["missing_required_fields"], [])
        self.assertEqual(payload["intake"]["incident_date"], "2026-07-24")

    def test_missing_actor_role_requires_clarification(self) -> None:
        app.dependency_overrides[require_assessment_consent_user] = authenticated_user
        narrative = "Someone detained me on 2026-07-24 without a reason."
        extracted = {
            "incident_date": "2026-07-24",
            "incident_location": None,
            "actor_name": None,
            "actor_role": None,
            "what_happened": "Someone detained the user without a reason.",
            "harm_suffered": "Loss of liberty.",
            "user_narrative": narrative,
        }

        with patch(
            "api.routes.analysis.extract_intake",
            new=AsyncMock(return_value=(extracted, "Review these details.")),
        ):
            response = self.client.post(
                "/analysis/structure-intake",
                json={"raw_user_description": narrative},
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "needs_clarification")
        self.assertFalse(payload["can_confirm"])
        self.assertEqual(payload["missing_required_fields"], ["actor_role"])
        self.assertEqual(len(payload["clarifying_questions"]), 1)

    def test_descriptive_or_future_dates_are_not_forwarded(self) -> None:
        narrative = "Police detained me and did not explain why."
        base = {
            "incident_location": None,
            "actor_name": "Police",
            "actor_role": "police officer",
            "what_happened": "Police detained the user without explanation.",
            "harm_suffered": "Loss of liberty.",
            "user_narrative": narrative,
        }

        descriptive = _validate_and_clean(
            {"incident_date": "last Tuesday", **base},
            narrative,
        )
        future = _validate_and_clean(
            {
                "incident_date": (date.today() + timedelta(days=1)).isoformat(),
                **base,
            },
            narrative,
        )

        self.assertIsNone(descriptive["incident_date"])
        self.assertIsNone(future["incident_date"])


if __name__ == "__main__":
    unittest.main()
