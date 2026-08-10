"""Contract tests for Firebase App Check enforcement on protected routes."""

from __future__ import annotations

import os
import unittest
from dataclasses import replace
from unittest.mock import AsyncMock, patch


os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("GEMINI_API_KEY", "test-only-key")
os.environ.setdefault("DB_PATH", ".")
os.environ.setdefault("FIREBASE_REQUIRE_APP_CHECK", "false")

from fastapi.testclient import TestClient  # noqa: E402

from api import auth as auth_module  # noqa: E402
from api.main import app  # noqa: E402


ANDROID_APP_ID = "1:140045392784:android:b2ad7b3de8de33f6fa3115"
OTHER_APP_ID = "1:140045392784:web:unapproved-app"

VERIFIED_CLAIMS = {
    "uid": "firebase-user-a",
    "sub": "firebase-user-a",
    "email": "user-a@example.test",
    "email_verified": True,
    "auth_time": 1,
    "iat": 1,
    "firebase": {"sign_in_provider": "password"},
}

PROFILE = {
    "owner_uid": "firebase-user-a",
    "display_name": "User A",
    "account_status": "active",
    "terms_version": "1.0",
    "privacy_version": "1.1",
    "assessment_consent_version": "1.0",
}


class AppCheckContractTests(unittest.TestCase):
    def setUp(self) -> None:
        app.dependency_overrides.clear()
        self.client = TestClient(app)

    def tearDown(self) -> None:
        app.dependency_overrides.clear()

    @staticmethod
    def _secured_config(*, allowed_app_ids: tuple[str, ...] = ()):
        return replace(
            auth_module.config,
            firebase_require_app_check=True,
            firebase_app_check_allowed_app_ids=allowed_app_ids,
        )

    def test_missing_app_check_token_is_rejected_before_auth_verification(
        self,
    ) -> None:
        secured_config = self._secured_config()
        with (
            patch.object(auth_module, "config", secured_config),
            patch("api.auth.app_check.verify_token") as verify_app_check,
            patch("api.auth.firebase_auth.verify_id_token") as verify_auth,
        ):
            response = self.client.get(
                "/users/me",
                headers={"Authorization": "Bearer valid-auth-token"},
            )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["detail"], "App verification required")
        verify_app_check.assert_not_called()
        verify_auth.assert_not_called()

    def test_invalid_app_check_token_is_rejected_without_internal_details(
        self,
    ) -> None:
        secured_config = self._secured_config()
        firebase_app = object()
        with (
            patch.object(auth_module, "config", secured_config),
            patch("api.auth.get_firebase_app", return_value=firebase_app),
            patch(
                "api.auth.app_check.verify_token",
                side_effect=ValueError("sensitive verifier detail"),
            ) as verify_app_check,
            patch("api.auth.firebase_auth.verify_id_token") as verify_auth,
        ):
            response = self.client.get(
                "/users/me",
                headers={
                    "Authorization": "Bearer valid-auth-token",
                    "X-Firebase-AppCheck": "invalid-app-check-token",
                },
            )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["detail"], "App verification failed")
        self.assertNotIn("sensitive", response.text)
        verify_app_check.assert_called_once_with(
            "invalid-app-check-token",
            app=firebase_app,
        )
        verify_auth.assert_not_called()

    def test_valid_auth_and_approved_app_check_token_are_accepted(self) -> None:
        secured_config = self._secured_config(allowed_app_ids=(ANDROID_APP_ID,))
        firebase_app = object()
        with (
            patch.object(auth_module, "config", secured_config),
            patch("api.auth.get_firebase_app", return_value=firebase_app),
            patch(
                "api.auth.app_check.verify_token",
                return_value={"sub": ANDROID_APP_ID},
            ) as verify_app_check,
            patch(
                "api.auth.firebase_auth.verify_id_token",
                return_value=VERIFIED_CLAIMS,
            ) as verify_auth,
            patch(
                "api.routes.users.get_user_profile",
                new=AsyncMock(return_value=PROFILE),
            ),
        ):
            response = self.client.get(
                "/users/me",
                headers={
                    "Authorization": "Bearer valid-auth-token",
                    "X-Firebase-AppCheck": "valid-app-check-token",
                },
            )

        self.assertEqual(response.status_code, 200)
        verify_app_check.assert_called_once_with(
            "valid-app-check-token",
            app=firebase_app,
        )
        verify_auth.assert_called_once_with(
            "valid-auth-token",
            app=firebase_app,
            check_revoked=secured_config.firebase_check_revoked_tokens,
        )

    def test_token_from_unapproved_project_app_is_rejected(self) -> None:
        secured_config = self._secured_config(allowed_app_ids=(ANDROID_APP_ID,))
        with (
            patch.object(auth_module, "config", secured_config),
            patch("api.auth.get_firebase_app", return_value=object()),
            patch(
                "api.auth.app_check.verify_token",
                return_value={"sub": OTHER_APP_ID},
            ),
            patch("api.auth.firebase_auth.verify_id_token") as verify_auth,
        ):
            response = self.client.get(
                "/users/me",
                headers={
                    "Authorization": "Bearer valid-auth-token",
                    "X-Firebase-AppCheck": "wrong-app-token",
                },
            )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["detail"], "App verification failed")
        verify_auth.assert_not_called()

    def test_enforcement_disabled_preserves_local_rest_testing(self) -> None:
        development_config = replace(
            auth_module.config,
            firebase_require_app_check=False,
            firebase_app_check_allowed_app_ids=(ANDROID_APP_ID,),
        )
        firebase_app = object()
        with (
            patch.object(auth_module, "config", development_config),
            patch("api.auth.get_firebase_app", return_value=firebase_app),
            patch("api.auth.app_check.verify_token") as verify_app_check,
            patch(
                "api.auth.firebase_auth.verify_id_token",
                return_value=VERIFIED_CLAIMS,
            ),
            patch(
                "api.routes.users.get_user_profile",
                new=AsyncMock(return_value=PROFILE),
            ),
        ):
            response = self.client.get(
                "/users/me",
                headers={"Authorization": "Bearer valid-auth-token"},
            )

        self.assertEqual(response.status_code, 200)
        verify_app_check.assert_not_called()


if __name__ == "__main__":
    unittest.main()
