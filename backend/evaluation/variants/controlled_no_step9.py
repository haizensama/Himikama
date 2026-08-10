"""
himikama/backend/evaluation/variants/controlled_no_step9.py
═══════════════════════════════════════════════════════════════
Controlled Himikama chain without Step 9.

Purpose:
    Ablation study to measure the contribution of Step 9
    cross-validation.

Runs:
    Step 1
    Step 2
    Step 3
    Step 4
    Step 5
    Step 6
    Step 7
    Step 8
    Step 10
    Confidence layer

Skips:
    Step 9 — Cross-validation

Important:
    This file is for evaluation only.
    It should not replace the production chain.runner.py.

Structured Output:
    This variant now returns the same final structured fields as
    full_himikama so the three variants can be compared fairly:

        structured_assessment
        final_potentially_violated_articles
        final_weak_or_uncertain_articles
        final_rejected_articles
        overall_assessment
        precedent_alignment
        article_assessments

    Step 4 articles remain available as articles_identified, but
    they are candidate/debug articles, not the final metric.
═══════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any

from api.config import config
from chain.confidence import apply_confidence_layer
from chain.steps import (
    run_step_1,
    run_step_2,
    run_step_3,
    run_step_4,
    run_step_5,
    run_step_6,
    run_step_7,
    run_step_8,
    run_step_10,
    _render_final_answer_from_structured_assessment,
    _explain_step_10,
)
from ingestion.embedder import get_article_collection, get_case_collection

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────────────────────

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
# BASIC HELPERS
# ─────────────────────────────────────────────────────────────

def _utc_now_iso() -> str:
    """
    Current UTC timestamp as ISO string.
    """
    return datetime.now(timezone.utc).isoformat()


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


def _coerce_string_list(value: Any) -> list[str]:
    """
    Convert list-like values into a clean list[str].
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


def _safe_step_data(
    step_results: dict[str, Any],
    step_key: str,
) -> dict[str, Any]:
    """
    Safely read step_results[step_key]["data"].
    """
    step = step_results.get(step_key, {})
    if not isinstance(step, dict):
        return {}

    data = step.get("data", {})
    return data if isinstance(data, dict) else {}


# ─────────────────────────────────────────────────────────────
# STEP FIELD EXTRACTION
# ─────────────────────────────────────────────────────────────

def _extract_articles_from_step_4(
    step_results: dict[str, Any],
) -> list[str]:
    """
    Safely extract articles identified by Step 4.

    These are candidate articles, not final conclusions.
    """
    step_4_data = _safe_step_data(step_results, "step_4")
    articles = step_4_data.get("articles_identified", [])

    return _coerce_string_list(articles)


def _extract_case_ids_from_step_7(
    step_results: dict[str, Any],
) -> list[str]:
    """
    Safely extract selected similar case IDs from Step 7.
    """
    step_7_data = _safe_step_data(step_results, "step_7")
    case_ids = step_7_data.get("case_ids", [])

    return _coerce_string_list(case_ids)


def _extract_stage_b_cases_from_step_7(
    step_results: dict[str, Any],
) -> list[dict[str, Any]]:
    """
    Safely extract Stage B cases from Step 7.
    """
    step_7_data = _safe_step_data(step_results, "step_7")
    cases = step_7_data.get("stage_b_cases", [])

    if not isinstance(cases, list):
        return []

    return [case for case in cases if isinstance(case, dict)]


def _extract_structured_assessment_from_step_10(
    step_10_result: dict[str, Any],
) -> dict[str, Any]:
    """
    Extract raw Step 10 structured assessment if available.
    """
    if not isinstance(step_10_result, dict):
        return {}

    data = step_10_result.get("data", {})
    if not isinstance(data, dict):
        return {}

    structured = data.get("structured_assessment", {})
    return structured if isinstance(structured, dict) else {}


def _build_all_answers(
    step_results: dict[str, Any],
) -> dict[str, str]:
    """
    Build the all_answers object expected by run_step_10().

    run_step_10 signature:
        run_step_10(
            intake: dict,
            all_answers: dict,
            allowed_articles: list[str] | None = None,
            selected_case_ids: list[str] | None = None,
        )

    It expects a dictionary of previous step answers, not the full
    step_results object. The no-Step-9 ablation also passes the exact
    Step 4 article list and Step 7 case ID list into Step 10, matching
    the full Himikama runner.
    """
    all_answers: dict[str, str] = {}

    for step_key, step in step_results.items():
        if not isinstance(step, dict):
            continue

        all_answers[step_key] = str(step.get("answer", ""))

    return all_answers


# ─────────────────────────────────────────────────────────────
# STRUCTURED ASSESSMENT NORMALIZATION
# ─────────────────────────────────────────────────────────────

def _empty_structured_assessment() -> dict[str, Any]:
    """
    Empty structured assessment with expected fields.
    """
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


def _build_early_termination_structured_assessment(status: str) -> dict[str, Any]:
    """
    Build deterministic structured assessment for hard-gate failures.
    """
    if status == "time_barred":
        overall_assessment = "time_barred"
        weakness = (
            "The analysis stopped because the incident appears to fall "
            "outside the 30-day Fundamental Rights filing window."
        )
    elif status == "not_state_actor":
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
            "by the no-Step-9 evaluation variant because the chain "
            "terminated before Step 10."
        ],
    }


def _normalize_structured_assessment(
    raw: dict[str, Any],
    *,
    allowed_articles: list[str],
    allowed_case_ids: list[str],
    stage_b_cases: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """
    Normalize and safety-filter Step 10 structured assessment.

    Enforced rules:
        1. Only articles already found in Step 4 are allowed.
        2. Only case IDs already selected in Step 7 are allowed.
        3. Only statuses supported | weak_or_uncertain | rejected are allowed.
    """
    if not isinstance(raw, dict):
        raw = {}

    allowed_articles = _dedupe_preserve_order([
        str(article)
        for article in allowed_articles
        if article
    ])
    allowed_case_ids = _dedupe_preserve_order([
        str(case_id)
        for case_id in allowed_case_ids
        if case_id
    ])
    article_specific_negative_support = _build_article_specific_negative_support_profile(
        stage_b_cases or [],
        allowed_articles,
    )

    def has_specific_negative_support(article: str) -> bool:
        support = article_specific_negative_support.get(str(article or "").strip(), {})
        return bool(isinstance(support, dict) and support.get("has_support"))

    article_assessments = _normalize_article_assessments(
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

    # Keep top-level buckets consistent with article_assessments.
    for assessment in article_assessments:
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
    # Priority: supported > weak_or_uncertain > rejected.
    weak_articles = [
        article for article in _dedupe_preserve_order(weak_articles)
        if article not in supported_articles
    ]
    rejected_articles = [
        article for article in _dedupe_preserve_order(rejected_articles)
        if article not in supported_articles and article not in weak_articles
    ]

    # Fix 2 for the no-Step-9 ablation: if Step 10 tries to reject an article
    # based only on broad negative precedent, convert that rejection to
    # weak_or_uncertain unless a selected negative case actually concerns the
    # same specific article or a direct parent/sub-article equivalent.
    protected_rejected_articles: list[str] = []
    converted_rejections: list[str] = []
    for article in rejected_articles:
        if has_specific_negative_support(article):
            protected_rejected_articles.append(article)
        else:
            converted_rejections.append(article)
            weak_articles.append(article)
            article_assessments = _set_article_assessment_status(
                article_assessments,
                article=article,
                status=ARTICLE_STATUS_WEAK_OR_UNCERTAIN,
                reason_suffix=(
                    " No-Step-9 Fix 2 guard converted rejection to "
                    "weak_or_uncertain because selected negative precedent was "
                    "not article-specific."
                ),
                allowed_articles=set(allowed_articles),
            )

    weak_articles = [
        article for article in _dedupe_preserve_order(weak_articles)
        if article not in supported_articles
    ]
    rejected_articles = [
        article for article in _dedupe_preserve_order(protected_rejected_articles)
        if article not in supported_articles and article not in weak_articles
    ]

    overall_assessment = str(raw.get("overall_assessment", "") or "").strip().lower()
    if overall_assessment not in ALLOWED_OVERALL_ASSESSMENTS:
        overall_assessment = ""

    precedent_alignment = str(
        raw.get("precedent_alignment", "") or ""
    ).strip().lower()
    if precedent_alignment not in ALLOWED_PRECEDENT_ALIGNMENTS:
        precedent_alignment = ""

    overall_assessment = _derive_overall_assessment_from_final_buckets(
        supported=supported_articles,
        weak=weak_articles,
        fallback=overall_assessment,
    )

    return {
        "final_potentially_violated_articles": supported_articles,
        "final_weak_or_uncertain_articles": weak_articles,
        "final_rejected_articles": rejected_articles,
        "overall_assessment": overall_assessment,
        "precedent_alignment": precedent_alignment,
        "article_assessments": article_assessments,
        "key_strengths": _coerce_string_list(raw.get("key_strengths", [])),
        "key_weaknesses": _coerce_string_list(raw.get("key_weaknesses", [])),
        "faithfulness_notes": _coerce_string_list(
            raw.get("faithfulness_notes", [])
        ),
        "safety_guard_metadata": {
            "step9_enabled": False,
            "article_specific_negative_support": article_specific_negative_support,
            "converted_rejections_without_article_specific_negative_support": converted_rejections,
        },
    }


def _derive_overall_assessment_from_final_buckets(
    *,
    supported: list[str],
    weak: list[str],
    fallback: Any,
) -> str:
    """
    Keep no-Step-9 output internally consistent.

    This does not add Step 9 safety. It only prevents contradictory outputs
    such as no supported articles with overall_assessment="likely_viable".
    """
    fallback_label = str(fallback or "").strip().lower()

    if fallback_label in {"time_barred", "not_state_actor"}:
        return fallback_label

    if supported:
        return "likely_viable"

    if weak:
        return "weak_or_uncertain"

    return "not_viable"


def _rerender_step_10_answer(
    step_10_result: dict[str, Any],
    structured_assessment: dict[str, Any],
) -> dict[str, Any]:
    """
    Re-render Step 10 answer after no-Step-9 normalization so visible text
    matches machine-readable fields.
    """
    if not isinstance(step_10_result, dict):
        return step_10_result

    updated = dict(step_10_result)
    final_answer = _render_final_answer_from_structured_assessment(
        structured_assessment
    )

    if final_answer:
        updated["answer"] = final_answer
        updated["explanation"] = _explain_step_10(final_answer)

    return updated


def _normalize_article_assessments(
    raw_article_assessments: Any,
    *,
    allowed_articles: list[str],
    allowed_case_ids: list[str],
) -> list[dict[str, Any]]:
    """
    Normalize per-article assessment objects.
    """
    if not isinstance(raw_article_assessments, list):
        return []

    allowed_article_set = set(allowed_articles)
    allowed_case_id_set = set(allowed_case_ids)

    normalized: list[dict[str, Any]] = []
    seen_articles: set[str] = set()

    for item in raw_article_assessments:
        if not isinstance(item, dict):
            continue

        article = str(item.get("article", "")).strip()
        if not article or article not in allowed_article_set:
            continue

        if article in seen_articles:
            continue

        status = str(item.get("status", "")).strip().lower()
        if status not in ALLOWED_ARTICLE_STATUSES:
            continue

        confidence = str(item.get("confidence", "") or "").strip().lower()
        if confidence not in ALLOWED_CONFIDENCE_LEVELS:
            confidence = ""

        normalized.append({
            "article": article,
            "status": status,
            "reason": str(item.get("reason", "") or "").strip(),
            "supporting_steps": _filter_allowed_steps(
                item.get("supporting_steps", [])
            ),
            "supporting_case_ids": _filter_allowed_case_ids(
                item.get("supporting_case_ids", []),
                allowed_case_id_set,
            ),
            "confidence": confidence,
        })
        seen_articles.add(article)

    return normalized


def _case_similarity(case: dict[str, Any]) -> float | None:
    """Safely parse retrieval similarity metadata."""
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
    """Extract normalized article labels from a selected case."""
    if not isinstance(case, dict):
        return set()

    raw = (
        case.get("articles_cited")
        or case.get("articles")
        or case.get("article")
        or ""
    )

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
    match = re.match(r"^(\d+)", str(article or "").strip())
    return match.group(1) if match else ""


def _is_parent_article(article: str) -> bool:
    article = str(article or "").strip()
    return bool(article and re.fullmatch(r"\d+", article))


def _articles_match_for_specific_precedent(candidate_article: str, case_article: str) -> bool:
    """Same article or direct parent/sub-article only; no sibling matches."""
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


def _is_negative_case(case: dict[str, Any]) -> bool:
    judgment = str(case.get("judgment", "") or "").strip().upper()
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
    return (
        judgment in negative_values
        or ("NOT" in judgment and "VIOL" in judgment)
        or ("NO" in judgment and "VIOL" in judgment)
        or "DISMISS" in judgment
    )


def _build_article_specific_negative_support_profile(
    stage_b_cases: list[dict[str, Any]],
    allowed_articles: list[str],
) -> dict[str, dict[str, Any]]:
    """Build article-specific negative precedent support map."""
    allowed = _dedupe_preserve_order([
        str(article).strip()
        for article in allowed_articles
        if str(article).strip()
    ])
    profile: dict[str, dict[str, Any]] = {}

    for article in allowed:
        matches: list[dict[str, Any]] = []
        for case in stage_b_cases or []:
            if not isinstance(case, dict) or not _is_negative_case(case):
                continue
            similarity = _case_similarity(case)
            if similarity is not None and similarity < ARTICLE_SPECIFIC_NEGATIVE_SIMILARITY_FLOOR:
                continue
            for case_article in _case_article_set(case):
                if not _articles_match_for_specific_precedent(article, case_article):
                    continue
                matches.append({
                    "case_id": str(case.get("case_id", "") or "").strip(),
                    "case_name": str(case.get("case_name", "") or "").strip(),
                    "case_article": case_article,
                    "judgment": str(case.get("judgment", "") or "").strip(),
                    "similarity": similarity,
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


def _move_article_between_buckets(
    article: str,
    *,
    target_status: str,
    supported: list[str],
    weak: list[str],
    rejected: list[str],
) -> tuple[list[str], list[str], list[str]]:
    article = str(article or "").strip()
    supported = [item for item in supported if item != article]
    weak = [item for item in weak if item != article]
    rejected = [item for item in rejected if item != article]
    if not article:
        return supported, weak, rejected
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
    article = str(article or "").strip()
    if not article or article not in allowed_articles:
        return article_assessments
    if status not in ALLOWED_ARTICLE_STATUSES:
        return article_assessments

    updated: list[dict[str, Any]] = []
    found = False
    suffix = str(reason_suffix or "").strip()
    for assessment in article_assessments:
        if not isinstance(assessment, dict):
            continue
        item = dict(assessment)
        if str(item.get("article", "")).strip() == article:
            found = True
            item["status"] = status
            reason = str(item.get("reason", "") or "").strip()
            if suffix and suffix not in reason:
                item["reason"] = (reason + " " + suffix).strip()
        updated.append(item)

    if not found:
        updated.append({
            "article": article,
            "status": status,
            "reason": (
                f"Article {article} was adjusted by the no-Step-9 Fix 2 guard. "
                + suffix
            ).strip(),
            "supporting_steps": ["step_4", "step_8"],
            "supporting_case_ids": [],
            "confidence": "low",
        })

    return updated


def _filter_allowed_articles(
    raw_articles: Any,
    allowed_articles: list[str],
) -> list[str]:
    """
    Keep only articles from Step 4.
    """
    allowed_set = set(allowed_articles)
    articles = _coerce_string_list(raw_articles)

    return _dedupe_preserve_order([
        article for article in articles
        if article in allowed_set
    ])


def _filter_allowed_case_ids(
    raw_case_ids: Any,
    allowed_case_ids: set[str],
) -> list[str]:
    """
    Keep only case IDs from Step 7.
    """
    case_ids = _coerce_string_list(raw_case_ids)

    return _dedupe_preserve_order([
        case_id for case_id in case_ids
        if case_id in allowed_case_ids
    ])


def _filter_allowed_steps(raw_steps: Any) -> list[str]:
    """
    Keep only valid step references.

    In this variant, step_9 can appear only to indicate that it was skipped.
    """
    steps = _coerce_string_list(raw_steps)

    return _dedupe_preserve_order([
        step for step in steps
        if step in ALLOWED_STEP_REFS
    ])


def _attach_structured_assessment_to_step_10(
    step_10_result: dict[str, Any],
    structured_assessment: dict[str, Any],
) -> dict[str, Any]:
    """
    Attach normalized structured assessment into Step 10 data.
    """
    if not isinstance(step_10_result, dict):
        step_10_result = {
            "step": "step_10",
            "answer": "",
            "explanation": "",
            "passed": True,
            "data": {},
        }

    updated = dict(step_10_result)
    data = updated.get("data", {})

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
    data["overall_assessment"] = structured_assessment.get(
        "overall_assessment",
        "",
    )
    data["precedent_alignment"] = structured_assessment.get(
        "precedent_alignment",
        "",
    )

    updated["data"] = data
    return updated


def _copy_structured_assessment_to_result(
    result: dict[str, Any],
    structured_assessment: dict[str, Any],
) -> dict[str, Any]:
    """
    Copy final structured fields to top-level result.
    """
    result["structured_assessment"] = structured_assessment
    result["final_potentially_violated_articles"] = _coerce_string_list(
        structured_assessment.get("final_potentially_violated_articles", [])
    )
    result["final_weak_or_uncertain_articles"] = _coerce_string_list(
        structured_assessment.get("final_weak_or_uncertain_articles", [])
    )
    result["final_rejected_articles"] = _coerce_string_list(
        structured_assessment.get("final_rejected_articles", [])
    )
    result["overall_assessment"] = str(
        structured_assessment.get("overall_assessment", "") or ""
    ).strip()
    result["precedent_alignment"] = str(
        structured_assessment.get("precedent_alignment", "") or ""
    ).strip()
    result["article_assessments"] = [
        item
        for item in structured_assessment.get("article_assessments", [])
        if isinstance(item, dict)
    ]

    return result


# ─────────────────────────────────────────────────────────────
# CONFIDENCE
# ─────────────────────────────────────────────────────────────

def _apply_confidence_to_result(
    result: dict[str, Any],
) -> dict[str, Any]:
    """
    Run deterministic confidence evaluation and attach the output.

    apply_confidence_layer() returns a ConfidenceResult dataclass,
    so we convert it to dict using .to_dict().
    """
    confidence_result = apply_confidence_layer(
        step_results=result.get("step_results", {}),
        articles_identified=result.get("articles_identified", []),
        final_answer=result.get("final_answer", ""),
    )

    confidence = confidence_result.to_dict()

    result["confidence"] = confidence
    result["confidence_level"] = confidence.get("confidence_level", "")
    result["flags"] = confidence.get("flags", [])
    result["final_answer_with_disclaimer"] = confidence.get(
        "final_answer_with_disclaimer",
        result.get("final_answer", ""),
    )

    # Keep the confidence-layer status because it may mark chain_incomplete.
    result["confidence_status"] = confidence.get("status", "")

    return result


# ─────────────────────────────────────────────────────────────
# PARTIAL RESULT
# ─────────────────────────────────────────────────────────────

def _build_partial_result(
    *,
    status: str,
    step_results: dict[str, Any],
    error: str | None,
    started_at: str,
) -> dict[str, Any]:
    """
    Build a consistent result object for early exits or errors.
    """
    articles_identified = _extract_articles_from_step_4(step_results)
    similar_case_ids = _extract_case_ids_from_step_7(step_results)

    if status in {"time_barred", "not_state_actor"}:
        structured_assessment = _build_early_termination_structured_assessment(
            status
        )
    else:
        structured_assessment = _empty_structured_assessment()

    result = {
        "variant": "controlled_no_step9",
        "status": status,
        "step_results": step_results,
        "final_answer": "",
        "final_answer_with_disclaimer": "",
        "structured_assessment": structured_assessment,
        "final_potentially_violated_articles": [],
        "final_weak_or_uncertain_articles": [],
        "final_rejected_articles": [],
        "overall_assessment": structured_assessment.get(
            "overall_assessment",
            "",
        ),
        "precedent_alignment": structured_assessment.get(
            "precedent_alignment",
            "",
        ),
        "article_assessments": [],
        "confidence": {},
        "flags": [],
        "confidence_level": "",
        "articles_identified": articles_identified,
        "similar_case_ids": similar_case_ids,
        "retrieved_articles": [],
        "retrieved_cases": _extract_stage_b_cases_from_step_7(step_results),
        "error": error,
        "started_at": started_at,
        "completed_at": _utc_now_iso(),
    }

    result = _copy_structured_assessment_to_result(
        result,
        structured_assessment,
    )

    try:
        result = _apply_confidence_to_result(result)
    except Exception as e:
        logger.warning(
            "Confidence evaluation failed for partial no-Step-9 result: %s",
            e,
        )

    return result


# ─────────────────────────────────────────────────────────────
# PUBLIC RUNNER
# ─────────────────────────────────────────────────────────────

async def run_controlled_chain_no_step9(
    intake: dict[str, Any],
) -> dict[str, Any]:
    """
    Run the controlled Himikama chain while skipping Step 9.

    This should be used only for evaluation/ablation.
    """

    started_at = _utc_now_iso()
    step_results: dict[str, Any] = {}

    try:
        article_collection = get_article_collection(config.db_path)
        case_collection = get_case_collection(config.db_path)

        # ─────────────────────────────────────────────────────
        # Step 1 — Timeliness hard gate
        # ─────────────────────────────────────────────────────
        step_1 = await run_step_1(intake)
        step_results["step_1"] = step_1

        if not step_1.get("passed", False):
            return _build_partial_result(
                status="time_barred",
                step_results=step_results,
                error=None,
                started_at=started_at,
            )

        # ─────────────────────────────────────────────────────
        # Step 2 — State actor hard gate
        # ─────────────────────────────────────────────────────
        step_2 = await run_step_2(intake, article_collection)
        step_results["step_2"] = step_2

        if not step_2.get("passed", False):
            return _build_partial_result(
                status="not_state_actor",
                step_results=step_results,
                error=None,
                started_at=started_at,
            )

        # ─────────────────────────────────────────────────────
        # Step 3 — Fact clarification
        # ─────────────────────────────────────────────────────
        step_3 = await run_step_3(intake)
        step_results["step_3"] = step_3

        # ─────────────────────────────────────────────────────
        # Step 4 — Rights identification
        # ─────────────────────────────────────────────────────
        step_4 = await run_step_4(intake, article_collection)
        step_results["step_4"] = step_4

        articles_from_step_4 = _extract_articles_from_step_4(step_results)

        # ─────────────────────────────────────────────────────
        # Step 5 — Nature of violation
        # ─────────────────────────────────────────────────────
        step_5 = await run_step_5(intake, article_collection)
        step_results["step_5"] = step_5

        # ─────────────────────────────────────────────────────
        # Step 6 — Intent and harm
        # ─────────────────────────────────────────────────────
        step_6 = await run_step_6(intake)
        step_results["step_6"] = step_6

        # ─────────────────────────────────────────────────────
        # Step 7 — Similar cases
        # ─────────────────────────────────────────────────────
        step_7 = await run_step_7(
            intake=intake,
            case_collection=case_collection,
            articles_from_step_4=articles_from_step_4,
        )
        step_results["step_7"] = step_7

        similar_case_ids = _extract_case_ids_from_step_7(step_results)
        stage_b_cases = _extract_stage_b_cases_from_step_7(step_results)

        # ─────────────────────────────────────────────────────
        # Step 8 — Precedent analysis
        # ─────────────────────────────────────────────────────
        step_8 = await run_step_8(intake, stage_b_cases)
        step_results["step_8"] = step_8

        # ─────────────────────────────────────────────────────
        # Step 9 — intentionally skipped for ablation
        # ─────────────────────────────────────────────────────
        step_results["step_9"] = {
            "step": "step_9",
            "answer": (
                "Step 9 cross-validation was intentionally skipped for "
                "ablation evaluation."
            ),
            "explanation": (
                "Cross-validation was disabled in this evaluation variant."
            ),
            "passed": True,
            "data": {
                "skipped": True,
                "ablation": "controlled_no_step9",
                # Important:
                # Do not set inconsistencies_found=False here.
                # We want the evaluation to know Step 9 did not actually run.
                "consistent": None,
                "inconsistencies_found": None,
            },
        }

        # ─────────────────────────────────────────────────────
        # Step 10 — Final synthesis
        # ─────────────────────────────────────────────────────
        all_answers = _build_all_answers(step_results)

        # Match the full Himikama runner's Step 10 call.
        # The only intended ablation difference is that Step 9's real
        # cross-validation answer is replaced by the skipped-step marker
        # above. Step 10 must still receive the exact Step 4 articles and
        # Step 7 selected case IDs so final structured outputs are
        # comparable with full_himikama.
        try:
            step_10 = await run_step_10(
                intake,
                all_answers,
                allowed_articles=articles_from_step_4,
                selected_case_ids=similar_case_ids,
            )
        except TypeError as e:
            # Backward-compatible fallback, mirroring chain.runner.py.
            # This should not run once steps.py supports allowed_articles
            # and selected_case_ids.
            if (
                "allowed_articles" not in str(e)
                and "selected_case_ids" not in str(e)
                and "unexpected keyword argument" not in str(e)
            ):
                raise

            logger.warning(
                "run_step_10 does not accept allowed_articles/selected_case_ids. "
                "Falling back to legacy Step 10 call."
            )
            step_10 = await run_step_10(intake, all_answers)

        raw_structured_assessment = _extract_structured_assessment_from_step_10(
            step_10
        )
        structured_assessment = _normalize_structured_assessment(
            raw_structured_assessment,
            allowed_articles=articles_from_step_4,
            allowed_case_ids=similar_case_ids,
            stage_b_cases=stage_b_cases,
        )

        step_10 = _attach_structured_assessment_to_step_10(
            step_10,
            structured_assessment,
        )
        step_10 = _rerender_step_10_answer(
            step_10,
            structured_assessment,
        )
        step_results["step_10"] = step_10

        final_answer = str(step_10.get("answer", ""))

        result = {
            "variant": "controlled_no_step9",
            "status": "complete",
            "step_results": step_results,
            "final_answer": final_answer,
            "final_answer_with_disclaimer": "",
            "structured_assessment": structured_assessment,
            "final_potentially_violated_articles": [],
            "final_weak_or_uncertain_articles": [],
            "final_rejected_articles": [],
            "overall_assessment": "",
            "precedent_alignment": "",
            "article_assessments": [],
            "confidence": {},
            "flags": [],
            "confidence_level": "",
            "articles_identified": articles_from_step_4,
            "similar_case_ids": similar_case_ids,
            "retrieved_articles": (
                step_4.get("data", {}).get("retrieved_articles", [])
                if isinstance(step_4, dict)
                else []
            ),
            "retrieved_cases": stage_b_cases,
            "error": None,
            "started_at": started_at,
            "completed_at": _utc_now_iso(),
        }

        result = _copy_structured_assessment_to_result(
            result,
            structured_assessment,
        )

        result = _apply_confidence_to_result(result)

        return result

    except Exception as e:
        logger.exception("Controlled no-Step-9 variant failed")

        return _build_partial_result(
            status="failed",
            step_results=step_results,
            error=str(e),
            started_at=started_at,
        )
