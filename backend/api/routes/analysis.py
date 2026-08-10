"""Authenticated Himikama analysis, history, result, and trace routes."""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    Request,
    status,
)

from api.auth import (
    AuthenticatedUser,
    require_active_user,
    require_assessment_consent_user,
)
from api.config import config
from api.firebase import (
    AttemptConflictError,
    AttemptNotRetryableError,
    create_attempt,
    delete_attempt,
    delete_user_history,
    get_attempt,
    get_user_history,
    retry_attempt,
)
from api.schemas import (
    AnalyzeRequest,
    StructureIntakeRequest,
    StructureIntakeResponse,
)
from chain.intake import (
    IntakeExtractionError,
    build_clarifying_questions,
    check_missing_required,
    extract_intake,
)


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/analysis", tags=["analysis"])

STEP_TITLES = {
    "step_1": "Timeliness check",
    "step_2": "State actor check",
    "step_3": "Understanding your situation",
    "step_4": "Identifying your rights",
    "step_5": "Nature of the violation",
    "step_6": "Intent and harm",
    "step_7": "Finding similar cases",
    "step_8": "Precedent analysis",
    "step_9": "Cross-validation",
    "step_10": "Final synthesis",
}

TERMINAL_ATTEMPT_STATUSES = {
    "complete",
    "time_barred",
    "not_state_actor",
    "failed",
}


@router.post(
    "/structure-intake",
    response_model=StructureIntakeResponse,
)
async def structure_intake(
    intake_request: StructureIntakeRequest,
    http_request: Request,
    user: AuthenticatedUser = Depends(require_assessment_consent_user),
) -> StructureIntakeResponse:
    """Extract objective facts for user review without legal analysis."""
    del user
    request_id = getattr(http_request.state, "request_id", "unknown")
    try:
        intake, confirmation_text = await extract_intake(
            intake_request.raw_user_description
        )
    except (RuntimeError, ValueError) as exc:
        failure_reason = (
            exc.reason
            if isinstance(exc, IntakeExtractionError)
            else "model_call_failed"
        )
        logger.warning(
            "Intake extraction failed request_id=%s "
            "failure_reason=%s error_type=%s",
            request_id,
            failure_reason,
            type(exc).__name__,
        )
        raise HTTPException(
            status_code=502,
            detail=(
                "The situation could not be structured reliably. "
                "No analysis was started. Please try again."
            ),
        ) from None

    missing_fields = check_missing_required(intake)
    can_confirm = not missing_fields
    return StructureIntakeResponse(
        success=True,
        status=(
            "needs_confirmation"
            if can_confirm
            else "needs_clarification"
        ),
        can_confirm=can_confirm,
        intake=intake,
        confirmation_text=confirmation_text,
        missing_required_fields=missing_fields,
        clarifying_questions=build_clarifying_questions(missing_fields),
    )


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


def extract_structured_assessment(source: dict[str, Any]) -> dict[str, Any]:
    step_results = _safe_dict(source.get("step_results"))
    step_10_data = _safe_dict(
        _safe_dict(step_results.get("step_10")).get("data")
    )
    confidence_evaluation = _safe_dict(
        _safe_dict(source.get("confidence")).get("evaluation")
    )
    candidates = (
        source.get("structured_assessment"),
        step_10_data.get("structured_assessment"),
        confidence_evaluation.get("structured_assessment"),
    )
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


def extract_final_fields(source: dict[str, Any]) -> dict[str, Any]:
    structured = extract_structured_assessment(source)
    final_fields = {
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
    structured.update(final_fields)
    return {"structured_assessment": structured, **final_fields}


def extract_similar_cases(step_results: dict[str, Any]) -> list[dict[str, Any]]:
    step_7_data = _safe_dict(_safe_dict(step_results.get("step_7")).get("data"))
    cases: list[dict[str, Any]] = []
    for case in _safe_list(step_7_data.get("stage_b_cases")):
        if not isinstance(case, dict):
            continue
        cases.append(
            {
                "case_id": case.get("case_id"),
                "case_name": case.get("case_name"),
                "case_number": case.get("case_number"),
                "year": case.get("year"),
                "judgment": case.get("judgment"),
                "articles_cited": case.get("articles_cited"),
            }
        )
    return cases


def build_reasoning_trace(step_results: dict[str, Any]) -> list[dict[str, Any]]:
    """Return an owner-only, UI-safe trace without raw internal data blocks."""
    trace: list[dict[str, Any]] = []
    for step_key in STEP_TITLES:
        step = step_results.get(step_key)
        if not isinstance(step, dict):
            continue
        trace.append(
            {
                "step": step_key,
                "title": STEP_TITLES[step_key],
                "passed": bool(step.get("passed", True)),
                "explanation": str(step.get("explanation") or ""),
                "details": str(step.get("answer") or ""),
            }
        )
    return trace


def build_main_response(
    *,
    attempt_id: str,
    source: dict[str, Any],
) -> dict[str, Any]:
    step_results = _safe_dict(source.get("step_results"))
    confidence = _safe_dict(source.get("confidence"))
    final_fields = extract_final_fields(source)
    attempt_status = str(source.get("status") or "").strip().lower()
    return {
        "success": attempt_status != "failed",
        "attempt_id": attempt_id,
        "status": attempt_status,
        "is_terminal": attempt_status in TERMINAL_ATTEMPT_STATUSES,
        "error_code": (
            str(source.get("error_code") or "")
            if attempt_status == "failed"
            else ""
        ),
        "main_answer": source.get("final_answer_with_disclaimer", ""),
        "confidence": {
            "level": source.get("confidence_level", ""),
            "flags": _safe_list(source.get("flags")),
            "explanation": confidence.get("explanation", ""),
            "flag_details": _safe_dict(confidence.get("flag_details")),
        },
        "summary": {
            "articles_identified": _safe_list(
                source.get("articles_identified")
            ),
            **final_fields,
            "similar_case_ids": _safe_list(source.get("similar_case_ids")),
            "similar_cases": extract_similar_cases(step_results),
        },
        "reasoning_available": bool(step_results),
        "timestamps": {
            "started_at": source.get("started_at"),
            "completed_at": source.get("completed_at"),
        },
    }


@router.post("/analyze", status_code=status.HTTP_202_ACCEPTED)
async def analyze(
    analysis_request: AnalyzeRequest,
    http_request: Request,
    user: AuthenticatedUser = Depends(require_assessment_consent_user),
) -> dict[str, Any]:
    """Persist an idempotent attempt for the durable worker to process."""
    request_id = getattr(http_request.state, "request_id", "unknown")
    try:
        intake = analysis_request.intake.model_dump(mode="json")
        submission = await create_attempt(
            user_id=user.uid,
            intake_object=intake,
            attempt_id=analysis_request.attempt_id,
        )
    except AttemptConflictError:
        raise HTTPException(
            status_code=409,
            detail="This attempt ID is already associated with different data",
        ) from None
    except PermissionError:
        raise HTTPException(
            status_code=403,
            detail="An active account is required to start an assessment",
        ) from None
    except Exception as exc:
        logger.exception(
            "Could not create analysis attempt request_id=%s error_type=%s",
            request_id,
            type(exc).__name__,
        )
        raise HTTPException(
            status_code=500,
            detail="The analysis could not be started",
        ) from None

    logger.info(
        "Accepted durable analysis request_id=%s attempt_id=%s created=%s",
        request_id,
        submission.attempt_id,
        submission.created,
    )
    return {
        "success": True,
        "attempt_id": submission.attempt_id,
        "status": submission.status,
        "idempotent_replay": not submission.created,
        "poll_url": f"/analysis/attempts/{submission.attempt_id}",
    }


@router.post("/attempts/{attempt_id}/retry", status_code=status.HTTP_202_ACCEPTED)
async def retry_saved_attempt(
    attempt_id: UUID,
    http_request: Request,
    user: AuthenticatedUser = Depends(require_assessment_consent_user),
) -> dict[str, Any]:
    """Requeue one owned failed attempt without generating another ID."""
    request_id = getattr(http_request.state, "request_id", "unknown")
    try:
        submission = await retry_attempt(
            user_id=user.uid,
            attempt_id=attempt_id,
        )
    except FileNotFoundError:
        raise HTTPException(404, "Attempt not found") from None
    except AttemptNotRetryableError:
        raise HTTPException(
            409,
            "Only a failed attempt can be retried",
        ) from None
    except (AttemptConflictError, PermissionError):
        raise HTTPException(403, "The attempt could not be retried") from None
    except Exception as exc:
        logger.exception(
            "Could not retry attempt request_id=%s attempt_id=%s "
            "error_type=%s",
            request_id,
            attempt_id,
            type(exc).__name__,
        )
        raise HTTPException(500, "The attempt could not be retried") from None

    logger.info(
        "Requeued failed analysis request_id=%s attempt_id=%s",
        request_id,
        submission.attempt_id,
    )
    return {
        "success": True,
        "attempt_id": submission.attempt_id,
        "status": submission.status,
        "poll_url": f"/analysis/attempts/{submission.attempt_id}",
    }


@router.get("/attempts/{attempt_id}/trace")
async def get_trace(
    attempt_id: UUID,
    user: AuthenticatedUser = Depends(require_active_user),
) -> dict[str, Any]:
    try:
        attempt = await get_attempt(user_id=user.uid, attempt_id=attempt_id)
        return {
            "success": True,
            "attempt_id": str(attempt_id),
            "status": attempt.get("status"),
            "reasoning_trace": build_reasoning_trace(
                _safe_dict(attempt.get("step_results"))
            ),
        }
    except FileNotFoundError:
        raise HTTPException(404, "Attempt not found") from None
    except Exception as exc:
        logger.exception("Could not retrieve reasoning trace: %s", type(exc).__name__)
        raise HTTPException(500, "Could not retrieve the reasoning trace") from None


@router.get("/attempts/{attempt_id}")
async def get_saved_attempt(
    attempt_id: UUID,
    user: AuthenticatedUser = Depends(require_active_user),
) -> dict[str, Any]:
    try:
        attempt = await get_attempt(user_id=user.uid, attempt_id=attempt_id)
        return build_main_response(attempt_id=str(attempt_id), source=attempt)
    except FileNotFoundError:
        raise HTTPException(404, "Attempt not found") from None
    except Exception as exc:
        logger.exception("Could not retrieve attempt: %s", type(exc).__name__)
        raise HTTPException(500, "Could not retrieve the attempt") from None


@router.delete("/attempts/{attempt_id}")
async def delete_saved_attempt(
    attempt_id: UUID,
    user: AuthenticatedUser = Depends(require_active_user),
) -> dict[str, bool]:
    try:
        await delete_attempt(user_id=user.uid, attempt_id=attempt_id)
        return {"success": True}
    except FileNotFoundError:
        raise HTTPException(404, "Attempt not found") from None
    except Exception as exc:
        logger.exception("Could not delete attempt: %s", type(exc).__name__)
        raise HTTPException(500, "Could not delete the assessment") from None


@router.get("/history")
async def history(
    limit: int = Query(default=20, ge=1, le=config.history_max_limit),
    user: AuthenticatedUser = Depends(require_active_user),
) -> dict[str, Any]:
    try:
        items = await get_user_history(user_id=user.uid, limit=limit)
        return {"success": True, "items": items}
    except Exception as exc:
        logger.exception("Could not retrieve history: %s", type(exc).__name__)
        raise HTTPException(500, "Could not retrieve analysis history") from None


@router.delete("/history")
async def clear_history(
    user: AuthenticatedUser = Depends(require_active_user),
) -> dict[str, bool]:
    try:
        await delete_user_history(user_id=user.uid)
        return {"success": True}
    except Exception as exc:
        logger.exception("Could not clear history: %s", type(exc).__name__)
        raise HTTPException(500, "Could not clear assessment history") from None


@router.post("/validate-intake")
async def validate_intake(
    analysis_request: AnalyzeRequest,
    user: AuthenticatedUser = Depends(require_assessment_consent_user),
) -> dict[str, Any]:
    del user
    return {
        "valid": True,
        "intake": analysis_request.intake.model_dump(mode="json"),
    }
