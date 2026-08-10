"""Authenticated account-profile routes for Himikama."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, status

from api.config import config
from api.auth import (
    AuthenticatedUser,
    require_recent_user,
    require_verified_user,
)
from api.firebase import (
    AccountDeletionNotRecoverableError,
    accept_assessment_consent,
    accept_current_policies,
    cancel_account_deletion,
    create_user_profile,
    get_user_profile,
    schedule_account_deletion,
    update_user_profile,
    withdraw_assessment_consent,
)
from api.schemas import (
    AcceptPoliciesRequest,
    AssessmentConsentRequest,
    CreateProfileRequest,
    DeleteAccountRequest,
    UpdateProfileRequest,
)


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/users", tags=["users"])


def _profile_response(
    profile: dict,
    user: AuthenticatedUser,
) -> dict:
    return {
        "success": True,
        "profile": {
            "display_name": profile.get("display_name", ""),
            "email": user.email,
            "email_verified": user.email_verified,
            "account_status": profile.get("account_status", ""),
            "terms_version": profile.get("terms_version", ""),
            "privacy_version": profile.get("privacy_version", ""),
            "terms_current": (
                profile.get("terms_version") == config.current_terms_version
            ),
            "privacy_current": (
                profile.get("privacy_version")
                == config.current_privacy_version
            ),
            "assessment_consent_version": profile.get(
                "assessment_consent_version", ""
            ),
            "assessment_consent_current": (
                profile.get("assessment_consent_version")
                == config.current_assessment_consent_version
            ),
            "assessment_consent_at": profile.get("assessment_consent_at"),
            "deletion_requested_at": profile.get("deletion_requested_at"),
            "deletion_effective_at": profile.get("deletion_effective_at"),
            "created_at": profile.get("created_at"),
            "updated_at": profile.get("updated_at"),
        },
    }


@router.post("/me/profile", status_code=status.HTTP_201_CREATED)
async def create_profile(
    request: CreateProfileRequest,
    user: AuthenticatedUser = Depends(require_verified_user),
) -> dict:
    try:
        profile = await create_user_profile(
            uid=user.uid,
            display_name=request.display_name,
        )
        response = _profile_response(profile, user)
        response["created"] = bool(profile.get("created"))
        return response
    except Exception as exc:
        logger.exception("Could not create user profile: %s", type(exc).__name__)
        raise HTTPException(500, "Could not create the user profile") from None


@router.get("/me")
async def read_profile(
    user: AuthenticatedUser = Depends(require_verified_user),
) -> dict:
    try:
        profile = await get_user_profile(uid=user.uid)
        if profile is None:
            raise HTTPException(404, "User profile not found")
        return _profile_response(profile, user)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Could not read user profile: %s", type(exc).__name__)
        raise HTTPException(500, "Could not read the user profile") from None


@router.patch("/me/profile")
async def update_profile(
    request: UpdateProfileRequest,
    user: AuthenticatedUser = Depends(require_verified_user),
) -> dict:
    try:
        profile = await update_user_profile(
            uid=user.uid,
            display_name=request.display_name,
        )
        return _profile_response(profile, user)
    except FileNotFoundError:
        raise HTTPException(404, "User profile not found") from None
    except PermissionError:
        raise HTTPException(403, "This account is not active") from None
    except Exception as exc:
        logger.exception("Could not update user profile: %s", type(exc).__name__)
        raise HTTPException(500, "Could not update the user profile") from None


@router.post("/me/policies")
async def accept_policies(
    request: AcceptPoliciesRequest,
    user: AuthenticatedUser = Depends(require_verified_user),
) -> dict:
    del request  # Literal-true fields are validated by Pydantic.
    try:
        profile = await accept_current_policies(uid=user.uid)
        return _profile_response(profile, user)
    except FileNotFoundError:
        raise HTTPException(404, "User profile not found") from None
    except PermissionError:
        raise HTTPException(403, "This account is not active") from None
    except Exception as exc:
        logger.exception("Could not record policy consent: %s", type(exc).__name__)
        raise HTTPException(500, "Could not record policy consent") from None


@router.post("/me/assessment-consent")
async def accept_assessment_processing(
    request: AssessmentConsentRequest,
    user: AuthenticatedUser = Depends(require_verified_user),
) -> dict:
    del request
    try:
        profile = await accept_assessment_consent(uid=user.uid)
        return _profile_response(profile, user)
    except FileNotFoundError:
        raise HTTPException(404, "User profile not found") from None
    except PermissionError:
        raise HTTPException(403, "This account is not active") from None
    except Exception as exc:
        logger.exception(
            "Could not record assessment consent: %s", type(exc).__name__
        )
        raise HTTPException(500, "Could not record assessment consent") from None


@router.delete("/me/assessment-consent")
async def withdraw_assessment_processing(
    user: AuthenticatedUser = Depends(require_verified_user),
) -> dict:
    try:
        profile = await withdraw_assessment_consent(uid=user.uid)
        return _profile_response(profile, user)
    except FileNotFoundError:
        raise HTTPException(404, "User profile not found") from None
    except PermissionError:
        raise HTTPException(403, "This account is not active") from None
    except Exception as exc:
        logger.exception(
            "Could not withdraw assessment consent: %s", type(exc).__name__
        )
        raise HTTPException(500, "Could not withdraw assessment consent") from None


@router.delete("/me", status_code=status.HTTP_202_ACCEPTED)
async def delete_account(
    request: DeleteAccountRequest,
    user: AuthenticatedUser = Depends(require_recent_user),
) -> dict:
    del request  # The exact confirmation string is validated by Pydantic.
    try:
        profile = await schedule_account_deletion(uid=user.uid)
        response = _profile_response(profile, user)
        response["recovery_days"] = config.account_deletion_recovery_days
        return response
    except FileNotFoundError:
        raise HTTPException(404, "User profile not found") from None
    except PermissionError:
        raise HTTPException(409, "Account deletion cannot be scheduled") from None
    except Exception as exc:
        logger.exception("Account deletion scheduling failed: %s", type(exc).__name__)
        raise HTTPException(500, "Account deletion could not be scheduled") from None


@router.post("/me/deletion/cancel")
async def recover_scheduled_account(
    user: AuthenticatedUser = Depends(require_verified_user),
) -> dict:
    try:
        profile = await cancel_account_deletion(uid=user.uid)
        return _profile_response(profile, user)
    except FileNotFoundError:
        raise HTTPException(404, "User profile not found") from None
    except AccountDeletionNotRecoverableError as exc:
        raise HTTPException(409, str(exc)) from None
    except Exception as exc:
        logger.exception("Account recovery failed: %s", type(exc).__name__)
        raise HTTPException(500, "Account recovery could not be completed") from None
