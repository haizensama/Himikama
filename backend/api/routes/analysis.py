"""
himikama/backend/api/routes/analysis.py
═══════════════════════════════════════════════════════════════
Analysis routes with Firestore persistence.

Important:
    POST /analysis/analyze runs the chain once.
    GET /analysis/attempts/{attempt_id}/trace only reads saved data.
    It does NOT rerun the chain.
═══════════════════════════════════════════════════════════════
"""

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from api.firebase import (
    create_attempt,
    get_attempt,
    get_user_history,
    mark_attempt_failed,
    save_attempt_result,
)
from api.schemas import AnalyzeRequest
from chain.runner import run_full_chain

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/analysis",
    tags=["analysis"],
)


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


def build_reasoning_trace(step_results: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Convert saved step_results into a UI-friendly reasoning trace.

    This function only reshapes saved data.
    It never calls Gemini and never reruns the chain.
    """
    trace: list[dict[str, Any]] = []

    for step_key in [
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
    ]:
        step = step_results.get(step_key)

        if not isinstance(step, dict):
            continue

        trace.append({
            "step": step_key,
            "title": STEP_TITLES.get(step_key, step_key),
            "passed": step.get("passed", True),
            "explanation": step.get("explanation", ""),
            "details": step.get("answer", ""),
            "data": step.get("data", {}),
        })

    return trace


def extract_similar_cases(step_results: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Extract selected Stage B cases from saved Step 7 data.
    """
    step_7 = step_results.get("step_7", {})
    step_7_data = step_7.get("data", {}) if isinstance(step_7, dict) else {}
    stage_b_cases = step_7_data.get("stage_b_cases", [])

    cases: list[dict[str, Any]] = []

    for case in stage_b_cases:
        if not isinstance(case, dict):
            continue

        cases.append({
            "case_id": case.get("case_id"),
            "case_name": case.get("case_name"),
            "case_number": case.get("case_number"),
            "year": case.get("year"),
            "judgment": case.get("judgment"),
            "articles_cited": case.get("articles_cited"),
        })

    return cases


def build_main_response(
    *,
    user_id: str,
    attempt_id: str,
    result: dict[str, Any],
) -> dict[str, Any]:
    """
    Build compact response for the main result screen.

    Does not include full reasoning trace.
    Flutter fetches trace later using attempt_id.
    """
    step_results = result.get("step_results", {})
    confidence = result.get("confidence", {})

    return {
        "success": result.get("status") != "failed",
        "user_id": user_id,
        "attempt_id": attempt_id,
        "status": result.get("status"),
        "main_answer": result.get("final_answer_with_disclaimer", ""),
        "confidence": {
            "level": result.get("confidence_level", ""),
            "flags": result.get("flags", []),
            "explanation": confidence.get("explanation", ""),
            "flag_details": confidence.get("flag_details", {}),
        },
        "summary": {
            "articles_identified": result.get("articles_identified", []),
            "similar_case_ids": result.get("similar_case_ids", []),
            "similar_cases": extract_similar_cases(step_results),
        },
        "reasoning_available": bool(step_results),
        "timestamps": {
            "started_at": result.get("started_at"),
            "completed_at": result.get("completed_at"),
        },
    }


def build_main_response_from_attempt(
    *,
    user_id: str,
    attempt_id: str,
    attempt: dict[str, Any],
) -> dict[str, Any]:
    """
    Build compact response from already-saved Firestore attempt.
    """
    step_results = attempt.get("step_results", {})
    confidence = attempt.get("confidence", {})

    return {
        "success": attempt.get("status") != "failed",
        "user_id": user_id,
        "attempt_id": attempt_id,
        "status": attempt.get("status"),
        "main_answer": attempt.get("final_answer_with_disclaimer", ""),
        "confidence": {
            "level": attempt.get("confidence_level", ""),
            "flags": attempt.get("flags", []),
            "explanation": confidence.get("explanation", ""),
            "flag_details": confidence.get("flag_details", {}),
        },
        "summary": {
            "articles_identified": attempt.get("articles_identified", []),
            "similar_case_ids": attempt.get("similar_case_ids", []),
            "similar_cases": extract_similar_cases(step_results),
        },
        "reasoning_available": bool(step_results),
        "timestamps": {
            "started_at": attempt.get("started_at"),
            "completed_at": attempt.get("completed_at"),
        },
    }


@router.post("/analyze")
async def analyze(
    request: AnalyzeRequest,
    user_id: str = Query(
        default="local",
        description="Firebase UID later. Use local during development.",
    ),
) -> dict[str, Any]:
    """
    Run the Himikama chain once, save the full result to Firestore,
    and return only the compact main result.

    This is the only endpoint that calls run_full_chain().
    """
    attempt_id: str | None = None

    try:
        intake_dict = request.intake.model_dump()

        attempt_id = await create_attempt(
            user_id=user_id,
            intake_object=intake_dict,
        )

        # Chain runs exactly once here.
        result = await run_full_chain(intake_dict)

        await save_attempt_result(
            user_id=user_id,
            attempt_id=attempt_id,
            result=result,
        )

        return build_main_response(
            user_id=user_id,
            attempt_id=attempt_id,
            result=result,
        )

    except Exception as e:
        logger.exception("Analysis failed")

        if attempt_id:
            await mark_attempt_failed(
                user_id=user_id,
                attempt_id=attempt_id,
                error=str(e),
            )

        raise HTTPException(
            status_code=500,
            detail=f"Analysis failed: {e}",
        )


@router.get("/attempts/{attempt_id}/trace")
async def get_trace(
    attempt_id: str,
    user_id: str = Query(default="local"),
) -> dict[str, Any]:
    """
    Return saved reasoning trace.

    This endpoint does NOT rerun the chain.
    It only reads step_results saved in Firestore.
    """
    try:
        attempt = await get_attempt(
            user_id=user_id,
            attempt_id=attempt_id,
        )

        step_results = attempt.get("step_results", {})

        return {
            "success": True,
            "user_id": user_id,
            "attempt_id": attempt_id,
            "status": attempt.get("status"),
            "reasoning_trace": build_reasoning_trace(step_results),
        }

    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Attempt not found")

    except Exception as e:
        logger.exception("Could not fetch trace")
        raise HTTPException(
            status_code=500,
            detail=f"Could not fetch trace: {e}",
        )


@router.get("/attempts/{attempt_id}")
async def get_saved_attempt(
    attempt_id: str,
    user_id: str = Query(default="local"),
) -> dict[str, Any]:
    """
    Return saved compact main result without rerunning chain.
    """
    try:
        attempt = await get_attempt(
            user_id=user_id,
            attempt_id=attempt_id,
        )

        return build_main_response_from_attempt(
            user_id=user_id,
            attempt_id=attempt_id,
            attempt=attempt,
        )

    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Attempt not found")


@router.get("/history")
async def history(
    user_id: str = Query(default="local"),
    limit: int = Query(default=20),
) -> dict[str, Any]:
    """
    Return saved attempt summaries for history screen.
    """
    try:
        items = await get_user_history(
            user_id=user_id,
            limit=limit,
        )

        return {
            "success": True,
            "user_id": user_id,
            "items": items,
        }

    except Exception as e:
        logger.exception("Could not fetch history")
        raise HTTPException(
            status_code=500,
            detail=f"Could not fetch history: {e}",
        )


@router.post("/validate-intake")
async def validate_intake(request: AnalyzeRequest) -> dict[str, Any]:
    """
    Validate request shape without running Gemini.
    """
    return {
        "valid": True,
        "intake": request.intake.model_dump(),
    }
