"""
himikama/backend/chain/attempt.py
═══════════════════════════════════════════════════════════════
Phase 3 — Attempt Management (Local Storage)

Responsibility:
    Create, read, and list attempt records.
    Stores the intake object, all step answers, synthesis,
    confidence level, and flags for each user attempt.

    PHASE 3 VERSION — Local JSON Storage:
        Attempts are stored as JSON files in attempts/ folder.
        This allows the full flow to be tested end-to-end
        without Firebase during local development.

    PHASE 6 UPGRADE — Firebase Firestore:
        This module will be replaced with a Firebase version.
        The function signatures stay identical so main.py
        requires zero changes when Firebase is added.

        Storage path in Firestore:
        users → {user_id} → attempts → {attempt_id}

Stored per attempt:
    attempt_id:       Unique ID (UUID)
    user_id:          "local" in Phase 3 (Firebase UID later)
    user_narrative:   Original verbatim user input
    intake_object:    Confirmed structured intake fields
    step_answers:     Dict of step_1 through step_10 answers
    final_synthesis:  Step 10 output
    confidence_level: "high" | "medium" | "low"
    flags:            List of flag strings
    status:           "pending" | "complete" | "time_barred"
                      | "not_state_actor"
    timestamp:        ISO format datetime string

Usage:
    from chain.attempt import (
        create_attempt,
        save_step_answer,
        complete_attempt,
        get_attempt_by_id,
        get_user_history,
    )
═══════════════════════════════════════════════════════════════
"""

import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────
# STORAGE CONFIGURATION
# ─────────────────────────────────────────────────────────────

# Local attempts folder — created automatically if not present
# Phase 6: this entire section replaced by Firestore client
ATTEMPTS_DIR = Path("attempts")


def _ensure_attempts_dir() -> None:
    """Create the attempts/ directory if it does not exist."""
    ATTEMPTS_DIR.mkdir(exist_ok=True)


def _attempt_path(attempt_id: str) -> Path:
    """Return the file path for a given attempt_id."""
    return ATTEMPTS_DIR / f"{attempt_id}.json"


# ─────────────────────────────────────────────────────────────
# INTERNAL READ / WRITE
# ─────────────────────────────────────────────────────────────

def _read_attempt(attempt_id: str) -> dict:
    """
    Read an attempt from local JSON storage.

    Args:
        attempt_id: The attempt UUID.

    Returns:
        Attempt dict.

    Raises:
        FileNotFoundError: If attempt does not exist.
    """
    path = _attempt_path(attempt_id)
    if not path.exists():
        raise FileNotFoundError(
            f"Attempt '{attempt_id}' not found in {ATTEMPTS_DIR}/"
        )
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _write_attempt(attempt: dict) -> None:
    """
    Write an attempt dict to local JSON storage.

    Args:
        attempt: Complete attempt dict.
    """
    _ensure_attempts_dir()
    path = _attempt_path(attempt["attempt_id"])
    with open(path, "w", encoding="utf-8") as f:
        json.dump(attempt, f, indent=2, ensure_ascii=False)


def _new_attempt_record(
    intake_object: dict,
    user_id: str = "local",
) -> dict:
    """
    Create a fresh attempt record with empty step slots.

    Args:
        intake_object: Confirmed intake fields from Flutter.
        user_id:       Firebase Auth UID. "local" in Phase 3.

    Returns:
        Attempt dict ready to be written to storage.
    """
    return {
        "attempt_id":       str(uuid.uuid4()),
        "user_id":          user_id,
        "user_narrative":   intake_object.get("user_narrative", ""),
        "intake_object":    intake_object,
        "step_answers":     {},
        "final_synthesis":  "",
        "confidence_level": "",
        "flags":            [],
        "status":           "pending",
        "disclaimer":       "",
        "timestamp":        datetime.now(timezone.utc).isoformat(),
    }


# ─────────────────────────────────────────────────────────────
# PUBLIC FUNCTIONS
# ─────────────────────────────────────────────────────────────

async def create_attempt(
    intake_object: dict,
    user_id: str = "local",
) -> str:
    """
    Create a new attempt record and return its ID.

    Called by POST /confirm after the user approves the
    extracted intake object. The chain runner will then
    populate the step answers and synthesis.

    Args:
        intake_object: Confirmed intake fields from Flutter.
        user_id:       Firebase Auth UID ("local" for now).

    Returns:
        attempt_id string (UUID).
    """
    attempt = _new_attempt_record(intake_object, user_id)
    _write_attempt(attempt)

    logger.info(
        f"Attempt created: {attempt['attempt_id']} "
        f"(user: {user_id})"
    )
    return attempt["attempt_id"]


async def save_step_answer(
    attempt_id: str,
    step_key: str,
    answer: str | dict,
) -> None:
    """
    Save a single step answer to the attempt record.

    Called by the chain runner after each step completes
    so that results are persisted incrementally. If the
    chain fails mid-run, completed steps are not lost.

    Args:
        attempt_id: The attempt UUID.
        step_key:   Step identifier e.g. "step_1", "step_4".
        answer:     The LLM answer for that step.

    Raises:
        FileNotFoundError: If attempt does not exist.
    """
    attempt = _read_attempt(attempt_id)
    attempt["step_answers"][step_key] = answer
    _write_attempt(attempt)
    logger.debug(f"Saved {step_key} for attempt {attempt_id}")


async def complete_attempt(
    attempt_id: str,
    final_synthesis: str,
    confidence_level: str,
    flags: list[str],
    status: str,
    disclaimer: str,
) -> None:
    """
    Mark an attempt as complete and save the final outputs.

    Called by the chain runner after Step 10 and the
    confidence layer have both finished.

    Args:
        attempt_id:       The attempt UUID.
        final_synthesis:  Step 10 LLM output.
        confidence_level: "high" | "medium" | "low".
        flags:            List of flag strings.
        status:           "complete" | "time_barred" |
                          "not_state_actor".
        disclaimer:       Mandatory legal disclaimer text.

    Raises:
        FileNotFoundError: If attempt does not exist.
    """
    attempt = _read_attempt(attempt_id)
    attempt["final_synthesis"]  = final_synthesis
    attempt["confidence_level"] = confidence_level
    attempt["flags"]            = flags
    attempt["status"]           = status
    attempt["disclaimer"]       = disclaimer
    _write_attempt(attempt)

    logger.info(
        f"Attempt {attempt_id} complete — "
        f"status: {status}, confidence: {confidence_level}, "
        f"flags: {flags}"
    )


async def get_attempt_by_id(attempt_id: str) -> dict:
    """
    Retrieve a full attempt record by its ID.

    Called by GET /attempt/{attempt_id} when a user
    views a past attempt from the history screen.

    Args:
        attempt_id: The attempt UUID.

    Returns:
        Full attempt dict.

    Raises:
        FileNotFoundError: If attempt does not exist.
    """
    return _read_attempt(attempt_id)


async def get_user_history(user_id: str) -> list[dict]:
    """
    Retrieve all attempt summaries for a user.

    Called by GET /history/{user_id}.
    Returns lightweight summaries — not full reasoning traces —
    for display in the Flutter history list screen.

    Phase 3: Returns all attempts since user_id is "local".
    Phase 6: Filters by Firebase Auth user_id in Firestore.

    Args:
        user_id: Firebase Auth UID.

    Returns:
        List of attempt summary dicts, newest first.
        Each summary contains:
            attempt_id, timestamp, status,
            confidence_level, articles_cited
    """
    _ensure_attempts_dir()

    summaries = []
    for path in ATTEMPTS_DIR.glob("*.json"):
        try:
            with open(path, "r", encoding="utf-8") as f:
                attempt = json.load(f)

            # Phase 6: filter by user_id
            # Phase 3: return all (user_id is always "local")
            if user_id != "local" and attempt.get("user_id") != user_id:
                continue

            # Extract articles from step_4 answer if available
            step_4 = attempt.get("step_answers", {}).get("step_4", "")
            articles = _extract_articles_from_answer(step_4)

            summaries.append({
                "attempt_id":       attempt["attempt_id"],
                "timestamp":        attempt["timestamp"],
                "status":           attempt.get("status", "pending"),
                "confidence_level": attempt.get("confidence_level", ""),
                "articles_cited":   articles,
            })

        except (json.JSONDecodeError, KeyError) as e:
            logger.warning(f"Could not read attempt file {path}: {e}")
            continue

    # Sort newest first
    summaries.sort(key=lambda x: x["timestamp"], reverse=True)
    return summaries


# ─────────────────────────────────────────────────────────────
# INTERNAL HELPERS
# ─────────────────────────────────────────────────────────────

def _extract_articles_from_answer(step_4_answer: str | dict) -> list[str]:
    """
    Extract article numbers from the Step 4 answer for the
    history list summary. Step 4 identifies which articles
    were potentially violated.

    Args:
        step_4_answer: The Step 4 LLM output (string or dict).

    Returns:
        List of article number strings found in the answer.
        Empty list if Step 4 has not run yet.
    """
    if not step_4_answer:
        return []

    text = (
        json.dumps(step_4_answer)
        if isinstance(step_4_answer, dict)
        else str(step_4_answer)
    )

    import re
    articles = re.findall(r"\d+(?:\(\d+\))?(?:\([a-zA-Z]\))?", text)
    return list(set(articles)) if articles else []
