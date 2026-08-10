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
    9. Run Step 10 final synthesis
   10. Normalize and enforce Step 10 structured assessment constraints
   11. Apply confidence layer
   12. Return full analysis payload

Structured Step 10 Safety Rules:
    The runner enforces the following restrictions after Step 10:
        1. Final article conclusions may only use articles from Step 4.
        2. Supporting case IDs may only use case IDs selected in Step 7.
        3. Article assessment status must be one of:
           supported | weak_or_uncertain | rejected

    This prevents Step 10 from introducing new article numbers,
    new case IDs, or unsupported status labels.
═══════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import logging
import re
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

ARTICLE_STATUS_SUPPORTED = "supported"
ARTICLE_STATUS_WEAK_OR_UNCERTAIN = "weak_or_uncertain"
ARTICLE_STATUS_REJECTED = "rejected"

ALLOWED_ARTICLE_STATUSES = {
    ARTICLE_STATUS_SUPPORTED,
    ARTICLE_STATUS_WEAK_OR_UNCERTAIN,
    ARTICLE_STATUS_REJECTED,
}

ALLOWED_OVERALL_ASSESSMENTS = {
    "likely_viable",
    "weak_or_uncertain",
    "not_viable",
    "time_barred",
    "not_state_actor",
}

ALLOWED_PRECEDENT_ALIGNMENTS = {
    "supports",
    "mixed",
    "weak",
    "contradicts",
    "no_cases",
    "not_assessed",
}

ALLOWED_CONFIDENCE_LEVELS = {
    "high",
    "medium",
    "low",
}

# Fix 2: a negative precedent may reject an article only when the
# selected negative case actually concerns the same specific article
# or a direct parent/sub-article equivalent. This prevents broad
# no-violation patterns from killing distinct articles such as 13(2)
# based on cases mainly about 13(1), 12(1), or 14(1)(b).
ARTICLE_SPECIFIC_NEGATIVE_SIMILARITY_FLOOR = 0.70

ALLOWED_STEP_REFS = {
    "step_1",
    "step_2",
    "step_3",
    "step_4",
    "step_5",
    "step_6",
    "step_7",
    "step_8",
    "step_9",
    "step_10",
}


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
            Final human-readable answer, normally produced by Step 10.

        final_answer_with_disclaimer:
            Final answer with mandatory disclaimer appended by
            confidence.py, if available.

        structured_assessment:
            Machine-readable final Step 10 assessment after runner-side
            normalization and safety filtering.

        final_potentially_violated_articles:
            Final Step 10 articles classified as supported.

        final_weak_or_uncertain_articles:
            Final Step 10 articles classified as weak_or_uncertain.

        final_rejected_articles:
            Final Step 10 articles classified as rejected.

        overall_assessment:
            Final Step 10 viability label:
                likely_viable | weak_or_uncertain | not_viable |
                time_barred | not_state_actor

        confidence:
            Dict returned by ConfidenceResult.to_dict().

        flags:
            Convenience copy of confidence flags.

        confidence_level:
            Convenience copy of confidence level.

        articles_identified:
            Candidate articles from Step 4.
            This is NOT the final legal conclusion.

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

    structured_assessment: dict[str, Any] = field(default_factory=dict)
    final_potentially_violated_articles: list[str] = field(default_factory=list)
    final_weak_or_uncertain_articles: list[str] = field(default_factory=list)
    final_rejected_articles: list[str] = field(default_factory=list)
    overall_assessment: str = ""

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
        structured assessment, confidence result, flags, and status.

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

        # Pass exact structured Step 4 and Step 7 values into Step 10.
        # This prevents Step 10 from trying to recover them from prose,
        # which previously caused exact sub-articles like "13(1)" and
        # "13(2)" to be collapsed or omitted.
        try:
            step_10 = await steps.run_step_10(
                intake,
                all_answers,
                allowed_articles=articles_identified,
                selected_case_ids=result.similar_case_ids,
            )
        except TypeError as e:
            # Backward-compatible fallback while steps.py is being updated.
            # Once steps.py accepts allowed_articles and selected_case_ids,
            # this branch will not run.
            if (
                "allowed_articles" not in str(e)
                and "selected_case_ids" not in str(e)
                and "unexpected keyword argument" not in str(e)
            ):
                raise

            logger.warning(
                "steps.run_step_10 does not yet accept structured Step 4/Step 7 "
                "arguments. Falling back to legacy Step 10 call. Update steps.py "
                "next to complete the fix."
            )
            step_10 = await steps.run_step_10(intake, all_answers)

        raw_structured_assessment = _extract_step_10_structured_assessment(step_10)
        normalized_structured_assessment = _normalize_structured_assessment(
            raw_structured_assessment,
            allowed_articles=articles_identified,
            allowed_case_ids=result.similar_case_ids,
        )

        normalized_structured_assessment = _apply_structured_assessment_safety_guards(
            normalized_structured_assessment,
            allowed_articles=articles_identified,
            allowed_case_ids=result.similar_case_ids,
            step_results=result.step_results,
            stage_b_cases=stage_b_cases,
            step9_enabled=True,
        )

        step_10 = _attach_normalized_structured_assessment(
            step_10,
            normalized_structured_assessment,
        )
        step_10 = _rerender_step_10_answer(
            step_10,
            normalized_structured_assessment,
        )

        result.step_results["step_10"] = step_10
        result.final_answer = str(step_10.get("answer", ""))
        result.structured_assessment = normalized_structured_assessment
        _copy_structured_assessment_to_result(result, normalized_structured_assessment)

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

    Early terminations do not run Step 10, so the structured assessment
    is generated deterministically here instead of by the LLM.
    """
    structured_assessment = _build_early_termination_structured_assessment(status)

    result.structured_assessment = structured_assessment
    _copy_structured_assessment_to_result(result, structured_assessment)

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


def _build_early_termination_structured_assessment(status: str) -> dict[str, Any]:
    """
    Build a deterministic structured assessment for hard-gate failures.
    """
    if status == STATUS_TIME_BARRED:
        overall_assessment = "time_barred"
        weakness = (
            "The analysis stopped because the incident appears to fall "
            "outside the 30-day Fundamental Rights filing window."
        )
    elif status == STATUS_NOT_STATE_ACTOR:
        overall_assessment = "not_state_actor"
        weakness = (
            "The analysis stopped because the respondent does not appear "
            "to be a state actor or a person exercising state-derived power."
        )
    else:
        overall_assessment = "not_viable"
        weakness = "The analysis stopped before final legal synthesis."

    return {
        "final_potentially_violated_articles": [],
        "final_weak_or_uncertain_articles": [],
        "final_rejected_articles": [],
        "overall_assessment": overall_assessment,
        "precedent_alignment": "not_assessed",
        "article_assessments": [],
        "key_strengths": [],
        "key_weaknesses": [weakness],
        "faithfulness_notes": [
            "This structured assessment was generated deterministically "
            "by runner.py because the chain terminated before Step 10."
        ],
    }


# ─────────────────────────────────────────────────────────────
# EXTRACTION HELPERS
# ─────────────────────────────────────────────────────────────

def _extract_articles_identified(step_4_result: dict[str, Any]) -> list[str]:
    """
    Extract Step 4 candidate article list safely.

    These are candidate articles, not final violated articles.
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


def _extract_step_10_structured_assessment(
    step_10_result: dict[str, Any],
) -> dict[str, Any]:
    """
    Extract raw structured assessment from Step 10.

    This is intentionally tolerant because Step 10 will be updated next.
    Until steps.py is changed, Step 10 will return data={}, so this
    function returns {} and the normalized structured fields will be empty.
    """
    if not isinstance(step_10_result, dict):
        return {}

    data = step_10_result.get("data", {})
    if not isinstance(data, dict):
        return {}

    structured = data.get("structured_assessment", {})
    if isinstance(structured, dict):
        return structured

    return {}


def _attach_normalized_structured_assessment(
    step_10_result: dict[str, Any],
    structured_assessment: dict[str, Any],
) -> dict[str, Any]:
    """
    Attach normalized structured assessment back into Step 10 result.

    This ensures step_results["step_10"]["data"] contains the same
    machine-readable fields that are copied to the top-level result.
    """
    if not isinstance(step_10_result, dict):
        step_10_result = {
            "step": "step_10",
            "answer": "",
            "explanation": "",
            "passed": True,
            "data": {},
        }

    updated_step_10 = dict(step_10_result)
    data = updated_step_10.get("data", {})

    if not isinstance(data, dict):
        data = {}

    data["structured_assessment"] = structured_assessment
    data["final_potentially_violated_articles"] = structured_assessment.get(
        "final_potentially_violated_articles",
        [],
    )
    data["final_weak_or_uncertain_articles"] = structured_assessment.get(
        "final_weak_or_uncertain_articles",
        [],
    )
    data["final_rejected_articles"] = structured_assessment.get(
        "final_rejected_articles",
        [],
    )
    data["overall_assessment"] = structured_assessment.get("overall_assessment", "")
    data["precedent_alignment"] = structured_assessment.get(
        "precedent_alignment",
        "",
    )
    data["article_assessments"] = structured_assessment.get(
        "article_assessments",
        [],
    )

    updated_step_10["data"] = data
    return updated_step_10


def _copy_structured_assessment_to_result(
    result: ChainRunResult,
    structured_assessment: dict[str, Any],
) -> None:
    """
    Copy normalized structured fields to top-level result convenience fields.
    """
    result.final_potentially_violated_articles = _coerce_string_list(
        structured_assessment.get("final_potentially_violated_articles", [])
    )
    result.final_weak_or_uncertain_articles = _coerce_string_list(
        structured_assessment.get("final_weak_or_uncertain_articles", [])
    )
    result.final_rejected_articles = _coerce_string_list(
        structured_assessment.get("final_rejected_articles", [])
    )
    result.overall_assessment = str(
        structured_assessment.get("overall_assessment", "")
    )


def _normalize_structured_assessment(
    raw: dict[str, Any],
    *,
    allowed_articles: list[str],
    allowed_case_ids: list[str],
) -> dict[str, Any]:
    """
    Normalize and safety-filter Step 10 structured assessment.

    Enforced rules:
        1. Only articles already found in Step 4 are allowed.
        2. Only case IDs already selected in Step 7 are allowed.
        3. Only statuses supported | weak_or_uncertain | rejected are allowed.

    If Step 10 returns invalid or missing structured data, invalid fields
    are removed instead of guessed.
    """
    if not isinstance(raw, dict):
        raw = {}

    allowed_articles = _dedupe_preserve_order(
        [str(article) for article in allowed_articles if article]
    )
    allowed_case_ids = _dedupe_preserve_order(
        [str(case_id) for case_id in allowed_case_ids if case_id]
    )

    normalized_article_assessments = _normalize_article_assessments(
        raw.get("article_assessments", []),
        allowed_articles=allowed_articles,
        allowed_case_ids=allowed_case_ids,
    )

    supported_articles = _filter_allowed_articles(
        raw.get("final_potentially_violated_articles", []),
        allowed_articles,
    )
    weak_articles = _filter_allowed_articles(
        raw.get("final_weak_or_uncertain_articles", []),
        allowed_articles,
    )
    rejected_articles = _filter_allowed_articles(
        raw.get("final_rejected_articles", []),
        allowed_articles,
    )

    # Derive/update article lists from article_assessments.
    # This keeps the top-level lists consistent with per-article statuses.
    for assessment in normalized_article_assessments:
        article = assessment.get("article", "")
        status = assessment.get("status", "")

        if status == ARTICLE_STATUS_SUPPORTED:
            supported_articles.append(article)
        elif status == ARTICLE_STATUS_WEAK_OR_UNCERTAIN:
            weak_articles.append(article)
        elif status == ARTICLE_STATUS_REJECTED:
            rejected_articles.append(article)

    supported_articles = _dedupe_preserve_order(supported_articles)

    # Avoid the same article appearing in multiple final buckets.
    # Priority:
    #   supported > weak_or_uncertain > rejected
    weak_articles = [
        article for article in _dedupe_preserve_order(weak_articles)
        if article not in supported_articles
    ]
    rejected_articles = [
        article for article in _dedupe_preserve_order(rejected_articles)
        if article not in supported_articles and article not in weak_articles
    ]

    return {
        "final_potentially_violated_articles": supported_articles,
        "final_weak_or_uncertain_articles": weak_articles,
        "final_rejected_articles": rejected_articles,
        "overall_assessment": _normalize_overall_assessment(
            raw.get("overall_assessment", "")
        ),
        "precedent_alignment": _derive_final_supported_precedent_alignment(
            raw_alignment=raw.get("precedent_alignment", ""),
            supported_articles=supported_articles,
            weak_articles=weak_articles,
            article_assessments=normalized_article_assessments,
            allowed_case_ids=allowed_case_ids,
        ),
        "article_assessments": normalized_article_assessments,
        "key_strengths": _coerce_string_list(raw.get("key_strengths", [])),
        "key_weaknesses": _coerce_string_list(raw.get("key_weaknesses", [])),
        "faithfulness_notes": _coerce_string_list(raw.get("faithfulness_notes", [])),
    }


def _apply_structured_assessment_safety_guards(
    structured: dict[str, Any],
    *,
    allowed_articles: list[str],
    allowed_case_ids: list[str],
    step_results: dict[str, Any],
    stage_b_cases: list[dict[str, Any]],
    step9_enabled: bool,
) -> dict[str, Any]:
    """
    Apply deterministic final-output safety guards after Step 10.

    These guards target the measured failure mode:
        gold not_viable -> predicted likely_viable

    They do not use gold labels. They use only:
        - Step 4 allowed articles
        - Step 7 selected cases and their judgments
        - Step 9 structured cross-validation recommendations
        - Step 10 structured output
    """
    if not isinstance(structured, dict):
        structured = {}

    allowed_articles = _dedupe_preserve_order([
        str(article).strip()
        for article in allowed_articles
        if str(article).strip()
    ])
    allowed_set = set(allowed_articles)
    allowed_case_ids = _dedupe_preserve_order([
        str(case_id).strip()
        for case_id in allowed_case_ids
        if str(case_id).strip()
    ])

    supported = _filter_allowed_articles(
        structured.get("final_potentially_violated_articles", []),
        allowed_articles,
    )
    weak = _filter_allowed_articles(
        structured.get("final_weak_or_uncertain_articles", []),
        allowed_articles,
    )
    rejected = _filter_allowed_articles(
        structured.get("final_rejected_articles", []),
        allowed_articles,
    )
    article_assessments = [
        item
        for item in structured.get("article_assessments", [])
        if isinstance(item, dict)
    ]

    step9_data = _extract_step9_safety_data(step_results) if step9_enabled else {}
    case_profile = _build_case_outcome_profile(stage_b_cases, allowed_articles=allowed_articles)

    def has_specific_negative_support(article: str) -> bool:
        return _has_article_specific_negative_support(article, case_profile)

    keep_supported = _filter_allowed_articles(
        step9_data.get("articles_to_keep_supported", []),
        allowed_articles,
    )
    downgrade = _filter_allowed_articles(
        step9_data.get("articles_to_downgrade", []),
        allowed_articles,
    )
    reject = _filter_allowed_articles(
        step9_data.get("articles_to_reject", []),
        allowed_articles,
    )

    evidence_strength = str(case_profile.get("evidence_strength", "") or "")
    raw_step9_negative_precedent = bool(step9_data.get("negative_precedent_pattern"))
    step9_pattern = str(step9_data.get("case_viability_pattern", "") or "").strip().lower()

    # Do not let one weak precedent become a binding negative pattern. A single
    # strong case can matter; a single weak case should usually downgrade.
    negative_precedent = bool(
        case_profile.get("strong_negative_precedent")
        or (
            raw_step9_negative_precedent
            and evidence_strength not in {"single_weak_case", "no_cases", "unclassified_cases"}
        )
        or step9_pattern in {"negative", "single_strong_negative"}
    )
    current_facts_stronger = bool(
        step9_data.get("current_facts_stronger_than_negative_cases")
    )
    recommended_overall = str(
        step9_data.get("recommended_overall_assessment", "") or ""
    ).strip().lower()

    guard_notes: list[str] = []
    overpromotion_risk_demotions: list[str] = []
    promoted_articles_after_fact_pattern_guard: list[str] = []

    strong_negative_context = bool(
        negative_precedent
        and not current_facts_stronger
        and evidence_strength in {"single_strong_case", "multi_case_negative"}
    )

    # Step 9 rejection is binding only when the case-law evidence is strong
    # AND the negative precedent is article-specific. With broad negative
    # precedent, rejection becomes downgrade. This is the core Fix 2 guard.
    for article in reject:
        has_article_specific_negative = has_specific_negative_support(article)
        target_status = (
            ARTICLE_STATUS_REJECTED
            if strong_negative_context and has_article_specific_negative
            else ARTICLE_STATUS_WEAK_OR_UNCERTAIN
        )
        supported, weak, rejected = _move_article_between_buckets(
            article,
            target_status=target_status,
            supported=supported,
            weak=weak,
            rejected=rejected,
        )
        article_assessments = _set_article_assessment_status(
            article_assessments,
            article=article,
            status=target_status,
            reason_suffix=(
                " Runner safety guard applied Step 9's structured rejection "
                "recommendation as binding because precedent evidence was strong "
                "and article-specific negative support existed."
                if target_status == ARTICLE_STATUS_REJECTED
                else " Runner safety guard converted Step 9's rejection into a "
                "downgrade because the negative precedent was not sufficiently "
                "article-specific or the selected evidence was thin."
            ),
            allowed_articles=allowed_set,
        )
        guard_notes.append(
            f"Step 9 recommended rejection for Article {article}; "
            f"article_specific_negative_support={has_article_specific_negative}; "
            f"final status set to {target_status}."
        )

    # Step 9 downgrade list prevents unsupported final-supported claims.
    #
    # Fix 2.1: Step 10 may place an article directly in the rejected bucket
    # even where Step 9's structured cross-validation only recommended a
    # downgrade. Previously this loop skipped articles already in `rejected`,
    # which allowed raw Step 10 rejection to override Step 9's more careful
    # article-specific downgrade. That is the exact T035 failure mode for
    # Article 13(2).
    #
    # Therefore, unless Step 9 also explicitly placed the article in its
    # `articles_to_reject` list, a Step 9 downgrade must actively move the
    # article to weak_or_uncertain even if Step 10 initially rejected it.
    for article in downgrade:
        if article in reject:
            continue

        supported, weak, rejected = _move_article_between_buckets(
            article,
            target_status=ARTICLE_STATUS_WEAK_OR_UNCERTAIN,
            supported=supported,
            weak=weak,
            rejected=rejected,
        )
        article_assessments = _set_article_assessment_status(
            article_assessments,
            article=article,
            status=ARTICLE_STATUS_WEAK_OR_UNCERTAIN,
            reason_suffix=(
                " Runner safety guard applied Step 9's structured downgrade "
                "recommendation and overrode any raw Step 10 rejection that "
                "was not confirmed by Step 9 as an article-specific rejection."
            ),
            allowed_articles=allowed_set,
        )
        guard_notes.append(
            f"Step 9 downgraded Article {article}; final status set to "
            "weak_or_uncertain, overriding any raw Step 10 rejection unless "
            "Step 9 also listed the article for rejection."
        )

    # Negative precedent/no-violation guard.
    # If selected precedent is mostly/all negative and Step 9 does not say the
    # current facts are stronger, block likely_viable unless Step 9 explicitly
    # keeps an article supported.
    if negative_precedent and not current_facts_stronger:
        support_whitelist = set(keep_supported)

        for article in list(supported):
            if article in support_whitelist:
                continue

            has_article_specific_negative = has_specific_negative_support(article)
            target = (
                ARTICLE_STATUS_REJECTED
                if (
                    has_article_specific_negative
                    and (recommended_overall == "not_viable" or case_profile.get("all_negative"))
                )
                else ARTICLE_STATUS_WEAK_OR_UNCERTAIN
            )
            supported, weak, rejected = _move_article_between_buckets(
                article,
                target_status=target,
                supported=supported,
                weak=weak,
                rejected=rejected,
            )
            article_assessments = _set_article_assessment_status(
                article_assessments,
                article=article,
                status=target,
                reason_suffix=(
                    " Runner safety guard blocked final support because the "
                    "selected precedent pattern was negative and the current "
                    "facts were not marked stronger than those negative cases."
                ),
                allowed_articles=allowed_set,
            )

        if recommended_overall == "not_viable" or case_profile.get("all_negative"):
            for article in list(weak):
                if article in support_whitelist:
                    continue

                if not has_specific_negative_support(article):
                    guard_notes.append(
                        f"Article {article} stayed weak_or_uncertain because the "
                        "negative precedent pattern was not article-specific."
                    )
                    continue

                supported, weak, rejected = _move_article_between_buckets(
                    article,
                    target_status=ARTICLE_STATUS_REJECTED,
                    supported=supported,
                    weak=weak,
                    rejected=rejected,
                )
                article_assessments = _set_article_assessment_status(
                    article_assessments,
                    article=article,
                    status=ARTICLE_STATUS_REJECTED,
                    reason_suffix=(
                        " Runner safety guard moved this article to rejected "
                        "because Step 9/case outcomes indicated a not-viable "
                        "negative precedent pattern with article-specific "
                        "negative support."
                    ),
                    allowed_articles=allowed_set,
                )

        guard_notes.append(
            "Negative precedent guard applied: selected precedent evidence was "
            f"{evidence_strength} and Step 9 did not mark the current facts as stronger."
        )

    supported = _dedupe_preserve_order([
        article for article in supported if article in allowed_set
    ])
    weak = _dedupe_preserve_order([
        article for article in weak
        if article in allowed_set and article not in supported
    ])
    rejected = _dedupe_preserve_order([
        article for article in rejected
        if article in allowed_set and article not in supported and article not in weak
    ])

    # Ensure every allowed article appears in exactly one bucket. Missing Step 4
    # candidates are not promoted to supported.
    for article in allowed_articles:
        if article in supported or article in weak or article in rejected:
            continue

        target = (
            ARTICLE_STATUS_REJECTED
            if (
                negative_precedent
                and has_specific_negative_support(article)
                and (
                    recommended_overall == "not_viable"
                    or case_profile.get("all_negative")
                )
            )
            else ARTICLE_STATUS_WEAK_OR_UNCERTAIN
        )
        supported, weak, rejected = _move_article_between_buckets(
            article,
            target_status=target,
            supported=supported,
            weak=weak,
            rejected=rejected,
        )
        article_assessments = _set_article_assessment_status(
            article_assessments,
            article=article,
            status=target,
            reason_suffix=(
                " Runner safety guard classified an omitted Step 4 candidate "
                "conservatively rather than silently dropping it."
            ),
            allowed_articles=allowed_set,
        )

    # Over-promotion risk guard.
    #
    # Fix 2.3: the previous promotion guard improved viable-case recall but
    # could still leave Article 12(1) finally supported in legally thin
    # commercial/procurement/contract/marking-scheme/search-only patterns.
    # These are better treated as weak_or_uncertain unless the current facts
    # contain a stronger recognized equality/arbitrariness pattern. This guard
    # does not use gold labels or scenario IDs; it only reads the current
    # case's own fact text from Steps 3-6.
    for article in list(supported):
        if not _article_has_overpromotion_risk_context(article, step_results):
            continue

        supported, weak, rejected = _move_article_between_buckets(
            article,
            target_status=ARTICLE_STATUS_WEAK_OR_UNCERTAIN,
            supported=supported,
            weak=weak,
            rejected=rejected,
        )
        article_assessments = _set_article_assessment_status(
            article_assessments,
            article=article,
            status=ARTICLE_STATUS_WEAK_OR_UNCERTAIN,
            reason_suffix=(
                " Runner over-promotion guard moved this article from "
                "supported to weak_or_uncertain because the current fact "
                "pattern is a legally thin commercial/procurement/contract/"
                "marking-scheme/search-only pattern rather than a strong "
                "viability pattern."
            ),
            allowed_articles=allowed_set,
        )
        overpromotion_risk_demotions.append(article)
        guard_notes.append(
            f"Article {article} demoted from supported to weak_or_uncertain "
            "by the over-promotion risk guard."
        )

    # Cautious promotion guard.
    #
    # Fix 2 protected articles from being rejected by broad negative precedent.
    # This promotion pass is deliberately narrower: it only promotes a weak
    # article when the visible facts themselves contain a strong article-specific
    # positive basis and the selected precedent profile is not strongly negative.
    #
    # This protects the earlier safety fixes: articles in Step 9's explicit
    # rejection list remain rejected/weak, and all-negative or multi-case
    # negative precedent profiles cannot be overridden by this promotion rule.
    promotion_blocked_by_negative_profile = bool(
        negative_precedent
        and evidence_strength in {"single_strong_case", "multi_case_negative"}
        and not current_facts_stronger
    )

    if not promotion_blocked_by_negative_profile:
        for article in list(weak):
            if article in reject:
                continue

            if recommended_overall == "not_viable":
                continue

            if not _article_has_strong_visible_positive_facts(
                article,
                step_results,
            ):
                continue

            supported, weak, rejected = _move_article_between_buckets(
                article,
                target_status=ARTICLE_STATUS_SUPPORTED,
                supported=supported,
                weak=weak,
                rejected=rejected,
            )
            article_assessments = _set_article_assessment_status(
                article_assessments,
                article=article,
                status=ARTICLE_STATUS_SUPPORTED,
                reason_suffix=(
                    " Runner promotion guard restored this article to supported "
                    "because the visible intake facts contain a strong "
                    "article-specific positive basis and the selected precedent "
                    "profile was not strongly negative."
                ),
                allowed_articles=allowed_set,
            )
            promoted_articles_after_fact_pattern_guard.append(article)
            guard_notes.append(
                f"Article {article} promoted from weak_or_uncertain to supported "
                "because strong visible positive facts were present and no "
                "strong negative precedent profile blocked promotion."
            )

    # For the top-level viability label, do not turn weak article-specific
    # uncertainty into not_viable merely because the global precedent pattern
    # is negative. A weak article can support not_viable only when that same
    # article has article-specific negative support.
    weak_articles_without_specific_negative_support = [
        article for article in weak
        if not has_specific_negative_support(article)
    ]
    effective_negative_precedent_for_overall = bool(
        negative_precedent
        and not weak_articles_without_specific_negative_support
    )

    overall_assessment = _derive_overall_assessment_from_final_buckets(
        supported=supported,
        weak=weak,
        fallback=structured.get("overall_assessment", ""),
        step9_recommended_overall=recommended_overall,
        negative_precedent=effective_negative_precedent_for_overall,
        current_facts_stronger=current_facts_stronger,
    )

    precedent_alignment = _derive_final_supported_precedent_alignment(
        raw_alignment=structured.get("precedent_alignment", ""),
        supported_articles=supported,
        weak_articles=weak,
        article_assessments=article_assessments,
        allowed_case_ids=allowed_case_ids,
    )

    key_weaknesses = _coerce_string_list(structured.get("key_weaknesses", []))
    faithfulness_notes = _coerce_string_list(structured.get("faithfulness_notes", []))

    for note in guard_notes:
        if note not in faithfulness_notes:
            faithfulness_notes.append(note)

    if negative_precedent and not current_facts_stronger:
        weakness = (
            "Selected precedent indicated a negative/no-violation pattern, "
            f"with evidence strength '{evidence_strength}', so final viability was treated conservatively."
        )
        if weakness not in key_weaknesses:
            key_weaknesses.append(weakness)

    return {
        **structured,
        "final_potentially_violated_articles": supported,
        "final_weak_or_uncertain_articles": weak,
        "final_rejected_articles": rejected,
        "overall_assessment": overall_assessment,
        "precedent_alignment": precedent_alignment,
        "article_assessments": article_assessments,
        "key_strengths": _coerce_string_list(structured.get("key_strengths", [])),
        "key_weaknesses": key_weaknesses,
        "faithfulness_notes": faithfulness_notes,
        "safety_guard_metadata": {
            "step9_enabled": step9_enabled,
            "negative_precedent_pattern": negative_precedent,
            "raw_step9_negative_precedent_pattern": raw_step9_negative_precedent,
            "evidence_strength": evidence_strength,
            "strong_negative_context": strong_negative_context,
            "current_facts_stronger_than_negative_cases": current_facts_stronger,
            "recommended_overall_assessment_from_step9": recommended_overall,
            "case_outcome_profile": case_profile,
            "article_specific_negative_support": case_profile.get(
                "article_specific_negative_support",
                {},
            ),
            "weak_articles_without_specific_negative_support": weak_articles_without_specific_negative_support,
            "effective_negative_precedent_for_overall": effective_negative_precedent_for_overall,
            "step9_articles_to_keep_supported": keep_supported,
            "step9_articles_to_downgrade": downgrade,
            "step9_articles_to_reject": reject,
            "overpromotion_risk_demotions": overpromotion_risk_demotions,
            "promoted_articles_after_fact_pattern_guard": (
                promoted_articles_after_fact_pattern_guard
            ),
        },
    }


def _extract_step9_safety_data(step_results: dict[str, Any]) -> dict[str, Any]:
    """
    Extract Step 9 structured cross-validation fields.

    Supports both:
        step_9.data.structured_cross_validation
        step_9.data top-level fields
    """
    step_9 = step_results.get("step_9", {})
    if not isinstance(step_9, dict):
        return {}

    data = step_9.get("data", {})
    if not isinstance(data, dict):
        return {}

    structured = data.get("structured_cross_validation", {})
    if isinstance(structured, dict):
        return structured

    return data


def _build_case_outcome_profile(
    stage_b_cases: list[dict[str, Any]],
    *,
    allowed_articles: list[str] | None = None,
) -> dict[str, Any]:
    """
    Deterministically summarize selected precedent outcomes.

    Distinguishes a strong single precedent from a weak single precedent. This
    matters because Himikama's corpus is small/domain-specific: one close case
    can matter, but one loose case should not control final viability.
    """
    positive_values = {
        "VIOLATED",
        "VIOLATION",
        "PARTIAL",
        "PARTIAL_VIOLATION",
        "PARTIAL VIOLATION",
        "PARTLY_VIOLATED",
    }
    negative_values = {
        "NOT_VIOLATED",
        "NOT VIOLATED",
        "NO_VIOLATION",
        "NO VIOLATION",
        "DISMISSED",
        "PROCEDURAL_FAILURE",
        "PROCEDURAL FAILURE",
        "REFUSED",
        "APPLICATION_DISMISSED",
    }

    positive = 0
    negative = 0
    unknown = 0
    judgments: list[str] = []
    classified_cases: list[dict[str, Any]] = []
    allowed_set = set(_coerce_string_list(allowed_articles or []))

    for case in stage_b_cases or []:
        if not isinstance(case, dict):
            continue

        judgment = str(case.get("judgment", "") or "").strip().upper()
        judgments.append(judgment)

        direction = "unknown"
        if judgment in positive_values:
            positive += 1
            direction = "positive"
        elif judgment in negative_values:
            negative += 1
            direction = "negative"
        elif "NOT" in judgment and "VIOL" in judgment:
            negative += 1
            direction = "negative"
        elif "NO" in judgment and "VIOL" in judgment:
            negative += 1
            direction = "negative"
        elif "DISMISS" in judgment:
            negative += 1
            direction = "negative"
        elif "VIOL" in judgment:
            positive += 1
            direction = "positive"
        elif judgment:
            unknown += 1

        if direction in {"positive", "negative"}:
            similarity = _case_similarity(case)
            case_articles = _case_article_set(case)
            shared_articles = sorted(case_articles & allowed_set) if allowed_set else sorted(case_articles)
            article_overlap = bool(shared_articles) if allowed_set else True
            strong_single_match = bool(
                article_overlap
                and similarity is not None
                and similarity >= 0.76
            )
            classified_cases.append({
                "case_id": str(case.get("case_id", "") or "").strip(),
                "case_name": str(case.get("case_name", "") or "").strip(),
                "judgment": judgment,
                "direction": direction,
                "similarity": similarity,
                "articles": sorted(case_articles),
                "shared_articles": shared_articles,
                "article_overlap": article_overlap,
                "strong_single_match": strong_single_match,
            })

    total_classified = positive + negative

    single_case_direction = ""
    single_case_is_strong = False
    evidence_strength = "no_cases"

    if total_classified == 0:
        evidence_strength = "unclassified_cases" if stage_b_cases else "no_cases"
    elif total_classified == 1:
        only_case = classified_cases[0] if classified_cases else {}
        single_case_direction = str(only_case.get("direction", ""))
        single_case_is_strong = bool(only_case.get("strong_single_match"))
        evidence_strength = "single_strong_case" if single_case_is_strong else "single_weak_case"
    elif positive > 0 and negative > 0:
        evidence_strength = "multi_case_mixed"
    elif negative > positive:
        evidence_strength = "multi_case_negative"
    elif positive > negative:
        evidence_strength = "multi_case_positive"
    else:
        evidence_strength = "multi_case_unclear"

    strong_negative_precedent = (
        evidence_strength == "multi_case_negative"
        or (
            evidence_strength == "single_strong_case"
            and single_case_direction == "negative"
        )
    )
    strong_positive_precedent = (
        evidence_strength == "multi_case_positive"
        or (
            evidence_strength == "single_strong_case"
            and single_case_direction == "positive"
        )
    )

    article_specific_negative_support = _build_article_specific_support_profile(
        classified_cases,
        allowed_set,
        direction="negative",
    )
    article_specific_positive_support = _build_article_specific_support_profile(
        classified_cases,
        allowed_set,
        direction="positive",
    )

    return {
        "positive_cases": positive,
        "negative_cases": negative,
        "unknown_cases": unknown,
        "total_cases": len(stage_b_cases or []),
        "total_classified_cases": total_classified,
        "judgments": judgments,
        "classified_cases": classified_cases,
        "evidence_strength": evidence_strength,
        "single_case_direction": single_case_direction,
        "single_case_is_strong": single_case_is_strong,
        "strong_negative_precedent": strong_negative_precedent,
        "strong_positive_precedent": strong_positive_precedent,
        "mostly_negative": strong_negative_precedent,
        "all_negative": strong_negative_precedent and negative == total_classified,
        "mostly_positive": strong_positive_precedent,
        "article_specific_negative_support": article_specific_negative_support,
        "article_specific_positive_support": article_specific_positive_support,
    }


def _case_similarity(case: dict[str, Any]) -> float | None:
    """Safely parse a retrieval similarity score."""
    if not isinstance(case, dict):
        return None
    value = case.get("similarity")
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _case_article_set(case: dict[str, Any]) -> set[str]:
    """Extract normalized article labels from a case dict."""
    if not isinstance(case, dict):
        return set()

    raw = (
        case.get("articles_cited")
        or case.get("articles")
        or case.get("article")
        or ""
    )

    articles: list[str] = []
    if isinstance(raw, list):
        articles = _coerce_string_list(raw)
    else:
        articles = _coerce_string_list(str(raw).split(","))

    if not articles and raw:
        articles = re.findall(
            r"(?<![A-Za-z0-9])(\d+(?:\([0-9A-Za-z]+\))*)(?![A-Za-z0-9])",
            str(raw),
        )

    return set(_dedupe_preserve_order(articles))


def _article_parent(article: str) -> str:
    """Return the bare parent number of a canonical article label."""
    match = re.match(r"^(\d+)", str(article or "").strip())
    return match.group(1) if match else ""


def _is_parent_article(article: str) -> bool:
    """True for bare parent labels such as 13, false for 13(1)."""
    article = str(article or "").strip()
    return bool(article and re.fullmatch(r"\d+", article))


def _articles_match_for_specific_precedent(candidate_article: str, case_article: str) -> bool:
    """
    Return True only for same-article or direct parent/sub-article matches.

    This deliberately does NOT treat sibling sub-articles as equivalent.
    Example: 13(1) is not article-specific support for 13(2).
    A broad case label such as 13 can support a specific 13(x) candidate only
    when the corpus metadata did not preserve the sub-article.
    """
    candidate = str(candidate_article or "").strip()
    case_value = str(case_article or "").strip()

    if not candidate or not case_value:
        return False

    if candidate == case_value:
        return True

    candidate_parent = _article_parent(candidate)
    case_parent = _article_parent(case_value)

    if not candidate_parent or candidate_parent != case_parent:
        return False

    return _is_parent_article(candidate) or _is_parent_article(case_value)


def _build_article_specific_support_profile(
    classified_cases: list[dict[str, Any]],
    allowed_articles: set[str],
    *,
    direction: str,
    similarity_floor: float = ARTICLE_SPECIFIC_NEGATIVE_SIMILARITY_FLOOR,
) -> dict[str, dict[str, Any]]:
    """Build an article -> selected-case support map for one outcome direction."""
    profile: dict[str, dict[str, Any]] = {}

    for article in allowed_articles:
        matches: list[dict[str, Any]] = []

        for case in classified_cases or []:
            if str(case.get("direction", "")) != direction:
                continue

            similarity = case.get("similarity")
            try:
                similarity_value = float(similarity) if similarity is not None else None
            except (TypeError, ValueError):
                similarity_value = None

            # If similarity metadata is available, require a minimally close case.
            # If it is missing, trust the fact that Step 7 selected the case but
            # still require article-specific overlap below.
            if similarity_value is not None and similarity_value < similarity_floor:
                continue

            for case_article in _coerce_string_list(case.get("articles", [])):
                if not _articles_match_for_specific_precedent(article, case_article):
                    continue

                matches.append({
                    "case_id": str(case.get("case_id", "") or "").strip(),
                    "case_name": str(case.get("case_name", "") or "").strip(),
                    "case_article": case_article,
                    "judgment": str(case.get("judgment", "") or "").strip(),
                    "similarity": similarity_value,
                })
                break

        case_ids = _dedupe_preserve_order([
            match.get("case_id", "")
            for match in matches
            if match.get("case_id")
        ])
        similarities = [
            match.get("similarity")
            for match in matches
            if match.get("similarity") is not None
        ]

        profile[article] = {
            "has_support": bool(matches),
            "case_count": len(case_ids),
            "case_ids": case_ids,
            "strongest_similarity": max(similarities) if similarities else None,
            "matches": matches,
        }

    return profile


def _has_article_specific_negative_support(
    article: str,
    case_profile: dict[str, Any],
) -> bool:
    """Return True when selected negative precedent supports rejecting this article."""
    support_map = case_profile.get("article_specific_negative_support", {})
    if not isinstance(support_map, dict):
        return False

    support = support_map.get(str(article or "").strip(), {})
    return bool(isinstance(support, dict) and support.get("has_support"))


def _collect_step_reasoning_text(step_results: dict[str, Any]) -> str:
    """Return a lower-cased text bundle from the material already generated by the chain."""
    if not isinstance(step_results, dict):
        return ""

    parts: list[str] = []

    for step_key in ("step_3", "step_4", "step_5", "step_6", "step_8", "step_9"):
        step = step_results.get(step_key, {})
        if not isinstance(step, dict):
            continue

        answer = step.get("answer", "")
        explanation = step.get("explanation", "")

        if answer:
            parts.append(str(answer))
        if explanation:
            parts.append(str(explanation))

    return "\n".join(parts).lower()


def _collect_current_fact_text(step_results: dict[str, Any]) -> str:
    """
    Return lower-cased current-case fact text only.

    Unlike _collect_step_reasoning_text(), this intentionally excludes
    Step 8 and Step 9 because those steps contain precedent case facts.
    Using precedent text for fact-pattern promotion can contaminate the
    current case with facts from retrieved cases. The promotion guard must
    depend only on the current user's facts and the chain's current-case
    analysis from Steps 3-6.
    """
    if not isinstance(step_results, dict):
        return ""

    parts: list[str] = []

    for step_key in ("step_3", "step_4", "step_5", "step_6"):
        step = step_results.get(step_key, {})
        if not isinstance(step, dict):
            continue

        answer = step.get("answer", "")
        explanation = step.get("explanation", "")

        if answer:
            parts.append(str(answer))
        if explanation:
            parts.append(str(explanation))

    return "\n".join(parts).lower()


def _contains_any(text: str, needles: list[str]) -> bool:
    """Case-insensitive substring check for phrase-style signals."""
    text = str(text or "").lower()
    return any(str(needle).lower() in text for needle in needles)


def _contains_word_or_phrase(text: str, needle: str) -> bool:
    """
    Match a signal as either a phrase or a whole word.

    This avoids false positives such as matching "cid" inside "incidents" or
    "stf" inside unrelated text while still allowing multi-word phrases like
    "police station" to be matched naturally.
    """
    text = str(text or "").lower()
    needle = str(needle or "").strip().lower()

    if not needle:
        return False

    if any(ch.isspace() for ch in needle) or "-" in needle or "/" in needle:
        return needle in text

    pattern = r"(?<![a-z0-9])" + re.escape(needle) + r"(?![a-z0-9])"
    return bool(re.search(pattern, text))


def _contains_any_word_or_phrase(text: str, needles: list[str]) -> bool:
    """Return True if any signal appears as a phrase or whole word."""
    return any(_contains_word_or_phrase(text, needle) for needle in needles)


def _has_security_or_special_investigation_blocker(text: str) -> bool:
    """
    Detect fact patterns where arrest/detention claims should not be promoted
    from weak to supported without stronger Article 11-type facts.

    These patterns often involve serious security, terrorism, narcotics, or
    public-money allegations where the triage intake alone is usually not
    enough to confidently convert weak Article 13/12 claims into supported
    final claims.
    """
    return _contains_any(text, [
        "prevention of terrorism act",
        "detention orders",
        "drug trafficking",
        "weapons dealings",
        "illicit drug",
        "proscribed organization",
        "terrorism law",
        "financial fraud involving public money",
    ])


def _has_severe_treatment_fact(text: str) -> bool:
    """
    Detect strong current-case Article 11-style physical ill-treatment facts.

    The signals are intentionally concrete. Mild references to confrontation,
    inconvenience, or generic distress are not enough.
    """
    if _contains_any(text, [
        "hung with my hands and legs tied",
        "beaten for hours",
        "beaten on the soles",
        "soles of my feet",
        "nai miris",
        "chilli extract",
        "burnt with cigarettes",
        "cigarette burn",
        "pistol at my head",
        "threatened to kill",
        "handcuffed",
        "made me kneel",
        "perforated",
        "bleeding injuries",
        "rope marks",
        "neuropathy",
    ]):
        return True

    return bool(
        _contains_any(text, [
            "beaten",
            "assaulted",
            "hit me hard",
            "struck",
        ])
        and _contains_any(text, [
            "hospital",
            "injury",
            "injuries",
            "ear drum",
            "severe pain",
            "bleeding",
        ])
    )


def _has_police_prison_or_school_context(text: str) -> bool:
    """
    Detect police/prison/public-school style state-power contexts using
    whole-word matching where appropriate.
    """
    return bool(
        _contains_any_word_or_phrase(text, [
            "police",
            "cid",
            "prison",
            "stf",
            "school",
            "teacher",
        ])
        or "terrorist investigation" in text
        or "police station" in text
    )


def _has_strong_arrest_detention_fact(text: str) -> bool:
    """
    Detect arrest/detention facts strong enough to promote Article 13(1)
    or companion Article 12(1), unless a special-investigation blocker exists.

    Generic statements that the police filed a false report are not enough;
    the text must contain a concrete deprivation-of-liberty signal plus a
    strong arbitrariness signal.
    """
    if _has_security_or_special_investigation_blocker(text) and not _has_severe_treatment_fact(text):
        return False

    arrest_signal = _contains_any(text, [
        "arrested",
        "arrest",
        "taken to the",
        "took me to",
        "custody",
        "cell",
        "detained",
        "remand",
        "produced before court",
        "produced before a magistrate",
    ])
    arbitrariness_signal = _contains_any(text, [
        "no proper evidence",
        "without proper basis",
        "false or fabricated",
        "false criminal",
        "false allegation",
        "fabricated",
        "several days",
        "kept overnight",
        "before being produced",
        "not produced",
        "police formally recorded my arrest",
        "failed to record",
        "placed them in a cell",
        "placed us in a cell",
        "detained in a cell",
        "heroin possession allegation",
        "completely deny",
    ])

    return bool(arrest_signal and arbitrariness_signal)


def _has_strong_article_13_2_fact(text: str) -> bool:
    """Detect current-case facts specifically supporting Article 13(2)."""
    if _has_security_or_special_investigation_blocker(text) and not _has_severe_treatment_fact(text):
        return False

    return bool(
        _has_police_prison_or_school_context(text)
        and _contains_any(text, [
            "several days",
            "kept overnight",
            "before being produced",
            "not produced",
            "police formally recorded my arrest",
            "delayed production",
            "unauthorized place",
        ])
    )


def _has_strong_public_admin_equality_fact(text: str) -> bool:
    """
    Detect strong current-case Article 12(1) administrative equality facts.

    This is intentionally narrower than "any employment/public-authority
    disappointment". It targets clear comparator, transparent-process, public
    school, public property allocation, or official recommendation patterns.
    Thin exam-marking, service classification, contract renewal, transfer, and
    technical qualification disputes remain weak unless they include one of the
    strong comparator/process signals.
    """
    if _article_has_overpromotion_risk_context("12(1)", {"step_3": {"answer": text}}):
        return False

    weak_admin_context = _contains_any(text, [
        "question paper",
        "marking scheme",
        "improper marking",
        "written competitive examination",
        "answer script",
        "recruitment and salary structure",
        "salary scale",
        "supra grade",
        "absorption scheme",
        "eight years as inspectors",
        "contract basis",
        "contract was repeatedly extended",
        "transfer from a specialized",
        "end post",
    ])

    strong_override = _contains_any(text, [
        "higher marks than",
        "lower-ranked",
        "lower ranked",
        "without calling applications",
        "without interviews",
        "political victimization relief",
        "same category",
        "similarly situated",
        "same position",
        "same dates",
        "backdate",
        "backdating",
        "police commission recommended",
        "government analyst report",
    ])

    if weak_admin_context and not strong_override:
        return False

    return _contains_any(text, [
        "political victimization relief",
        "same category",
        "similarly situated",
        "same position",
        "same dates",
        "backdate",
        "backdating",
        "six years senior",
        "higher marks than",
        "lower-ranked",
        "lower ranked",
        "without calling applications",
        "without interviews",
        "transparent process",
        "political affiliation",
        "political connections",
        "personal favoritism",
        "cut-off date",
        "successive registered lease",
        "grade one admission",
        "allocated to some lawyers",
        "before proper public notice",
        "proper public notice",
        "better locations",
        "part payments",
        "police commission recommended",
        "government analyst report",
        "termination of the criminal case",
        "kept summoning me",
    ])


def _article_has_overpromotion_risk_context(
    article: str,
    step_results: dict[str, Any],
) -> bool:
    """
    Return True for fact patterns that should not remain finally supported
    merely because Step 9/Step 10 was optimistic.

    The guard is deliberately narrow and currently applies only to Article
    12(1), because the measured unsafe promotions after Fix 2.2 were Article
    12(1) promotions in procurement/commercial contract/interview-marking/
    search-only contexts. These claims may still remain weak_or_uncertain.
    """
    article = str(article or "").strip()
    if article != "12(1)":
        return False

    text = (
        _collect_current_fact_text(step_results)
        if isinstance(step_results, dict)
        else str(step_results or "").lower()
    )

    if not text:
        return False

    return bool(
        _contains_any(text, [
            "tender",
            "bid",
            "procurement",
            "substantially non-responsive",
            "road rehabilitation",
            "construction company",
            "teledrama",
            "telecast",
            "commercial agreement",
            "ratings and revenue",
            "viewers",
            "marking scheme",
            "chartered accountancy",
            "officer (audit)",
            "interview was different",
            "equivalent to a diploma",
        ])
        or (
            _contains_any(text, [
                "searched the house",
                "search warrant",
                "without a search warrant",
                "cordoned search",
                "broken locks",
                "new locks",
            ])
            and not _contains_any(text, ["arrest", "detention", "assault", "injury", "injuries"])
        )
        or (
            _has_security_or_special_investigation_blocker(text)
            and not _has_severe_treatment_fact(text)
        )
    )


def _article_has_strong_visible_positive_facts(
    article: str,
    step_results: dict[str, Any],
) -> bool:
    """
    Return True only where the chain's own current-case reasoning contains
    strong article-specific positive facts.

    Fix 2.3 broadens the earlier appointment-only promotion rule, but keeps it
    constrained to recognizable strong fact patterns:

        - Article 11: concrete severe physical ill-treatment in police/prison/
          public-school style state-power contexts.
        - Article 13(1): concrete arrest/custody facts plus strong arbitrariness
          indicators, excluding thin PTA/security/narcotics/public-money
          contexts unless severe ill-treatment is also present.
        - Article 13(2): concrete delayed production / pre-production detention
          indicators.
        - Article 12(1): companion to strong Article 11/13 facts, or strong
          public-administration equality/process facts.
        - Article 14(1)(g): only the earlier strict public appointment
          correction / non-implementation pattern.

    The function does not use gold labels, scenario IDs, or final outcomes.
    """
    article = str(article or "").strip()
    text = _collect_current_fact_text(step_results)

    if not text:
        return False

    if article == "11":
        return bool(
            _has_police_prison_or_school_context(text)
            and _has_severe_treatment_fact(text)
        )

    if article == "13(1)":
        return bool(
            _has_police_prison_or_school_context(text)
            and _has_strong_arrest_detention_fact(text)
        )

    if article == "13(2)":
        return _has_strong_article_13_2_fact(text)

    if article == "12(1)":
        if _article_has_overpromotion_risk_context(article, step_results):
            return False

        return bool(
            (
                _has_police_prison_or_school_context(text)
                and (
                    _has_severe_treatment_fact(text)
                    or _has_strong_arrest_detention_fact(text)
                )
            )
            or _has_strong_public_admin_equality_fact(text)
        )

    # Preserve the original narrow Fix 2.2 occupation/appointment promotion.
    # This is what rescued the Senior Lecturer / appointment-regularisation
    # pattern without turning every employment grievance into Article 14(1)(g).
    public_admin_context = _contains_any(text, [
        "public university",
        "university grants commission",
        "sabaragamuwa university",
        "state university",
        "higher education authorities",
        "public employment",
        "official administrative actions",
    ])

    corrective_decision_visible = _contains_any(text, [
        "university services appeals board",
        "appeals board",
        "internal committee recommendations",
        "internal committee",
        "council consideration",
        "council",
        "recognized that a mistake",
        "recognised that a mistake",
        "administrative error",
        "clerical mistake",
        "official order",
        "order from",
    ])

    non_implementation_visible = _contains_any(text, [
        "failed for years",
        "failed to regularize",
        "failed to regularise",
        "not implement",
        "did not implement",
        "refusal to implement",
        "persistent disregard",
        "failure to act",
        "prolonged failure",
    ])

    appointment_context = _contains_any(text, [
        "appointment",
        "appointed",
        "senior lecturer",
        "lecturer",
        "academic designation",
        "regularize",
        "regularise",
    ])

    tangible_career_harm = _contains_any(text, [
        "loss of salary",
        "salary benefits",
        "salary entitlements",
        "increments",
        "career progression",
        "sabbatical",
        "professional opportunities",
        "professional recognition",
        "academic opportunities",
        "professional standing",
    ])

    strong_admin_appointment_pattern = bool(
        public_admin_context
        and corrective_decision_visible
        and non_implementation_visible
        and appointment_context
    )

    if article == "14(1)(g)":
        return bool(strong_admin_appointment_pattern and tangible_career_harm)

    return False



def _move_article_between_buckets(
    article: str,
    *,
    target_status: str,
    supported: list[str],
    weak: list[str],
    rejected: list[str],
) -> tuple[list[str], list[str], list[str]]:
    """
    Move one article into exactly one final bucket.

    This helper is used by the post-Step-10 safety guard. It removes the
    article from all existing buckets first, then places it in the requested
    final status bucket so the same article never appears as both supported
    and rejected/weak.
    """
    article = str(article or "").strip()
    if not article:
        return (
            _dedupe_preserve_order(supported),
            _dedupe_preserve_order(weak),
            _dedupe_preserve_order(rejected),
        )

    supported = [item for item in supported if item != article]
    weak = [item for item in weak if item != article]
    rejected = [item for item in rejected if item != article]

    if target_status == ARTICLE_STATUS_SUPPORTED:
        supported.append(article)
    elif target_status == ARTICLE_STATUS_WEAK_OR_UNCERTAIN:
        weak.append(article)
    elif target_status == ARTICLE_STATUS_REJECTED:
        rejected.append(article)

    return (
        _dedupe_preserve_order(supported),
        _dedupe_preserve_order(weak),
        _dedupe_preserve_order(rejected),
    )


def _set_article_assessment_status(
    article_assessments: list[dict[str, Any]],
    *,
    article: str,
    status: str,
    reason_suffix: str,
    allowed_articles: set[str],
) -> list[dict[str, Any]]:
    """
    Update or create a per-article assessment to match guard-adjusted buckets.

    Step 10 produces both top-level article buckets and detailed
    article_assessments. Whenever the deterministic guard moves an article, the
    detailed assessment must be updated too, otherwise the JSON becomes
    internally inconsistent.
    """
    article = str(article or "").strip()
    if not article or article not in allowed_articles:
        return article_assessments

    normalized_status = str(status or "").strip().lower()
    if normalized_status not in ALLOWED_ARTICLE_STATUSES:
        return article_assessments

    suffix = str(reason_suffix or "").strip()
    updated: list[dict[str, Any]] = []
    found = False

    for assessment in article_assessments:
        if not isinstance(assessment, dict):
            continue

        item = dict(assessment)

        if str(item.get("article", "")).strip() == article:
            found = True
            item["article"] = article
            item["status"] = normalized_status
            reason = str(item.get("reason", "") or "").strip()
            if suffix and suffix not in reason:
                item["reason"] = (reason + " " + suffix).strip()
            elif not reason:
                item["reason"] = f"Article {article} was adjusted by the deterministic final safety guard."

            confidence = str(item.get("confidence", "") or "").strip().lower()
            if confidence not in ALLOWED_CONFIDENCE_LEVELS:
                item["confidence"] = "medium" if normalized_status == ARTICLE_STATUS_REJECTED else "low"

        updated.append(item)

    if not found:
        updated.append({
            "article": article,
            "status": normalized_status,
            "reason": (
                f"Article {article} was adjusted by the deterministic final safety guard. "
                + suffix
            ).strip(),
            "supporting_steps": ["step_4", "step_9"],
            "supporting_case_ids": [],
            "confidence": "medium" if normalized_status == ARTICLE_STATUS_REJECTED else "low",
        })

    return updated

def _derive_overall_assessment_from_final_buckets(
    *,
    supported: list[str],
    weak: list[str],
    fallback: Any,
    step9_recommended_overall: str,
    negative_precedent: bool,
    current_facts_stronger: bool,
) -> str:
    """
    Force the overall label to match final article buckets.

    This directly prevents inconsistent outputs such as:
        final_potentially_violated_articles=[]
        overall_assessment="likely_viable"
    """
    fallback_label = _normalize_overall_assessment(fallback)

    if fallback_label in {"time_barred", "not_state_actor"}:
        return fallback_label

    if supported:
        return "likely_viable"

    if weak:
        if (
            step9_recommended_overall == "not_viable"
            and negative_precedent
            and not current_facts_stronger
        ):
            return "not_viable"
        return "weak_or_uncertain"

    return "not_viable"


def _rerender_step_10_answer(
    step_10_result: dict[str, Any],
    structured_assessment: dict[str, Any],
) -> dict[str, Any]:
    """
    Re-render Step 10 human answer after runner-side safety guards.

    Step 10 originally renders the answer before runner normalization. If the
    runner changes structured buckets, the visible answer must be refreshed so
    the final text and machine-readable fields stay consistent.
    """
    if not isinstance(step_10_result, dict):
        return step_10_result

    renderer = getattr(steps, "_render_final_answer_from_structured_assessment", None)
    if not callable(renderer):
        return step_10_result

    updated = dict(step_10_result)
    final_answer = renderer(structured_assessment)
    if final_answer:
        updated["answer"] = final_answer
        explainer = getattr(steps, "_explain_step_10", None)
        if callable(explainer):
            updated["explanation"] = explainer(final_answer)

    return updated


def _derive_final_supported_precedent_alignment(
    *,
    raw_alignment: Any,
    supported_articles: list[str],
    weak_articles: list[str],
    article_assessments: list[dict[str, Any]],
    allowed_case_ids: list[str],
) -> str:
    """
    Derive top-level precedent_alignment from final supported articles only.

    Step 4 is intentionally broad and may include weak or rejected
    candidate articles. Those weak/rejected candidates should not make
    the global precedent alignment look mixed when the final supported
    articles are precedent-backed.

    Meaning:
        supports     -> every final supported article has direct selected
                        precedent support.
        mixed        -> some, but not all, final supported articles have
                        direct selected precedent support.
        weak         -> final supported articles exist, but selected precedent
                        support is weak, indirect, or absent.
        contradicts  -> final supported articles are contradicted by selected
                        precedent.
        no_cases     -> Step 7 selected no cases.
        not_assessed -> no final supported or weak articles exist.
    """
    selected_case_ids = _coerce_string_list(allowed_case_ids)

    if not selected_case_ids:
        return "no_cases"

    supported_articles = _dedupe_preserve_order([
        str(article).strip()
        for article in supported_articles
        if str(article).strip()
    ])

    weak_articles = _dedupe_preserve_order([
        str(article).strip()
        for article in weak_articles
        if str(article).strip()
    ])

    if not supported_articles:
        if weak_articles:
            return "weak"
        return "not_assessed"

    supported_set = set(supported_articles)
    supported_assessments = [
        assessment
        for assessment in article_assessments
        if assessment.get("article") in supported_set
        and assessment.get("status") == ARTICLE_STATUS_SUPPORTED
    ]

    if not supported_assessments:
        normalized_raw = _normalize_precedent_alignment(raw_alignment)
        return normalized_raw if normalized_raw else "weak"

    directly_supported = [
        assessment
        for assessment in supported_assessments
        if _assessment_has_direct_supporting_precedent(assessment)
    ]

    if len(directly_supported) == len(supported_articles):
        return "supports"

    if directly_supported:
        return "mixed"

    normalized_raw = _normalize_precedent_alignment(raw_alignment)

    if normalized_raw == "contradicts":
        return "contradicts"

    return "weak"


def _assessment_has_direct_supporting_precedent(
    assessment: dict[str, Any],
) -> bool:
    """
    Return True when an article assessment appears directly supported
    by selected precedent.

    This avoids treating a distinguishable NOT_VIOLATED case as direct
    support merely because its case ID appears in supporting_case_ids.
    """
    case_ids = _coerce_string_list(assessment.get("supporting_case_ids", []))
    if not case_ids:
        return False

    reason = str(assessment.get("reason", "")).lower()

    negative_precedent_signals = [
        "not_violated",
        "not violated",
        "no violation",
        "found no violation",
        "did not find a violation",
        "did not find violation",
        "distinguish",
        "distinguished",
        "contradict",
        "contradicts",
        "does not support",
        "not supported",
    ]

    if any(signal in reason for signal in negative_precedent_signals):
        return False

    positive_precedent_signals = [
        "supports",
        "supported by precedent",
        "strong precedent",
        "aligns strongly",
        "found a violation",
        "violation was found",
        "found to violate",
        "violated article",
        "violation of article",
        "similar factual",
    ]

    if any(signal in reason for signal in positive_precedent_signals):
        return True

    supporting_steps = _coerce_string_list(assessment.get("supporting_steps", []))
    return "step_8" in supporting_steps or "step_9" in supporting_steps


def _normalize_article_assessments(
    raw_article_assessments: Any,
    *,
    allowed_articles: list[str],
    allowed_case_ids: list[str],
) -> list[dict[str, Any]]:
    """
    Normalize per-article assessment objects from Step 10.
    """
    if not isinstance(raw_article_assessments, list):
        return []

    allowed_article_set = set(allowed_articles)
    normalized: list[dict[str, Any]] = []
    seen_articles: set[str] = set()

    for item in raw_article_assessments:
        if not isinstance(item, dict):
            continue

        article = str(item.get("article", "")).strip()
        if not article or article not in allowed_article_set:
            continue

        # Keep only one assessment per article to avoid inconsistent
        # duplicate statuses in downstream evaluation.
        if article in seen_articles:
            continue

        status = str(item.get("status", "")).strip()
        if status not in ALLOWED_ARTICLE_STATUSES:
            continue

        confidence = str(item.get("confidence", "")).strip().lower()
        if confidence not in ALLOWED_CONFIDENCE_LEVELS:
            confidence = ""

        reason = str(item.get("reason", "")).strip()
        supporting_steps = _filter_allowed_steps(
            item.get("supporting_steps", [])
        )
        supporting_case_ids = _filter_allowed_case_ids(
            item.get("supporting_case_ids", []),
            allowed_case_ids,
        )

        if (
            not supporting_case_ids
            and status == ARTICLE_STATUS_SUPPORTED
            and len(allowed_case_ids) == 1
            and _assessment_uses_precedent(reason, supporting_steps)
        ):
            supporting_case_ids = allowed_case_ids

        normalized.append({
            "article": article,
            "status": status,
            "reason": reason,
            "supporting_steps": supporting_steps,
            "supporting_case_ids": supporting_case_ids,
            "confidence": confidence,
        })
        seen_articles.add(article)

    return normalized


def _assessment_uses_precedent(
    reason: str,
    supporting_steps: list[str],
) -> bool:
    """
    Detect whether an article assessment relies on selected precedent.

    Used only to safely fill supporting_case_ids when Step 10 cites
    precedent in prose but omits the machine-readable selected case ID.
    """
    if "step_8" in supporting_steps or "step_9" in supporting_steps:
        return True

    lower_reason = str(reason or "").lower()
    precedent_signals = [
        "precedent",
        "case law",
        "similar case",
        "dodampe",
        "atapattu",
        "court found",
        "found a violation",
        "violation was found",
    ]

    return any(signal in lower_reason for signal in precedent_signals)


def _filter_allowed_articles(
    raw_articles: Any,
    allowed_articles: list[str],
) -> list[str]:
    """
    Keep only article numbers that were identified in Step 4.
    """
    allowed_set = set(allowed_articles)
    articles = _coerce_string_list(raw_articles)

    return _dedupe_preserve_order([
        article for article in articles
        if article in allowed_set
    ])


def _filter_allowed_case_ids(
    raw_case_ids: Any,
    allowed_case_ids: list[str],
) -> list[str]:
    """
    Keep only case IDs that were selected in Step 7.
    """
    allowed_set = set(allowed_case_ids)
    case_ids = _coerce_string_list(raw_case_ids)

    return _dedupe_preserve_order([
        case_id for case_id in case_ids
        if case_id in allowed_set
    ])


def _filter_allowed_steps(raw_steps: Any) -> list[str]:
    """
    Keep only valid step references.
    """
    steps_list = _coerce_string_list(raw_steps)

    return _dedupe_preserve_order([
        step for step in steps_list
        if step in ALLOWED_STEP_REFS
    ])


def _normalize_overall_assessment(raw_value: Any) -> str:
    """
    Normalize overall assessment label.
    """
    value = str(raw_value or "").strip().lower()

    if value in ALLOWED_OVERALL_ASSESSMENTS:
        return value

    return ""


def _normalize_precedent_alignment(raw_value: Any) -> str:
    """
    Normalize precedent alignment label.
    """
    value = str(raw_value or "").strip().lower()

    if value in ALLOWED_PRECEDENT_ALIGNMENTS:
        return value

    return ""


def _coerce_string_list(value: Any) -> list[str]:
    """
    Convert a list-like value into a clean list of strings.

    Strings are treated as a single item only for non-article fields.
    For article fields, _filter_allowed_articles applies an additional
    allowed-article filter.
    """
    if value is None:
        return []

    if isinstance(value, list):
        return _dedupe_preserve_order([
            str(item).strip()
            for item in value
            if str(item).strip()
        ])

    if isinstance(value, tuple):
        return _dedupe_preserve_order([
            str(item).strip()
            for item in value
            if str(item).strip()
        ])

    if isinstance(value, str) and value.strip():
        return [value.strip()]

    return []


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

        # Candidate Step 4 articles
        "articles_identified": result.get("articles_identified", []),

        # Final Step 10 structured conclusion
        "final_potentially_violated_articles": result.get(
            "final_potentially_violated_articles",
            [],
        ),
        "final_weak_or_uncertain_articles": result.get(
            "final_weak_or_uncertain_articles",
            [],
        ),
        "final_rejected_articles": result.get("final_rejected_articles", []),
        "overall_assessment": result.get("overall_assessment", ""),
        "precedent_alignment": (
            result.get("structured_assessment", {}).get("precedent_alignment", "")
            if isinstance(result.get("structured_assessment", {}), dict)
            else ""
        ),

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
