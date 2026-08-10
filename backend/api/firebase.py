"""Firebase Admin and Firestore persistence for Himikama.

All functions in this module are backend-only. The Flutter application must
never receive Firebase Admin credentials or call these functions directly.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import firebase_admin
from firebase_admin import credentials, firestore

from api.config import config


logger = logging.getLogger(__name__)

_firebase_lock = threading.RLock()
_firebase_app = None
_firestore_client = None


TERMINAL_ATTEMPT_STATUSES = {
    "complete",
    "time_barred",
    "not_state_actor",
    "failed",
}


class AttemptConflictError(RuntimeError):
    """The supplied idempotency key belongs to a different intake."""


class AttemptNotRetryableError(RuntimeError):
    """The owned attempt is not currently eligible for retry."""


class AccountDeletionNotRecoverableError(RuntimeError):
    """The seven-day account recovery window has ended."""


@dataclass(frozen=True)
class AttemptSubmission:
    attempt_id: str
    status: str
    created: bool


@dataclass(frozen=True)
class ClaimedAnalysisJob:
    attempt_id: str
    owner_uid: str
    lease_token: str
    generation: int


@dataclass(frozen=True)
class ClaimedAccountDeletion:
    owner_uid: str
    lease_token: str


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _validate_uid(uid: str) -> str:
    normalized = str(uid or "").strip()
    if not normalized or len(normalized) > 128 or "/" in normalized:
        raise ValueError("Invalid Firebase UID")
    return normalized


def _validate_attempt_id(attempt_id: str | uuid.UUID) -> str:
    try:
        return str(uuid.UUID(str(attempt_id)))
    except (TypeError, ValueError, AttributeError) as exc:
        raise ValueError("Invalid attempt ID") from exc


def get_firebase_app():
    """Initialize Firebase Admin once, using a local key or ADC."""
    global _firebase_app

    if _firebase_app is not None:
        return _firebase_app

    with _firebase_lock:
        if _firebase_app is not None:
            return _firebase_app

        try:
            _firebase_app = firebase_admin.get_app()
            return _firebase_app
        except ValueError:
            pass

        options: dict[str, str] | None = None
        if config.firebase_project_id:
            options = {"projectId": config.firebase_project_id}

        credential = None
        if config.firebase_service_account_path:
            service_account_path = Path(config.firebase_service_account_path)
            if not service_account_path.is_file():
                raise RuntimeError(
                    "Configured Firebase service account file was not found"
                )
            credential = credentials.Certificate(str(service_account_path))

        if credential is None:
            # Production deployments should prefer Application Default
            # Credentials / workload identity instead of a JSON key file.
            _firebase_app = firebase_admin.initialize_app(options=options)
        else:
            _firebase_app = firebase_admin.initialize_app(
                credential,
                options=options,
            )

        return _firebase_app


def get_firestore_client():
    global _firestore_client
    if _firestore_client is None:
        with _firebase_lock:
            if _firestore_client is None:
                _firestore_client = firestore.client(app=get_firebase_app())
    return _firestore_client


def _user_ref(uid: str):
    return (
        get_firestore_client()
        .collection(config.firestore_users_collection)
        .document(_validate_uid(uid))
    )


def _attempt_ref(uid: str, attempt_id: str | uuid.UUID):
    return (
        _user_ref(uid)
        .collection("attempts")
        .document(_validate_attempt_id(attempt_id))
    )


def _job_ref(attempt_id: str | uuid.UUID):
    return (
        get_firestore_client()
        .collection(config.firestore_jobs_collection)
        .document(_validate_attempt_id(attempt_id))
    )


def _owned_attempt_sync(uid: str, attempt_id: str | uuid.UUID) -> dict[str, Any]:
    normalized_uid = _validate_uid(uid)
    snapshot = _attempt_ref(normalized_uid, attempt_id).get()
    if not snapshot.exists:
        raise FileNotFoundError("Attempt not found")

    data = snapshot.to_dict() or {}
    if data.get("owner_uid") != normalized_uid:
        logger.error("Stored attempt failed its ownership integrity check")
        raise FileNotFoundError("Attempt not found")
    return data


def _assert_active_profile_sync(uid: str) -> None:
    """Recheck account state at the storage boundary before a new write."""
    normalized_uid = _validate_uid(uid)
    snapshot = _user_ref(normalized_uid).get()
    if not snapshot.exists:
        raise PermissionError("Active user profile required")
    profile = snapshot.to_dict() or {}
    if (
        profile.get("owner_uid") != normalized_uid
        or profile.get("account_status") != "active"
        or profile.get("terms_version") != config.current_terms_version
        or profile.get("privacy_version") != config.current_privacy_version
        or profile.get("assessment_consent_version")
        != config.current_assessment_consent_version
    ):
        raise PermissionError("Active user profile required")


# ---------------------------------------------------------------------------
# User profiles
# ---------------------------------------------------------------------------

async def create_user_profile(*, uid: str, display_name: str) -> dict[str, Any]:
    return await asyncio.to_thread(
        _create_user_profile_sync,
        uid,
        display_name,
    )


def _create_user_profile_sync(uid: str, display_name: str) -> dict[str, Any]:
    normalized_uid = _validate_uid(uid)
    reference = _user_ref(normalized_uid)
    snapshot = reference.get()
    if snapshot.exists:
        existing = snapshot.to_dict() or {}
        if existing.get("owner_uid") != normalized_uid:
            raise RuntimeError("User profile ownership check failed")
        existing["created"] = False
        return existing

    now = _utc_now()
    profile = {
        "owner_uid": normalized_uid,
        "display_name": display_name,
        "account_status": "active",
        "terms_version": config.current_terms_version,
        "privacy_version": config.current_privacy_version,
        "terms_accepted_at": now,
        "privacy_accepted_at": now,
        "assessment_consent_version": "",
        "assessment_consent_at": None,
        "created_at": now,
        "updated_at": now,
    }
    reference.create(profile)
    return {**profile, "created": True}


async def get_user_profile(*, uid: str) -> dict[str, Any] | None:
    return await asyncio.to_thread(_get_user_profile_sync, uid)


def _get_user_profile_sync(uid: str) -> dict[str, Any] | None:
    normalized_uid = _validate_uid(uid)
    snapshot = _user_ref(normalized_uid).get()
    if not snapshot.exists:
        return None
    data = snapshot.to_dict() or {}
    if data.get("owner_uid") != normalized_uid:
        raise RuntimeError("User profile ownership check failed")
    return data


async def update_user_profile(*, uid: str, display_name: str) -> dict[str, Any]:
    return await asyncio.to_thread(
        _update_user_profile_sync,
        uid,
        display_name,
    )


def _update_user_profile_sync(uid: str, display_name: str) -> dict[str, Any]:
    normalized_uid = _validate_uid(uid)
    reference = _user_ref(normalized_uid)
    snapshot = reference.get()
    if not snapshot.exists:
        raise FileNotFoundError("User profile not found")
    existing = snapshot.to_dict() or {}
    if existing.get("owner_uid") != normalized_uid:
        raise RuntimeError("User profile ownership check failed")
    if existing.get("account_status") != "active":
        raise PermissionError("User profile is not active")

    updated_at = _utc_now()
    reference.update({"display_name": display_name, "updated_at": updated_at})
    existing.update({"display_name": display_name, "updated_at": updated_at})
    return existing


async def accept_current_policies(*, uid: str) -> dict[str, Any]:
    return await asyncio.to_thread(_accept_current_policies_sync, uid)


def _accept_current_policies_sync(uid: str) -> dict[str, Any]:
    normalized_uid = _validate_uid(uid)
    reference = _user_ref(normalized_uid)
    snapshot = reference.get()
    if not snapshot.exists:
        raise FileNotFoundError("User profile not found")
    existing = snapshot.to_dict() or {}
    if existing.get("owner_uid") != normalized_uid:
        raise RuntimeError("User profile ownership check failed")
    if existing.get("account_status") != "active":
        raise PermissionError("User profile is not active")

    now = _utc_now()
    update = {
        "terms_version": config.current_terms_version,
        "privacy_version": config.current_privacy_version,
        "terms_accepted_at": now,
        "privacy_accepted_at": now,
        "updated_at": now,
    }
    reference.update(update)
    existing.update(update)
    return existing


async def accept_assessment_consent(*, uid: str) -> dict[str, Any]:
    return await asyncio.to_thread(_accept_assessment_consent_sync, uid)


def _accept_assessment_consent_sync(uid: str) -> dict[str, Any]:
    normalized_uid = _validate_uid(uid)
    reference = _user_ref(normalized_uid)
    snapshot = reference.get()
    if not snapshot.exists:
        raise FileNotFoundError("User profile not found")
    existing = snapshot.to_dict() or {}
    if existing.get("owner_uid") != normalized_uid:
        raise RuntimeError("User profile ownership check failed")
    if existing.get("account_status") != "active":
        raise PermissionError("User profile is not active")

    now = _utc_now()
    update = {
        "assessment_consent_version": (
            config.current_assessment_consent_version
        ),
        "assessment_consent_at": now,
        "assessment_consent_withdrawn_at": None,
        "updated_at": now,
    }
    reference.update(update)
    existing.update(update)
    return existing


async def withdraw_assessment_consent(*, uid: str) -> dict[str, Any]:
    return await asyncio.to_thread(_withdraw_assessment_consent_sync, uid)


def _withdraw_assessment_consent_sync(uid: str) -> dict[str, Any]:
    normalized_uid = _validate_uid(uid)
    reference = _user_ref(normalized_uid)
    snapshot = reference.get()
    if not snapshot.exists:
        raise FileNotFoundError("User profile not found")
    existing = snapshot.to_dict() or {}
    if existing.get("owner_uid") != normalized_uid:
        raise RuntimeError("User profile ownership check failed")
    if existing.get("account_status") != "active":
        raise PermissionError("User profile is not active")

    now = _utc_now()
    update = {
        "assessment_consent_version": "",
        "assessment_consent_at": None,
        "assessment_consent_withdrawn_at": now,
        "updated_at": now,
    }
    reference.update(update)
    existing.update(update)
    return existing


async def delete_user_data(*, uid: str) -> None:
    await asyncio.to_thread(_delete_user_data_sync, uid)


def _delete_user_data_sync(uid: str) -> None:
    normalized_uid = _validate_uid(uid)
    user_reference = _user_ref(normalized_uid)
    profile_snapshot = user_reference.get()
    if profile_snapshot.exists:
        profile = profile_snapshot.to_dict() or {}
        if profile.get("owner_uid") != normalized_uid:
            raise RuntimeError("User profile ownership check failed")
    _erase_user_assessment_data_sync(normalized_uid)
    user_reference.delete()


async def erase_user_assessment_data(*, uid: str) -> None:
    await asyncio.to_thread(_erase_user_assessment_data_sync, uid)


def _erase_user_assessment_data_sync(uid: str) -> None:
    normalized_uid = _validate_uid(uid)
    user_reference = _user_ref(normalized_uid)
    attempts = user_reference.collection("attempts")

    # Delete jobs first. A leased worker then fails its conditional result
    # write and cannot recreate an assessment the user asked to erase.
    jobs_query = (
        get_firestore_client()
        .collection(config.firestore_jobs_collection)
        .where("owner_uid", "==", normalized_uid)
    )
    while True:
        job_documents = list(jobs_query.limit(100).stream())
        if not job_documents:
            break
        batch = get_firestore_client().batch()
        for document in job_documents:
            batch.delete(document.reference)
        batch.commit()

    # Firestore does not cascade-delete subcollections.
    while True:
        documents = list(attempts.limit(100).stream())
        if not documents:
            break
        batch = get_firestore_client().batch()
        for document in documents:
            batch.delete(document.reference)
        batch.commit()


def _cancel_user_analysis_jobs_sync(uid: str) -> None:
    """Stop queued/leased work while retaining assessments for recovery."""
    normalized_uid = _validate_uid(uid)
    jobs_query = (
        get_firestore_client()
        .collection(config.firestore_jobs_collection)
        .where("owner_uid", "==", normalized_uid)
    )
    while True:
        documents = list(jobs_query.limit(100).stream())
        if not documents:
            break
        batch = get_firestore_client().batch()
        for document in documents:
            batch.delete(document.reference)
        batch.commit()

    attempts = _user_ref(normalized_uid).collection("attempts")
    while True:
        processing = list(
            attempts.where("status", "==", "processing").limit(100).stream()
        )
        if not processing:
            break
        batch = get_firestore_client().batch()
        now = _utc_now()
        for document in processing:
            data = document.to_dict() or {}
            if data.get("owner_uid") != normalized_uid:
                raise RuntimeError(
                    "Attempt ownership check failed during account deletion"
                )
            batch.update(
                document.reference,
                {
                    "status": "failed",
                    "error_code": "account_deletion_scheduled",
                    "updated_at": now,
                },
            )
        batch.commit()


async def schedule_account_deletion(*, uid: str) -> dict[str, Any]:
    profile = await asyncio.to_thread(_schedule_account_deletion_sync, uid)
    await asyncio.to_thread(_cancel_user_analysis_jobs_sync, uid)
    return await asyncio.to_thread(_mark_account_deletion_ready_sync, uid)


def _schedule_account_deletion_sync(uid: str) -> dict[str, Any]:
    normalized_uid = _validate_uid(uid)
    reference = _user_ref(normalized_uid)
    snapshot = reference.get()
    if not snapshot.exists:
        raise FileNotFoundError("User profile not found")
    profile = snapshot.to_dict() or {}
    if profile.get("owner_uid") != normalized_uid:
        raise RuntimeError("User profile ownership check failed")
    if profile.get("account_status") == "deletion_scheduled":
        return profile
    if profile.get("account_status") != "active":
        raise PermissionError("Account deletion cannot be scheduled")

    now = _utc_now()
    update = {
        "account_status": "deletion_scheduled",
        "deletion_requested_at": now,
        "deletion_effective_at": now
        + timedelta(days=config.account_deletion_recovery_days),
        "deletion_lease_token": None,
        "deletion_lease_owner": None,
        "deletion_lease_expires_at": None,
        "deletion_cleanup_completed_at": None,
        "updated_at": now,
    }
    reference.update(update)
    profile.update(update)
    return profile


def _mark_account_deletion_ready_sync(uid: str) -> dict[str, Any]:
    normalized_uid = _validate_uid(uid)
    reference = _user_ref(normalized_uid)
    snapshot = reference.get()
    if not snapshot.exists:
        raise FileNotFoundError("User profile not found")
    profile = snapshot.to_dict() or {}
    if (
        profile.get("owner_uid") != normalized_uid
        or profile.get("account_status") != "deletion_scheduled"
    ):
        raise PermissionError("Account deletion is not scheduled")
    now = _utc_now()
    update = {
        "deletion_cleanup_completed_at": now,
        "updated_at": now,
    }
    reference.update(update)
    profile.update(update)
    return profile


async def cancel_account_deletion(*, uid: str) -> dict[str, Any]:
    # Re-run the idempotent job cancellation before restoring an active
    # account. This also repairs a schedule request interrupted mid-cleanup.
    profile = await get_user_profile(uid=uid)
    if profile is None:
        raise FileNotFoundError("User profile not found")
    if profile.get("account_status") == "deletion_scheduled":
        await asyncio.to_thread(_cancel_user_analysis_jobs_sync, uid)
        await asyncio.to_thread(_mark_account_deletion_ready_sync, uid)
    return await asyncio.to_thread(_cancel_account_deletion_sync, uid)


@firestore.transactional
def _cancel_account_deletion_transaction(
    transaction,
    *,
    uid: str,
) -> dict[str, Any]:
    reference = _user_ref(uid)
    snapshot = reference.get(transaction=transaction)
    if not snapshot.exists:
        raise FileNotFoundError("User profile not found")
    profile = snapshot.to_dict() or {}
    if profile.get("owner_uid") != uid:
        raise RuntimeError("User profile ownership check failed")
    if profile.get("account_status") != "deletion_scheduled":
        raise AccountDeletionNotRecoverableError(
            "Account deletion is already being completed"
        )
    if _as_utc_datetime(profile.get("deletion_cleanup_completed_at")) is None:
        raise AccountDeletionNotRecoverableError(
            "Account deletion is still being prepared; try again shortly"
        )
    effective_at = _as_utc_datetime(profile.get("deletion_effective_at"))
    if effective_at is None or _utc_now() >= effective_at:
        raise AccountDeletionNotRecoverableError(
            "The account recovery window has ended"
        )

    now = _utc_now()
    update = {
        "account_status": "active",
        "deletion_requested_at": None,
        "deletion_effective_at": None,
        "deletion_lease_token": None,
        "deletion_lease_owner": None,
        "deletion_lease_expires_at": None,
        "deletion_cleanup_completed_at": None,
        "updated_at": now,
    }
    transaction.update(reference, update)
    profile.update(update)
    return profile


def _cancel_account_deletion_sync(uid: str) -> dict[str, Any]:
    normalized_uid = _validate_uid(uid)
    transaction = get_firestore_client().transaction()
    return _cancel_account_deletion_transaction(
        transaction,
        uid=normalized_uid,
    )


def _deletion_is_claimable(profile: dict[str, Any], *, now: datetime) -> bool:
    effective_at = _as_utc_datetime(profile.get("deletion_effective_at"))
    if effective_at is None or effective_at > now:
        return False
    status = str(profile.get("account_status") or "")
    if status == "deletion_scheduled":
        return True
    if status == "deletion_processing":
        lease_expires = _as_utc_datetime(
            profile.get("deletion_lease_expires_at")
        )
        return lease_expires is None or lease_expires <= now
    return False


def _find_due_account_deletion_uids_sync() -> list[str]:
    collection = get_firestore_client().collection(
        config.firestore_users_collection
    )
    snapshots: list[Any] = []
    for account_status in ("deletion_scheduled", "deletion_processing"):
        snapshots.extend(
            collection.where("account_status", "==", account_status)
            .limit(config.account_deletion_scan_limit)
            .stream()
        )

    now = _utc_now()
    due: list[tuple[datetime, str]] = []
    seen: set[str] = set()
    for snapshot in snapshots:
        profile = snapshot.to_dict() or {}
        try:
            uid = _validate_uid(profile.get("owner_uid"))
        except ValueError:
            logger.error("Ignored malformed account deletion profile")
            continue
        if uid in seen or not _deletion_is_claimable(profile, now=now):
            continue
        seen.add(uid)
        effective_at = _as_utc_datetime(profile.get("deletion_effective_at"))
        due.append((effective_at or now, uid))
    due.sort(key=lambda item: item[0])
    return [uid for _, uid in due]


@firestore.transactional
def _claim_account_deletion_transaction(
    transaction,
    *,
    uid: str,
    worker_id: str,
) -> ClaimedAccountDeletion | None:
    reference = _user_ref(uid)
    snapshot = reference.get(transaction=transaction)
    if not snapshot.exists:
        return None
    profile = snapshot.to_dict() or {}
    if profile.get("owner_uid") != uid:
        logger.error("Account deletion ownership integrity check failed")
        return None
    now = _utc_now()
    if not _deletion_is_claimable(profile, now=now):
        return None

    token = str(uuid.uuid4())
    transaction.update(
        reference,
        {
            "account_status": "deletion_processing",
            "deletion_lease_token": token,
            "deletion_lease_owner": worker_id,
            "deletion_lease_expires_at": now
            + timedelta(seconds=config.account_deletion_lease_seconds),
            "updated_at": now,
        },
    )
    return ClaimedAccountDeletion(owner_uid=uid, lease_token=token)


async def claim_due_account_deletion(
    *, worker_id: str
) -> ClaimedAccountDeletion | None:
    return await asyncio.to_thread(_claim_due_account_deletion_sync, worker_id)


def _claim_due_account_deletion_sync(
    worker_id: str,
) -> ClaimedAccountDeletion | None:
    normalized_worker = str(worker_id or "").strip()
    if not normalized_worker or len(normalized_worker) > 100:
        raise ValueError("Invalid deletion worker ID")
    for uid in _find_due_account_deletion_uids_sync():
        transaction = get_firestore_client().transaction()
        claim = _claim_account_deletion_transaction(
            transaction,
            uid=uid,
            worker_id=normalized_worker,
        )
        if claim is not None:
            return claim
    return None


def _deletion_claim_is_current(
    profile: dict[str, Any], claim: ClaimedAccountDeletion
) -> bool:
    return (
        profile.get("owner_uid") == claim.owner_uid
        and profile.get("account_status") == "deletion_processing"
        and profile.get("deletion_lease_token") == claim.lease_token
    )


@firestore.transactional
def _release_account_deletion_transaction(
    transaction,
    *,
    claim: ClaimedAccountDeletion,
) -> bool:
    reference = _user_ref(claim.owner_uid)
    snapshot = reference.get(transaction=transaction)
    if not snapshot.exists:
        return False
    profile = snapshot.to_dict() or {}
    if not _deletion_claim_is_current(profile, claim):
        return False
    transaction.update(
        reference,
        {
            "account_status": "deletion_scheduled",
            "deletion_lease_token": None,
            "deletion_lease_owner": None,
            "deletion_lease_expires_at": None,
            "updated_at": _utc_now(),
        },
    )
    return True


async def release_account_deletion(
    *, claim: ClaimedAccountDeletion
) -> bool:
    return await asyncio.to_thread(_release_account_deletion_sync, claim)


def _release_account_deletion_sync(claim: ClaimedAccountDeletion) -> bool:
    transaction = get_firestore_client().transaction()
    return _release_account_deletion_transaction(transaction, claim=claim)


@firestore.transactional
def _complete_account_deletion_transaction(
    transaction,
    *,
    claim: ClaimedAccountDeletion,
) -> bool:
    reference = _user_ref(claim.owner_uid)
    snapshot = reference.get(transaction=transaction)
    if not snapshot.exists:
        return True
    profile = snapshot.to_dict() or {}
    if not _deletion_claim_is_current(profile, claim):
        return False
    transaction.delete(reference)
    return True


async def complete_account_deletion(
    *, claim: ClaimedAccountDeletion
) -> bool:
    return await asyncio.to_thread(_complete_account_deletion_sync, claim)


def _complete_account_deletion_sync(claim: ClaimedAccountDeletion) -> bool:
    transaction = get_firestore_client().transaction()
    return _complete_account_deletion_transaction(transaction, claim=claim)


# ---------------------------------------------------------------------------
# Analysis attempts
# ---------------------------------------------------------------------------

async def create_attempt(
    *,
    user_id: str,
    intake_object: dict[str, Any],
    attempt_id: str | uuid.UUID | None = None,
) -> AttemptSubmission:
    return await asyncio.to_thread(
        _create_attempt_sync,
        user_id,
        intake_object,
        attempt_id,
    )


def _intake_fingerprint(intake_object: dict[str, Any]) -> str:
    canonical = json.dumps(
        intake_object,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _new_job_document(
    *,
    uid: str,
    attempt_id: str,
    now: datetime,
    generation: int = 1,
    retry_count: int = 0,
) -> dict[str, Any]:
    """Create a durable job containing identifiers and scheduling data only."""
    return {
        "attempt_id": attempt_id,
        "owner_uid": uid,
        "status": "queued",
        "generation": generation,
        "retry_count": retry_count,
        "available_at": now,
        "lease_token": None,
        "lease_owner": None,
        "lease_expires_at": None,
        "created_at": now,
        "updated_at": now,
    }


def _new_attempt_document(
    *,
    uid: str,
    attempt_id: str,
    intake_object: dict[str, Any],
    now: datetime,
) -> dict[str, Any]:
    return {
        "attempt_id": attempt_id,
        "owner_uid": uid,
        "user_narrative": intake_object.get("user_narrative", ""),
        "intake_object": intake_object,
        "intake_fingerprint": _intake_fingerprint(intake_object),
        "status": "processing",
        "created_at": now,
        "updated_at": now,
    }


@firestore.transactional
def _create_attempt_transaction(
    transaction,
    *,
    uid: str,
    attempt_id: str,
    intake_object: dict[str, Any],
) -> AttemptSubmission:
    user_reference = _user_ref(uid)
    attempt_reference = _attempt_ref(uid, attempt_id)
    job_reference = _job_ref(attempt_id)

    profile_snapshot = user_reference.get(transaction=transaction)
    attempt_snapshot = attempt_reference.get(transaction=transaction)
    job_snapshot = job_reference.get(transaction=transaction)

    if not profile_snapshot.exists:
        raise PermissionError("Active user profile required")
    profile = profile_snapshot.to_dict() or {}
    if (
        profile.get("owner_uid") != uid
        or profile.get("account_status") != "active"
        or profile.get("terms_version") != config.current_terms_version
        or profile.get("privacy_version") != config.current_privacy_version
        or profile.get("assessment_consent_version")
        != config.current_assessment_consent_version
    ):
        raise PermissionError("Active user profile required")

    requested_fingerprint = _intake_fingerprint(intake_object)
    if attempt_snapshot.exists:
        existing = attempt_snapshot.to_dict() or {}
        if existing.get("owner_uid") != uid:
            raise AttemptConflictError("Attempt ID is already in use")
        if existing.get("intake_fingerprint") != requested_fingerprint:
            raise AttemptConflictError(
                "Attempt ID cannot be reused with different intake data"
            )

        existing_status = str(existing.get("status") or "processing")
        if existing_status == "processing" and not job_snapshot.exists:
            transaction.create(
                job_reference,
                _new_job_document(
                    uid=uid,
                    attempt_id=attempt_id,
                    now=_utc_now(),
                ),
            )
        return AttemptSubmission(
            attempt_id=attempt_id,
            status=existing_status,
            created=False,
        )

    if job_snapshot.exists:
        logger.error("Orphaned analysis job blocked attempt creation")
        raise AttemptConflictError("Attempt ID is already in use")

    now = _utc_now()
    transaction.create(
        attempt_reference,
        _new_attempt_document(
            uid=uid,
            attempt_id=attempt_id,
            intake_object=intake_object,
            now=now,
        ),
    )
    transaction.create(
        job_reference,
        _new_job_document(uid=uid, attempt_id=attempt_id, now=now),
    )
    return AttemptSubmission(
        attempt_id=attempt_id,
        status="processing",
        created=True,
    )


def _create_attempt_sync(
    uid: str,
    intake_object: dict[str, Any],
    attempt_id: str | uuid.UUID | None,
) -> AttemptSubmission:
    normalized_uid = _validate_uid(uid)
    normalized_attempt_id = _validate_attempt_id(attempt_id or uuid.uuid4())
    transaction = get_firestore_client().transaction()
    submission = _create_attempt_transaction(
        transaction,
        uid=normalized_uid,
        attempt_id=normalized_attempt_id,
        intake_object=intake_object,
    )
    logger.info(
        "Accepted idempotent analysis submission attempt_id=%s created=%s",
        submission.attempt_id,
        submission.created,
    )
    return submission


def _as_utc_datetime(value: Any) -> datetime | None:
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _job_is_claimable(job: dict[str, Any], *, now: datetime) -> bool:
    job_status = str(job.get("status") or "")
    if job_status == "queued":
        available_at = _as_utc_datetime(job.get("available_at"))
        return available_at is None or available_at <= now
    if job_status == "leased":
        lease_expires_at = _as_utc_datetime(job.get("lease_expires_at"))
        return lease_expires_at is None or lease_expires_at <= now
    return False


def _claim_is_current(
    job: dict[str, Any],
    claim: ClaimedAnalysisJob,
) -> bool:
    return (
        job.get("status") == "leased"
        and job.get("owner_uid") == claim.owner_uid
        and job.get("lease_token") == claim.lease_token
        and int(job.get("generation") or 0) == claim.generation
    )


def _find_claimable_job_ids_sync() -> list[str]:
    collection = get_firestore_client().collection(
        config.firestore_jobs_collection
    )
    snapshots: list[Any] = []
    for job_status in ("queued", "leased"):
        snapshots.extend(
            collection.where("status", "==", job_status)
            .limit(config.analysis_job_scan_limit)
            .stream()
        )

    now = _utc_now()
    candidates: list[tuple[datetime, str]] = []
    seen: set[str] = set()
    for snapshot in snapshots:
        data = snapshot.to_dict() or {}
        try:
            attempt_id = _validate_attempt_id(data.get("attempt_id"))
            _validate_uid(data.get("owner_uid"))
        except ValueError:
            logger.error("Ignored malformed durable analysis job")
            continue
        if attempt_id in seen or not _job_is_claimable(data, now=now):
            continue
        seen.add(attempt_id)
        created_at = _as_utc_datetime(data.get("created_at")) or now
        candidates.append((created_at, attempt_id))

    candidates.sort(key=lambda item: item[0])
    return [attempt_id for _, attempt_id in candidates]


@firestore.transactional
def _claim_job_transaction(
    transaction,
    *,
    attempt_id: str,
    worker_id: str,
) -> ClaimedAnalysisJob | None:
    job_reference = _job_ref(attempt_id)
    job_snapshot = job_reference.get(transaction=transaction)
    if not job_snapshot.exists:
        return None
    job = job_snapshot.to_dict() or {}
    now = _utc_now()
    if not _job_is_claimable(job, now=now):
        return None

    owner_uid = _validate_uid(job.get("owner_uid"))
    stored_attempt_id = _validate_attempt_id(job.get("attempt_id"))
    if stored_attempt_id != attempt_id:
        logger.error("Durable job failed its attempt integrity check")
        return None

    attempt_reference = _attempt_ref(owner_uid, attempt_id)
    attempt_snapshot = attempt_reference.get(transaction=transaction)
    if not attempt_snapshot.exists:
        transaction.update(
            job_reference,
            {"status": "failed", "updated_at": now, "error_code": "missing_attempt"},
        )
        return None
    attempt = attempt_snapshot.to_dict() or {}
    if attempt.get("owner_uid") != owner_uid:
        logger.error("Durable job failed its ownership integrity check")
        return None

    attempt_status = str(attempt.get("status") or "")
    if attempt_status in TERMINAL_ATTEMPT_STATUSES:
        transaction.update(
            job_reference,
            {
                "status": "complete" if attempt_status != "failed" else "failed",
                "attempt_status": attempt_status,
                "lease_token": None,
                "lease_owner": None,
                "lease_expires_at": None,
                "updated_at": now,
            },
        )
        return None
    if attempt_status != "processing":
        return None

    lease_token = str(uuid.uuid4())
    generation = max(1, int(job.get("generation") or 1))
    transaction.update(
        job_reference,
        {
            "status": "leased",
            "lease_token": lease_token,
            "lease_owner": worker_id,
            "lease_expires_at": now
            + timedelta(seconds=config.analysis_job_lease_seconds),
            "claimed_at": now,
            "updated_at": now,
        },
    )
    transaction.update(
        attempt_reference,
        {
            "status": "processing",
            "worker_generation": generation,
            "updated_at": now,
        },
    )
    return ClaimedAnalysisJob(
        attempt_id=attempt_id,
        owner_uid=owner_uid,
        lease_token=lease_token,
        generation=generation,
    )


def _claim_next_job_sync(worker_id: str) -> ClaimedAnalysisJob | None:
    normalized_worker_id = str(worker_id or "").strip()
    if not normalized_worker_id or len(normalized_worker_id) > 100:
        raise ValueError("Invalid worker ID")
    for attempt_id in _find_claimable_job_ids_sync():
        transaction = get_firestore_client().transaction()
        claimed = _claim_job_transaction(
            transaction,
            attempt_id=attempt_id,
            worker_id=normalized_worker_id,
        )
        if claimed is not None:
            return claimed
    return None


async def claim_next_analysis_job(*, worker_id: str) -> ClaimedAnalysisJob | None:
    return await asyncio.to_thread(_claim_next_job_sync, worker_id)


async def get_attempt_intake(
    *, user_id: str, attempt_id: str | uuid.UUID
) -> dict[str, Any]:
    attempt = await get_attempt(user_id=user_id, attempt_id=attempt_id)
    if str(attempt.get("status") or "") != "processing":
        raise RuntimeError("Attempt is not processing")
    intake = attempt.get("intake_object")
    if not isinstance(intake, dict):
        raise RuntimeError("Attempt intake is missing")
    return dict(intake)


@firestore.transactional
def _heartbeat_job_transaction(
    transaction,
    *,
    claim: ClaimedAnalysisJob,
) -> bool:
    reference = _job_ref(claim.attempt_id)
    snapshot = reference.get(transaction=transaction)
    if not snapshot.exists:
        return False
    job = snapshot.to_dict() or {}
    if not _claim_is_current(job, claim):
        return False
    now = _utc_now()
    transaction.update(
        reference,
        {
            "lease_expires_at": now
            + timedelta(seconds=config.analysis_job_lease_seconds),
            "updated_at": now,
        },
    )
    return True


async def heartbeat_analysis_job(*, claim: ClaimedAnalysisJob) -> bool:
    return await asyncio.to_thread(_heartbeat_job_sync, claim)


def _heartbeat_job_sync(claim: ClaimedAnalysisJob) -> bool:
    transaction = get_firestore_client().transaction()
    return _heartbeat_job_transaction(transaction, claim=claim)


def _attempt_result_update(result: dict[str, Any]) -> dict[str, Any]:
    final_fields = _extract_final_structured_fields(result)
    return {
        "status": result.get("status"),
        "step_results": result.get("step_results", {}),
        "final_answer": result.get("final_answer", ""),
        "final_answer_with_disclaimer": result.get(
            "final_answer_with_disclaimer", ""
        ),
        **final_fields,
        "confidence": result.get("confidence", {}),
        "confidence_level": result.get("confidence_level", ""),
        "flags": result.get("flags", []),
        "articles_identified": result.get("articles_identified", []),
        "similar_case_ids": result.get("similar_case_ids", []),
        "started_at": result.get("started_at"),
        "completed_at": result.get("completed_at"),
        "updated_at": _utc_now(),
    }


@firestore.transactional
def _complete_claimed_job_transaction(
    transaction,
    *,
    claim: ClaimedAnalysisJob,
    result: dict[str, Any],
) -> bool:
    job_reference = _job_ref(claim.attempt_id)
    attempt_reference = _attempt_ref(claim.owner_uid, claim.attempt_id)
    job_snapshot = job_reference.get(transaction=transaction)
    attempt_snapshot = attempt_reference.get(transaction=transaction)
    if not job_snapshot.exists or not attempt_snapshot.exists:
        return False
    job = job_snapshot.to_dict() or {}
    attempt = attempt_snapshot.to_dict() or {}
    if (
        not _claim_is_current(job, claim)
        or attempt.get("owner_uid") != claim.owner_uid
        or attempt.get("status") != "processing"
    ):
        return False

    result_status = str(result.get("status") or "")
    if result_status not in {"complete", "time_barred", "not_state_actor"}:
        raise ValueError("Worker result is not a successful terminal outcome")

    now = _utc_now()
    attempt_update = _attempt_result_update(result)
    attempt_update["updated_at"] = now
    transaction.update(attempt_reference, attempt_update)
    transaction.update(
        job_reference,
        {
            "status": "complete",
            "attempt_status": result_status,
            "lease_token": None,
            "lease_owner": None,
            "lease_expires_at": None,
            "completed_at": now,
            "updated_at": now,
        },
    )
    return True


async def complete_claimed_analysis_job(
    *, claim: ClaimedAnalysisJob, result: dict[str, Any]
) -> bool:
    return await asyncio.to_thread(_complete_claimed_job_sync, claim, result)


def _complete_claimed_job_sync(
    claim: ClaimedAnalysisJob, result: dict[str, Any]
) -> bool:
    transaction = get_firestore_client().transaction()
    return _complete_claimed_job_transaction(
        transaction,
        claim=claim,
        result=result,
    )


@firestore.transactional
def _fail_claimed_job_transaction(
    transaction,
    *,
    claim: ClaimedAnalysisJob,
    error_code: str,
) -> bool:
    job_reference = _job_ref(claim.attempt_id)
    attempt_reference = _attempt_ref(claim.owner_uid, claim.attempt_id)
    job_snapshot = job_reference.get(transaction=transaction)
    attempt_snapshot = attempt_reference.get(transaction=transaction)
    if not job_snapshot.exists or not attempt_snapshot.exists:
        return False
    job = job_snapshot.to_dict() or {}
    attempt = attempt_snapshot.to_dict() or {}
    if (
        not _claim_is_current(job, claim)
        or attempt.get("owner_uid") != claim.owner_uid
        or attempt.get("status") != "processing"
    ):
        return False

    now = _utc_now()
    safe_error_code = str(error_code or "analysis_failed")[:80]
    transaction.update(
        attempt_reference,
        {"status": "failed", "error_code": safe_error_code, "updated_at": now},
    )
    transaction.update(
        job_reference,
        {
            "status": "failed",
            "error_code": safe_error_code,
            "lease_token": None,
            "lease_owner": None,
            "lease_expires_at": None,
            "failed_at": now,
            "updated_at": now,
        },
    )
    return True


async def fail_claimed_analysis_job(
    *,
    claim: ClaimedAnalysisJob,
    error_code: str = "analysis_failed",
) -> bool:
    return await asyncio.to_thread(_fail_claimed_job_sync, claim, error_code)


def _fail_claimed_job_sync(
    claim: ClaimedAnalysisJob,
    error_code: str,
) -> bool:
    transaction = get_firestore_client().transaction()
    return _fail_claimed_job_transaction(
        transaction,
        claim=claim,
        error_code=error_code,
    )


@firestore.transactional
def _release_job_transaction(
    transaction,
    *,
    claim: ClaimedAnalysisJob,
) -> bool:
    reference = _job_ref(claim.attempt_id)
    snapshot = reference.get(transaction=transaction)
    if not snapshot.exists:
        return False
    job = snapshot.to_dict() or {}
    if not _claim_is_current(job, claim):
        return False
    now = _utc_now()
    transaction.update(
        reference,
        {
            "status": "queued",
            "available_at": now,
            "lease_token": None,
            "lease_owner": None,
            "lease_expires_at": None,
            "updated_at": now,
        },
    )
    return True


async def release_analysis_job(*, claim: ClaimedAnalysisJob) -> bool:
    return await asyncio.to_thread(_release_job_sync, claim)


def _release_job_sync(claim: ClaimedAnalysisJob) -> bool:
    transaction = get_firestore_client().transaction()
    return _release_job_transaction(transaction, claim=claim)


@firestore.transactional
def _retry_attempt_transaction(
    transaction,
    *,
    uid: str,
    attempt_id: str,
) -> AttemptSubmission:
    user_reference = _user_ref(uid)
    attempt_reference = _attempt_ref(uid, attempt_id)
    job_reference = _job_ref(attempt_id)
    profile_snapshot = user_reference.get(transaction=transaction)
    attempt_snapshot = attempt_reference.get(transaction=transaction)
    job_snapshot = job_reference.get(transaction=transaction)

    if not profile_snapshot.exists:
        raise PermissionError("Active user profile required")
    profile = profile_snapshot.to_dict() or {}
    if (
        profile.get("owner_uid") != uid
        or profile.get("account_status") != "active"
        or profile.get("terms_version") != config.current_terms_version
        or profile.get("privacy_version") != config.current_privacy_version
        or profile.get("assessment_consent_version")
        != config.current_assessment_consent_version
    ):
        raise PermissionError("Active user profile required")
    if not attempt_snapshot.exists:
        raise FileNotFoundError("Attempt not found")
    attempt = attempt_snapshot.to_dict() or {}
    if attempt.get("owner_uid") != uid:
        raise FileNotFoundError("Attempt not found")
    if attempt.get("status") != "failed":
        raise AttemptNotRetryableError("Only failed attempts can be retried")

    job = (job_snapshot.to_dict() or {}) if job_snapshot.exists else {}
    if job and job.get("owner_uid") != uid:
        raise AttemptConflictError("Job ownership check failed")
    generation = max(1, int(job.get("generation") or 1)) + 1
    retry_count = max(0, int(job.get("retry_count") or 0)) + 1
    now = _utc_now()

    transaction.update(
        attempt_reference,
        {
            "status": "processing",
            "error_code": "",
            "step_results": {},
            "final_answer": "",
            "final_answer_with_disclaimer": "",
            "structured_assessment": {},
            "final_potentially_violated_articles": [],
            "final_weak_or_uncertain_articles": [],
            "final_rejected_articles": [],
            "overall_assessment": "",
            "precedent_alignment": "",
            "article_assessments": [],
            "confidence": {},
            "confidence_level": "",
            "flags": [],
            "articles_identified": [],
            "similar_case_ids": [],
            "completed_at": None,
            "worker_generation": generation,
            "updated_at": now,
        },
    )
    job_document = _new_job_document(
        uid=uid,
        attempt_id=attempt_id,
        now=now,
        generation=generation,
        retry_count=retry_count,
    )
    if job_snapshot.exists:
        job_document["created_at"] = job.get("created_at") or now
        transaction.set(job_reference, job_document)
    else:
        transaction.create(job_reference, job_document)
    return AttemptSubmission(
        attempt_id=attempt_id,
        status="processing",
        created=False,
    )


async def retry_attempt(
    *, user_id: str, attempt_id: str | uuid.UUID
) -> AttemptSubmission:
    return await asyncio.to_thread(_retry_attempt_sync, user_id, attempt_id)


def _retry_attempt_sync(
    uid: str, attempt_id: str | uuid.UUID
) -> AttemptSubmission:
    normalized_uid = _validate_uid(uid)
    normalized_attempt_id = _validate_attempt_id(attempt_id)
    transaction = get_firestore_client().transaction()
    return _retry_attempt_transaction(
        transaction,
        uid=normalized_uid,
        attempt_id=normalized_attempt_id,
    )


async def get_attempt(
    *,
    user_id: str,
    attempt_id: str | uuid.UUID,
) -> dict[str, Any]:
    return await asyncio.to_thread(_owned_attempt_sync, user_id, attempt_id)


@firestore.transactional
def _delete_attempt_transaction(
    transaction,
    *,
    uid: str,
    attempt_id: str,
) -> None:
    attempt_reference = _attempt_ref(uid, attempt_id)
    job_reference = _job_ref(attempt_id)
    attempt_snapshot = attempt_reference.get(transaction=transaction)
    job_snapshot = job_reference.get(transaction=transaction)
    if not attempt_snapshot.exists:
        raise FileNotFoundError("Attempt not found")
    attempt = attempt_snapshot.to_dict() or {}
    if attempt.get("owner_uid") != uid:
        raise FileNotFoundError("Attempt not found")
    if job_snapshot.exists:
        job = job_snapshot.to_dict() or {}
        if job.get("owner_uid") != uid:
            raise RuntimeError("Job ownership check failed")
        transaction.delete(job_reference)
    transaction.delete(attempt_reference)


async def delete_attempt(
    *, user_id: str, attempt_id: str | uuid.UUID
) -> None:
    await asyncio.to_thread(_delete_attempt_sync, user_id, attempt_id)


def _delete_attempt_sync(uid: str, attempt_id: str | uuid.UUID) -> None:
    normalized_uid = _validate_uid(uid)
    normalized_attempt_id = _validate_attempt_id(attempt_id)
    transaction = get_firestore_client().transaction()
    _delete_attempt_transaction(
        transaction,
        uid=normalized_uid,
        attempt_id=normalized_attempt_id,
    )


async def delete_user_history(*, user_id: str) -> None:
    await erase_user_assessment_data(uid=user_id)


async def get_user_history(
    *,
    user_id: str,
    limit: int = 20,
) -> list[dict[str, Any]]:
    if not 1 <= limit <= config.history_max_limit:
        raise ValueError("Invalid history limit")
    return await asyncio.to_thread(_get_user_history_sync, user_id, limit)


def _get_user_history_sync(uid: str, limit: int) -> list[dict[str, Any]]:
    normalized_uid = _validate_uid(uid)
    query = (
        _user_ref(normalized_uid)
        .collection("attempts")
        .order_by("created_at", direction=firestore.Query.DESCENDING)
        .limit(limit)
    )

    items: list[dict[str, Any]] = []
    for snapshot in query.stream():
        data = snapshot.to_dict() or {}
        if data.get("owner_uid") != normalized_uid:
            logger.error("History record failed its ownership integrity check")
            continue
        final_fields = _extract_final_structured_fields(data)
        items.append(
            {
                "attempt_id": data.get("attempt_id"),
                "status": data.get("status"),
                "confidence_level": data.get("confidence_level"),
                "flags": _safe_list(data.get("flags")),
                "articles_identified": _safe_list(
                    data.get("articles_identified")
                ),
                **final_fields,
                "similar_case_ids": _safe_list(data.get("similar_case_ids")),
                "created_at": data.get("created_at"),
                "updated_at": data.get("updated_at"),
            }
        )
    return items


# ---------------------------------------------------------------------------
# Result normalization
# ---------------------------------------------------------------------------

def _safe_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _safe_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _string_list(value: Any) -> list[str]:
    values = value if isinstance(value, (list, tuple)) else [value]
    seen: set[str] = set()
    output: list[str] = []
    for item in values:
        text = str(item or "").strip()
        if text and text not in seen:
            seen.add(text)
            output.append(text)
    return output


def _empty_structured_assessment() -> dict[str, Any]:
    return {
        "final_potentially_violated_articles": [],
        "final_weak_or_uncertain_articles": [],
        "final_rejected_articles": [],
        "overall_assessment": "",
        "precedent_alignment": "",
        "article_assessments": [],
        "key_strengths": [],
        "key_weaknesses": [],
        "faithfulness_notes": [],
    }


def _extract_structured_assessment(source: dict[str, Any]) -> dict[str, Any]:
    candidates = [
        source.get("structured_assessment"),
        _safe_dict(
            _safe_dict(_safe_dict(source.get("step_results")).get("step_10")).get(
                "data"
            )
        ).get("structured_assessment"),
        _safe_dict(_safe_dict(source.get("confidence")).get("evaluation")).get(
            "structured_assessment"
        ),
    ]
    raw = next((item for item in candidates if isinstance(item, dict)), {})
    normalized = {**_empty_structured_assessment(), **raw}
    for key in (
        "final_potentially_violated_articles",
        "final_weak_or_uncertain_articles",
        "final_rejected_articles",
        "key_strengths",
        "key_weaknesses",
        "faithfulness_notes",
    ):
        normalized[key] = _string_list(normalized.get(key))
    normalized["article_assessments"] = [
        item
        for item in _safe_list(normalized.get("article_assessments"))
        if isinstance(item, dict)
    ]
    normalized["overall_assessment"] = str(
        normalized.get("overall_assessment") or ""
    ).strip()
    normalized["precedent_alignment"] = str(
        normalized.get("precedent_alignment") or ""
    ).strip()
    return normalized


def _extract_final_structured_fields(source: dict[str, Any]) -> dict[str, Any]:
    structured = _extract_structured_assessment(source)
    mapping = {
        "final_potentially_violated_articles": _string_list(
            source.get(
                "final_potentially_violated_articles",
                structured["final_potentially_violated_articles"],
            )
        ),
        "final_weak_or_uncertain_articles": _string_list(
            source.get(
                "final_weak_or_uncertain_articles",
                structured["final_weak_or_uncertain_articles"],
            )
        ),
        "final_rejected_articles": _string_list(
            source.get(
                "final_rejected_articles",
                structured["final_rejected_articles"],
            )
        ),
        "overall_assessment": str(
            source.get("overall_assessment", structured["overall_assessment"])
            or ""
        ).strip(),
        "precedent_alignment": str(
            source.get("precedent_alignment", structured["precedent_alignment"])
            or ""
        ).strip(),
        "article_assessments": [
            item
            for item in _safe_list(
                source.get("article_assessments", structured["article_assessments"])
            )
            if isinstance(item, dict)
        ],
    }
    structured.update(mapping)
    return {"structured_assessment": structured, **mapping}
