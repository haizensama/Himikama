"""
himikama/backend/chain/runner.py
═══════════════════════════════════════════════════════════════
Phase 4/5 — Chain Runner / Orchestrator
Himikama — Sri Lankan Fundamental Rights Legal AI

Responsibility:
    Runs the full Himikama legal reasoning chain in the correct
    deterministic order.

    This file connects:
        • chain.steps        → individual reasoning steps
        • chain.retrieval    → indirectly through steps.py
        • chain.confidence   → final confidence scoring
        • ingestion.embedder → ChromaDB collection loading
        • api.config         → DB path / app configuration

    This module does NOT write to Firestore directly.
    It returns a complete Firestore-ready result object.
    The API/service layer should handle persistence.

Why runner.py exists:
    FastAPI endpoints should not manually coordinate all 10 steps.
    steps.py should not know about the full workflow.
    confidence.py should not know how the chain was executed.

    runner.py is the workflow controller.

Execution Flow:
    1. Load ChromaDB collections
    2. Run Step 1 timeliness hard gate
    3. If Step 1 fails → confidence layer → return early
    4. Run Step 2 state actor hard gate
    5. If Step 2 fails → confidence layer → return early
    6. Run Steps 3–10 in order
    7. Pass Step 4 articles into Step 7
    8. Pass Step 7 Stage B cases into Steps 8 and 9
    9. Apply confidence layer
   10. Return full analysis payload

Important:
    This module should be called by the FastAPI analysis endpoint,
    for example:

        result = await run_full_chain(confirmed_intake)

    Then the API/service layer saves result to Firestore.
═══════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

from api.config import config
from chain import steps
from chain.confidence import apply_confidence_layer
from ingestion.embedder import get_article_collection, get_case_collection

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────────────────────

STATUS_RUNNING = "running"
STATUS_COMPLETE = "complete"
STATUS_TIME_BARRED = "time_barred"
STATUS_NOT_STATE_ACTOR = "not_state_actor"
STATUS_FAILED = "failed"


# ─────────────────────────────────────────────────────────────
# RESULT TYPE
# ─────────────────────────────────────────────────────────────

@dataclass
class ChainRunResult:
    """
    Firestore/API-ready result of a chain run.

    Attributes:
        status:
            complete | time_barred | not_state_actor | failed

        step_results:
            Dict of step key → full step result returned by steps.py.

        final_answer:
            Raw Step 10 answer, if Step 10 completed.

        final_answer_with_disclaimer:
            Final answer with mandatory disclaimer appended by
            confidence.py, if available.

        confidence:
            Dict returned by ConfidenceResult.to_dict().

        flags:
            Convenience copy of confidence flags.

        confidence_level:
            Convenience copy of confidence level.

        articles_identified:
            Articles from Step 4.

        similar_case_ids:
            Case IDs selected in Step 7.

        error:
            Error text if the runner failed unexpectedly.

        started_at / completed_at:
            UTC ISO timestamps.
    """

    status: str = STATUS_RUNNING
    step_results: dict[str, Any] = field(default_factory=dict)
    final_answer: str = ""
    final_answer_with_disclaimer: str = ""
    confidence: dict[str, Any] = field(default_factory=dict)
    flags: list[str] = field(default_factory=list)
    confidence_level: str = ""
    articles_identified: list[str] = field(default_factory=list)
    similar_case_ids: list[str] = field(default_factory=list)
    error: str | None = None
    started_at: str = field(default_factory=lambda: _utc_now_iso())
    completed_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """
        Convert to a Firestore/API-friendly dict.
        """
        return asdict(self)


# ─────────────────────────────────────────────────────────────
# PUBLIC ENTRY POINT
# ─────────────────────────────────────────────────────────────

async def run_full_chain(
    intake: dict[str, Any],
    *,
    db_path: str | None = None,
) -> dict[str, Any]:
    """
    Run the complete Himikama reasoning chain for a confirmed intake.

    Args:
        intake:
            Confirmed intake object from Step 0 / user confirmation.
            Expected keys include:
                incident_date
                incident_location
                actor_name
                actor_role
                what_happened
                harm_suffered
                user_narrative

        db_path:
            Optional ChromaDB path override. Defaults to config.db_path.

    Returns:
        Firestore-ready dict containing step results, final synthesis,
        confidence result, flags, and status.

    Notes:
        This function does not write to Firestore. The caller should
        persist the returned dict.
    """
    result = ChainRunResult()

    try:
        _validate_intake_shape(intake)

        chroma_db_path = db_path or config.db_path
        logger.info("Starting Himikama chain run using db_path=%s", chroma_db_path)

        article_collection, case_collection = _load_collections(chroma_db_path)

        # ── Step 1: Timeliness hard gate ─────────────────────
        step_1 = await steps.run_step_1(intake)
        result.step_results["step_1"] = step_1

        if not step_1.get("passed", False):
            return _finalize_early_termination(
                result=result,
                status=STATUS_TIME_BARRED,
                articles_identified=[],
            )

        # ── Step 2: State actor hard gate ────────────────────
        step_2 = await steps.run_step_2(intake, article_collection)
        result.step_results["step_2"] = step_2

        if not step_2.get("passed", False):
            return _finalize_early_termination(
                result=result,
                status=STATUS_NOT_STATE_ACTOR,
                articles_identified=[],
            )

        # ── Step 3: Fact clarification ───────────────────────
        step_3 = await steps.run_step_3(intake)
        result.step_results["step_3"] = step_3

        # ── Step 4: Rights identification ────────────────────
        step_4 = await steps.run_step_4(intake, article_collection)
        result.step_results["step_4"] = step_4

        articles_identified = _extract_articles_identified(step_4)
        result.articles_identified = articles_identified

        # ── Step 5: Nature of violation ──────────────────────
        step_5 = await steps.run_step_5(intake, article_collection)
        result.step_results["step_5"] = step_5

        # ── Step 6: Intent + harm ────────────────────────────
        step_6 = await steps.run_step_6(intake)
        result.step_results["step_6"] = step_6

        # ── Step 7: Similar cases ────────────────────────────
        step_7 = await steps.run_step_7(
            intake,
            case_collection,
            articles_identified,
        )
        result.step_results["step_7"] = step_7

        stage_b_cases = _extract_stage_b_cases(step_7)
        result.similar_case_ids = _extract_case_ids(step_7)

        # ── Step 8: Precedent analysis ───────────────────────
        step_8 = await steps.run_step_8(intake, stage_b_cases)
        result.step_results["step_8"] = step_8

        # ── Step 9: Cross-validation ─────────────────────────
        step_9 = await steps.run_step_9(
            intake,
            stage_b_cases,
            articles_identified,
        )
        result.step_results["step_9"] = step_9

        # ── Step 10: Final synthesis ─────────────────────────
        all_answers = _build_all_answers(result.step_results)
        step_10 = await steps.run_step_10(intake, all_answers)
        result.step_results["step_10"] = step_10
        result.final_answer = step_10.get("answer", "")

        # ── Confidence layer ─────────────────────────────────
        confidence_result = apply_confidence_layer(
            step_results=result.step_results,
            articles_identified=articles_identified,
            final_answer=result.final_answer,
        )

        result.status = confidence_result.status
        result.confidence = confidence_result.to_dict()
        result.flags = confidence_result.flags
        result.confidence_level = confidence_result.confidence_level
        result.final_answer_with_disclaimer = (
            confidence_result.final_answer_with_disclaimer
        )
        result.completed_at = _utc_now_iso()

        logger.info(
            "Chain completed: status=%s, confidence=%s, flags=%s",
            result.status,
            result.confidence_level,
            result.flags,
        )

        return result.to_dict()

    except Exception as e:
        logger.exception("Himikama chain run failed")
        result.status = STATUS_FAILED
        result.error = str(e)
        result.completed_at = _utc_now_iso()
        return result.to_dict()


# ─────────────────────────────────────────────────────────────
# COLLECTION LOADING
# ─────────────────────────────────────────────────────────────

def _load_collections(db_path: str):
    """
    Load ChromaDB article and case collections.

    Returns:
        (article_collection, case_collection)

    Raises:
        RuntimeError if collections cannot be loaded.
    """
    try:
        article_collection = get_article_collection(db_path)
        case_collection = get_case_collection(db_path)
    except Exception as e:
        raise RuntimeError(
            "Could not load ChromaDB collections. Run the ingestion "
            "pipeline first and confirm DB_PATH is correct. "
            f"Original error: {e}"
        )

    article_count = _safe_collection_count(article_collection)
    case_count = _safe_collection_count(case_collection)

    if article_count == 0:
        raise RuntimeError(
            "constitutional_articles collection is empty. Run ingestion first."
        )

    if case_count == 0:
        raise RuntimeError(
            "case_summaries collection is empty. Run ingestion first."
        )

    logger.info(
        "Loaded ChromaDB collections: constitutional_articles=%s, case_summaries=%s",
        article_count,
        case_count,
    )

    return article_collection, case_collection


def _safe_collection_count(collection) -> int:
    """
    Safely read a ChromaDB collection count.
    """
    try:
        return int(collection.count())
    except Exception:
        return 0


# ─────────────────────────────────────────────────────────────
# EARLY TERMINATION
# ─────────────────────────────────────────────────────────────

def _finalize_early_termination(
    *,
    result: ChainRunResult,
    status: str,
    articles_identified: list[str],
) -> dict[str, Any]:
    """
    Apply confidence layer after a hard-gate failure and return.
    """
    confidence_result = apply_confidence_layer(
        step_results=result.step_results,
        articles_identified=articles_identified,
        final_answer="",
    )

    result.status = status
    result.confidence = confidence_result.to_dict()
    result.flags = confidence_result.flags
    result.confidence_level = confidence_result.confidence_level
    result.final_answer_with_disclaimer = (
        confidence_result.final_answer_with_disclaimer
    )
    result.completed_at = _utc_now_iso()

    logger.info(
        "Chain terminated early: status=%s, flags=%s",
        result.status,
        result.flags,
    )

    return result.to_dict()


# ─────────────────────────────────────────────────────────────
# EXTRACTION HELPERS
# ─────────────────────────────────────────────────────────────

def _extract_articles_identified(step_4_result: dict[str, Any]) -> list[str]:
    """
    Extract Step 4 article list safely.
    """
    data = step_4_result.get("data", {}) if isinstance(step_4_result, dict) else {}
    articles = data.get("articles_identified", [])

    if not isinstance(articles, list):
        return []

    return _dedupe_preserve_order([str(article) for article in articles if article])


def _extract_stage_b_cases(step_7_result: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Extract Stage B cases from Step 7 safely.
    """
    data = step_7_result.get("data", {}) if isinstance(step_7_result, dict) else {}
    cases = data.get("stage_b_cases", [])

    if not isinstance(cases, list):
        return []

    return [case for case in cases if isinstance(case, dict)]


def _extract_case_ids(step_7_result: dict[str, Any]) -> list[str]:
    """
    Extract selected similar case IDs from Step 7 safely.
    """
    data = step_7_result.get("data", {}) if isinstance(step_7_result, dict) else {}
    case_ids = data.get("case_ids", [])

    if not isinstance(case_ids, list):
        return []

    return _dedupe_preserve_order([str(case_id) for case_id in case_ids if case_id])


def _build_all_answers(step_results: dict[str, Any]) -> dict[str, str]:
    """
    Build the Step 10 input dictionary from completed step results.

    Step 10 expects a dict of step_key → answer string.
    """
    answers: dict[str, str] = {}

    for i in range(1, 10):
        key = f"step_{i}"
        step_result = step_results.get(key, {})

        if isinstance(step_result, dict):
            answers[key] = str(step_result.get("answer", "Not completed."))
        else:
            answers[key] = "Not completed."

    return answers


def _dedupe_preserve_order(items: list[str]) -> list[str]:
    """
    Deduplicate while preserving order.
    """
    seen: set[str] = set()
    output: list[str] = []

    for item in items:
        if item not in seen:
            seen.add(item)
            output.append(item)

    return output


def _utc_now_iso() -> str:
    """
    Current UTC timestamp as ISO string.
    """
    return datetime.now(timezone.utc).isoformat()


# ─────────────────────────────────────────────────────────────
# VALIDATION
# ─────────────────────────────────────────────────────────────

def _validate_intake_shape(intake: dict[str, Any]) -> None:
    """
    Minimal validation for confirmed intake input.

    This does not replace Pydantic validation in api/schemas.py.
    It only prevents runner.py from operating on invalid objects.
    """
    if not isinstance(intake, dict):
        raise TypeError("intake must be a dict")

    required_keys = {
        "incident_date",
        "actor_role",
        "what_happened",
        "user_narrative",
    }

    missing = [key for key in required_keys if key not in intake]
    if missing:
        raise ValueError(
            "Confirmed intake is missing required key(s): "
            + ", ".join(missing)
        )


# ─────────────────────────────────────────────────────────────
# OPTIONAL DEBUG SUMMARY
# ─────────────────────────────────────────────────────────────

def summarize_chain_result(result: dict[str, Any]) -> dict[str, Any]:
    """
    Create a compact summary for logs, tests, or history lists.

    This is useful for GET /history/{user_id}, where the frontend does
    not need the full reasoning trace for every attempt.
    """
    step_results = result.get("step_results", {})

    return {
        "status": result.get("status"),
        "confidence_level": result.get("confidence_level"),
        "flags": result.get("flags", []),
        "articles_identified": result.get("articles_identified", []),
        "similar_case_ids": result.get("similar_case_ids", []),
        "started_at": result.get("started_at"),
        "completed_at": result.get("completed_at"),
        "step_1_explanation": _safe_step_explanation(step_results, "step_1"),
        "step_4_explanation": _safe_step_explanation(step_results, "step_4"),
        "step_10_explanation": _safe_step_explanation(step_results, "step_10"),
    }


def _safe_step_explanation(step_results: dict[str, Any], step_key: str) -> str:
    """
    Safely extract a step explanation.
    """
    step = step_results.get(step_key, {})
    if not isinstance(step, dict):
        return ""
    return str(step.get("explanation", ""))
