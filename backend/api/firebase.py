"""
himikama/backend/api/firebase.py
═══════════════════════════════════════════════════════════════
Firestore persistence for Himikama attempts.

Purpose:
    Store full runner.py results so the UI can fetch the reasoning
    trace later without rerunning the chain.

Firestore structure:
    users/{user_id}/attempts/{attempt_id}
═══════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import firebase_admin
from firebase_admin import credentials, firestore

from api.config import config

logger = logging.getLogger(__name__)

_db = None


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_firestore_client():
    """
    Initialise Firebase Admin SDK once and return Firestore client.

    This must run only on the backend/server, never in Flutter.
    """
    global _db

    if _db is not None:
        return _db

    service_account_path = Path(config.firebase_service_account_path)

    if not service_account_path.exists():
        raise FileNotFoundError(
            f"Firebase service account file not found: {service_account_path}"
        )

    if not firebase_admin._apps:
        cred = credentials.Certificate(str(service_account_path))
        firebase_admin.initialize_app(cred)

    _db = firestore.client()
    return _db


def _attempt_ref(user_id: str, attempt_id: str):
    """
    Return Firestore document reference:
        users/{user_id}/attempts/{attempt_id}
    """
    db = get_firestore_client()

    return (
        db.collection(config.firestore_users_collection)
        .document(user_id)
        .collection("attempts")
        .document(attempt_id)
    )


async def create_attempt(
    *,
    user_id: str,
    intake_object: dict[str, Any],
) -> str:
    """
    Create a pending attempt document before running the chain.

    Returns:
        attempt_id
    """
    attempt_id = str(uuid.uuid4())

    doc = {
        "attempt_id": attempt_id,
        "user_id": user_id,
        "user_narrative": intake_object.get("user_narrative", ""),
        "intake_object": intake_object,
        "status": "pending",
        "created_at": _utc_now_iso(),
        "updated_at": _utc_now_iso(),
    }

    _attempt_ref(user_id, attempt_id).set(doc)

    logger.info("Created attempt %s for user %s", attempt_id, user_id)

    return attempt_id


async def mark_attempt_failed(
    *,
    user_id: str,
    attempt_id: str,
    error: str,
) -> None:
    """
    Mark an attempt as failed if the chain crashes before saving result.
    """
    _attempt_ref(user_id, attempt_id).set(
        {
            "status": "failed",
            "error": error,
            "updated_at": _utc_now_iso(),
        },
        merge=True,
    )


async def save_attempt_result(
    *,
    user_id: str,
    attempt_id: str,
    result: dict[str, Any],
) -> None:
    """
    Save full runner.py result.

    This stores:
        - final answer
        - confidence
        - step_results for trace
        - articles/cases summary
        - status and timestamps
    """
    doc_update = {
        "status": result.get("status"),
        "step_results": result.get("step_results", {}),
        "final_answer": result.get("final_answer", ""),
        "final_answer_with_disclaimer": result.get(
            "final_answer_with_disclaimer",
            "",
        ),
        "confidence": result.get("confidence", {}),
        "confidence_level": result.get("confidence_level", ""),
        "flags": result.get("flags", []),
        "articles_identified": result.get("articles_identified", []),
        "similar_case_ids": result.get("similar_case_ids", []),
        "error": result.get("error"),
        "started_at": result.get("started_at"),
        "completed_at": result.get("completed_at"),
        "updated_at": _utc_now_iso(),
    }

    _attempt_ref(user_id, attempt_id).set(doc_update, merge=True)

    logger.info("Saved result for attempt %s", attempt_id)


async def get_attempt(
    *,
    user_id: str,
    attempt_id: str,
) -> dict[str, Any]:
    """
    Get full attempt document from Firestore.
    """
    snap = _attempt_ref(user_id, attempt_id).get()

    if not snap.exists:
        raise FileNotFoundError(f"Attempt not found: {attempt_id}")

    return snap.to_dict()


async def get_user_history(
    *,
    user_id: str,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """
    Get lightweight attempt summaries for history screen.
    """
    db = get_firestore_client()

    query = (
        db.collection(config.firestore_users_collection)
        .document(user_id)
        .collection("attempts")
        .order_by("created_at", direction=firestore.Query.DESCENDING)
        .limit(limit)
    )

    items: list[dict[str, Any]] = []

    for doc in query.stream():
        data = doc.to_dict()

        items.append({
            "attempt_id": data.get("attempt_id"),
            "status": data.get("status"),
            "confidence_level": data.get("confidence_level"),
            "flags": data.get("flags", []),
            "articles_identified": data.get("articles_identified", []),
            "similar_case_ids": data.get("similar_case_ids", []),
            "created_at": data.get("created_at"),
            "updated_at": data.get("updated_at"),
        })

    return items
