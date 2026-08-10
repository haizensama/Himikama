"""Firebase Authentication dependencies for protected FastAPI routes."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from firebase_admin import app_check, auth as firebase_auth

from api.config import config
from api.firebase import get_firebase_app, get_user_profile


logger = logging.getLogger(__name__)

_bearer = HTTPBearer(
    auto_error=False,
    scheme_name="Firebase ID token",
    description="Firebase ID token issued after signing in",
)


@dataclass(frozen=True)
class AuthenticatedUser:
    uid: str
    email: str | None
    email_verified: bool
    auth_time: int | None
    issued_at: int | None
    provider: str | None
    claims: dict[str, Any]


def _authentication_error(detail: str = "Authentication required") -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


async def _verify_app_check_if_required(request: Request) -> None:
    if not config.firebase_require_app_check:
        return

    token = request.headers.get("X-Firebase-AppCheck", "").strip()
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="App verification required",
        )

    try:
        firebase_app = await asyncio.to_thread(get_firebase_app)
        claims = await asyncio.to_thread(
            app_check.verify_token,
            token,
            app=firebase_app,
        )
        if not isinstance(claims, Mapping):
            raise ValueError("App Check returned invalid claims")

        app_id = str(claims.get("sub") or "").strip()
        allowed_app_ids = config.firebase_app_check_allowed_app_ids
        if allowed_app_ids and app_id not in allowed_app_ids:
            raise ValueError("App Check token belongs to an unapproved app")

        # Retain only the non-secret Firebase App ID for request-scoped audit
        # and diagnostics. Never retain or log the App Check token itself.
        request.state.firebase_app_id = app_id or None
    except Exception as exc:
        logger.warning("App Check rejected a request: %s", type(exc).__name__)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="App verification failed",
        ) from None


async def require_verified_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> AuthenticatedUser:
    """Verify a Firebase ID token and return its trusted identity claims."""
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise _authentication_error()

    token = credentials.credentials.strip()
    if not token:
        raise _authentication_error()

    await _verify_app_check_if_required(request)

    try:
        firebase_app = await asyncio.to_thread(get_firebase_app)
        decoded = await asyncio.to_thread(
            firebase_auth.verify_id_token,
            token,
            app=firebase_app,
            check_revoked=config.firebase_check_revoked_tokens,
        )
    except Exception as exc:
        # Never log the token or return the underlying Firebase exception.
        logger.warning("Firebase token rejected: %s", type(exc).__name__)
        raise _authentication_error("Invalid or expired authentication") from None

    uid = str(decoded.get("uid") or decoded.get("sub") or "").strip()
    if not uid or len(uid) > 128 or "/" in uid:
        raise _authentication_error("Invalid authentication identity")

    firebase_claims = decoded.get("firebase", {})
    provider = (
        str(firebase_claims.get("sign_in_provider") or "").strip() or None
        if isinstance(firebase_claims, dict)
        else None
    )
    if provider == "anonymous":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="A registered account is required",
        )

    email = str(decoded.get("email") or "").strip() or None
    email_verified = decoded.get("email_verified") is True
    if config.firebase_require_verified_email and (
        not email or not email_verified
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Please verify your email address before using Himikama",
        )

    return AuthenticatedUser(
        uid=uid,
        email=email,
        email_verified=email_verified,
        auth_time=_optional_int(decoded.get("auth_time")),
        issued_at=_optional_int(decoded.get("iat")),
        provider=provider,
        claims=decoded,
    )


async def require_recent_user(
    user: AuthenticatedUser = Depends(require_verified_user),
) -> AuthenticatedUser:
    """Require a recent sign-in for destructive account operations."""
    if user.auth_time is None:
        raise _authentication_error("Please sign in again to continue")

    age = int(time.time()) - user.auth_time
    if age < 0 or age > config.recent_auth_max_age_seconds:
        raise _authentication_error("Please sign in again to continue")
    return user


async def require_active_user(
    user: AuthenticatedUser = Depends(require_verified_user),
) -> AuthenticatedUser:
    """Require a completed, active Himikama profile and current consent."""
    try:
        profile = await get_user_profile(uid=user.uid)
    except Exception as exc:
        logger.error("Could not verify account profile: %s", type(exc).__name__)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Account verification is temporarily unavailable",
        ) from None

    if profile is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Complete account setup before using Himikama",
        )
    if profile.get("account_status") != "active":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This account is not active",
        )
    if (
        profile.get("terms_version") != config.current_terms_version
        or profile.get("privacy_version") != config.current_privacy_version
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Review and accept the current policies before continuing",
        )
    return user


async def require_assessment_consent_user(
    user: AuthenticatedUser = Depends(require_active_user),
) -> AuthenticatedUser:
    """Require current, explicit consent before processing legal narratives."""
    try:
        profile = await get_user_profile(uid=user.uid)
    except Exception as exc:
        logger.error("Could not verify assessment consent: %s", type(exc).__name__)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Consent verification is temporarily unavailable",
        ) from None

    if profile is None or (
        profile.get("assessment_consent_version")
        != config.current_assessment_consent_version
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Review and accept assessment data processing before "
                "starting or retrying an assessment"
            ),
        )
    return user


async def delete_firebase_auth_user(uid: str) -> None:
    """Delete a Firebase Authentication user after their data is removed."""
    firebase_app = await asyncio.to_thread(get_firebase_app)
    await asyncio.to_thread(firebase_auth.delete_user, uid, app=firebase_app)


async def delete_firebase_auth_user_if_exists(uid: str) -> None:
    """Idempotently delete an Auth user for a retryable deletion worker."""
    try:
        await delete_firebase_auth_user(uid)
    except firebase_auth.UserNotFoundError:
        return


def _optional_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None
