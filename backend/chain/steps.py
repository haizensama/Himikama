"""
himikama/backend/chain/steps.py
═══════════════════════════════════════════════════════════════
Phase 4 — Sub-Query Chain Steps
Controlled LangChain RAG Orchestration

Responsibility:
    Implements all 10 steps of the Himikama legal reasoning chain.

    Your Python application code controls:
        • Step order and hard gate decisions
        • Which collection is searched and what filter is applied
        • Which retrieval function runs
        • Which retrieved content is passed to the LLM
        • What data is returned for Firestore persistence

    LangChain controls the technical execution inside each step:
        • Prompt assembly via ChatPromptTemplate
        • Controlled RAG pipeline via RunnableLambda
        • Gemini call via ChatGoogleGenerativeAI
        • Async invocation via ainvoke()

    This is NOT autonomous LangChain agent orchestration.
    LangChain does not decide what to retrieve or which step to run.
    The application remains the legal workflow controller.

Step Overview:
    Step 1  — Timeliness [Pure Python — no LLM, hard gate]
    Step 2  — State Actor [Controlled LangChain RAG, hard gate]
    Step 3  — Fact Clarification [Controlled LangChain LLM]
    Step 4  — Rights Identification [Controlled LangChain RAG]
    Step 5  — Nature of Violation [Controlled LangChain RAG]
    Step 6  — Intent + Harm [Controlled LangChain LLM, merged]
    Step 7  — Similar Cases [Controlled LangChain RAG, two-stage]
    Step 8  — Precedent Analysis [Controlled LangChain context]
    Step 9  — Cross-Validation [Controlled LangChain context]
    Step 10 — Final Synthesis [Controlled LangChain LLM]

Return format for every step:
    {
        "step":        str   — step identifier e.g. "step_1"
        "answer":      str   — full raw LLM answer (stored in Firestore)
        "explanation": str   — plain-English user-facing summary
                               (displayed in Flutter explainability UI)
        "passed":      bool  — False only if hard gate failed
        "data":        dict  — step-specific structured data
    }

Explainability:
    Every step returns both "answer" (raw LLM output for completeness)
    and "explanation" (plain English for the Flutter UI stages):

        🔍 Understanding your situation  → Steps 1–3 explanations
        ⚖️  Identifying your rights       → Step 4–5 explanations
        🔬 Examining the violation        → Step 6 explanation
        📚 Finding similar cases          → Steps 7–8 explanations
        🧠 Validating our reasoning       → Step 9 explanation
        📊 Final assessment               → Step 10 explanation

    runner.py saves both fields to attempt storage after each step.

LLM Model:
    Uses config.gemini_model — set GEMINI_MODEL in .env.
    Currently: gemini-2.5-flash
    Temperature: 0 on all steps (deterministic output).
═══════════════════════════════════════════════════════════════
"""

import asyncio
import json
import logging
import random
import re
from datetime import datetime, timezone, timedelta
from typing import Any, Callable

from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnableLambda
from langchain_google_genai import ChatGoogleGenerativeAI

from chain.retrieval import (
    retrieve_articles,
    retrieve_cases_stage_a,
    retrieve_cases_stage_b,
    get_keyword_boost_articles,
    format_articles_for_prompt,
    format_cases_for_prompt,
)

logger = logging.getLogger(__name__)

# Fix 2: article-specific negative precedent threshold. A selected
# negative case may reject an article only when it concerns the same
# specific article or a direct parent/sub-article equivalent.
ARTICLE_SPECIFIC_NEGATIVE_SIMILARITY_FLOOR = 0.70


# ─────────────────────────────────────────────────────────────
# STANDARD SYSTEM PROMPT
# Applied to every LangChain LLM-based step.
# Implements all anti-hallucination principles.
# ─────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are a legal reasoning engine for Sri Lankan Fundamental Rights law operating as part of a structured analytical pipeline.

STRICT OPERATING RULES:
1. Answer ONLY the specific question asked in this step.
2. Use ONLY the materials explicitly provided in this prompt — retrieved articles, retrieved cases, and extracted user facts.
3. Do NOT reference any law, article, case, or fact not present in this prompt.
4. Do NOT provide legal advice or tell the user what to do.
5. Do NOT draw conclusions beyond the scope of the question asked.
6. If the provided materials are insufficient to answer, state INSUFFICIENT_INFORMATION rather than assuming.
7. Use hedging language — "appears to", "suggests", "based on the materials provided" — not definitive legal conclusions.
8. Every claim must be traceable to the materials provided in this prompt."""


# ─────────────────────────────────────────────────────────────
# LANGCHAIN HELPERS
# ─────────────────────────────────────────────────────────────

def _get_llm(max_tokens: int = 1024) -> ChatGoogleGenerativeAI:
    """
    Create a Gemini chat model through LangChain.

    Reads model name from config so changing GEMINI_MODEL in .env
    updates every step instantly. Temperature=0 for deterministic
    legal reasoning — never change this for chain steps.

    Args:
        max_tokens: Maximum output tokens for this step.

    Returns:
        ChatGoogleGenerativeAI instance.
    """
    try:
        from api.config import config
    except Exception as e:
        raise RuntimeError(f"Could not load API config: {e}")

    return ChatGoogleGenerativeAI(
        model=config.gemini_model,           # reads from .env GEMINI_MODEL
        google_api_key=config.gemini_api_key,
        temperature=0,                        # deterministic — never change
        max_output_tokens=max_tokens,
    )


def _extract_llm_text(response: Any) -> str:
    """
    Safely extract plain text from a LangChain Gemini response.

    Handles all response content types Gemini may return:
    string, list of strings, list of dicts with text/content keys.
    """
    if response is None:
        return ""

    content = getattr(response, "content", response)

    if isinstance(content, str):
        return content.strip()

    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text") or item.get("content") or ""
                if text:
                    parts.append(str(text))
            else:
                parts.append(str(item))
        return "\n".join(parts).strip()

    return str(content).strip()




# ─────────────────────────────────────────────────────────────
# TRANSIENT LLM/API RETRY HELPERS
# ─────────────────────────────────────────────────────────────

_RETRYABLE_LLM_ERROR_HINTS = (
    "429",
    "500",
    "502",
    "503",
    "504",
    "unavailable",
    "service unavailable",
    "temporarily unavailable",
    "deadline exceeded",
    "timeout",
    "timed out",
    "rate limit",
    "resource exhausted",
    "too many requests",
    "internal server error",
    "bad gateway",
    "gateway timeout",
    "connection reset",
    "connection aborted",
)


def _is_retryable_llm_error(error: Exception) -> bool:
    """
    Return True for transient provider/network failures that are safe to retry.

    This is intentionally conservative. It retries temporary transport/provider
    errors such as Gemini/API 503 UNAVAILABLE, 429 rate limits, timeouts, and
    gateway failures. It does not hide persistent coding or prompt-shape errors
    unless their message clearly matches a transient failure signal.
    """
    message = f"{type(error).__name__}: {error}".lower()
    return any(hint in message for hint in _RETRYABLE_LLM_ERROR_HINTS)


async def _ainvoke_llm_with_retries(
    runnable: Any,
    payload: dict[str, Any],
    *,
    step_name: str,
    max_attempts: int = 6,
    base_delay_seconds: float = 2.0,
    max_delay_seconds: float = 20.0,
) -> Any:
    """
    Invoke a LangChain runnable with retry protection for transient LLM/API errors.

    Why this exists:
        During batch evaluation, Gemini can occasionally return temporary
        provider errors such as 503 UNAVAILABLE. Without retry protection,
        one temporary provider failure causes the scenario row to be written
        as status="failed", which pollutes the evaluation output.

    Behavior:
        - Retry only transient provider/network failures.
        - Use exponential backoff with small jitter.
        - Preserve deterministic legal reasoning settings; this does not
          change prompts, temperature, retrieval, or legal logic.
        - If all attempts fail, raise the original error so the runner still
          records a real failure instead of silently fabricating output.

    max_attempts means total attempts, not retries after the first attempt.
    With max_attempts=6, the call gets 1 original attempt + 5 retries.
    """
    last_error: Exception | None = None

    for attempt in range(1, max_attempts + 1):
        try:
            return await runnable.ainvoke(payload)
        except Exception as error:
            last_error = error

            if not _is_retryable_llm_error(error) or attempt >= max_attempts:
                raise

            delay = min(
                max_delay_seconds,
                base_delay_seconds * (2 ** (attempt - 1)),
            )
            delay += random.uniform(0.0, 0.75)

            logger.warning(
                "%s LLM/API call failed with transient error on attempt "
                "%s/%s. Retrying in %.2f seconds. Error: %s",
                step_name,
                attempt,
                max_attempts,
                delay,
                error,
            )

            await asyncio.sleep(delay)

    # Defensive fallback; practically unreachable because the final failed
    # attempt raises above.
    if last_error is not None:
        raise last_error

    raise RuntimeError(f"{step_name} LLM/API call failed without an exception.")


def _format_intake_fields(intake_fields: dict[str, Any]) -> str:
    """Format selected intake fields as a prompt-ready bullet list."""
    lines = [
        f"- {label}: {value}"
        for label, value in intake_fields.items()
        if value
    ]
    return "\n".join(lines) if lines else "No specific facts extracted."


def _base_prompt_template() -> ChatPromptTemplate:
    """
    Shared LangChain prompt template for all steps.

    Variables: step_number, step_question, user_narrative,
               facts_section, retrieved_section
    """
    return ChatPromptTemplate.from_messages([
        ("system", SYSTEM_PROMPT),
        ("human", """USER NARRATIVE (for context):
{user_narrative}

EXTRACTED FACTS:
{facts_section}

{retrieved_section}

SUB-QUERY STEP {step_number}:
{step_question}"""),
    ])


def _make_prompt_inputs(
    step_number:      int,
    step_question:    str,
    intake:           dict,
    intake_fields:    dict[str, Any],
    retrieved_content: str = "",
    retrieved_label:   str = "RETRIEVED LEGAL MATERIALS",
) -> dict[str, Any]:
    """Build the input dict expected by the LangChain prompt template."""
    retrieved_section = (
        f"{retrieved_label}:\n{retrieved_content}"
        if retrieved_content else ""
    )
    return {
        "step_number":      step_number,
        "step_question":    step_question,
        "user_narrative":   intake.get("user_narrative", ""),
        "facts_section":    _format_intake_fields(intake_fields),
        "retrieved_section": retrieved_section,
    }


# ─────────────────────────────────────────────────────────────
# LANGCHAIN EXECUTION HELPERS
# ─────────────────────────────────────────────────────────────

async def _run_llm_only_chain(
    *,
    step_number:   int,
    step_question: str,
    intake:        dict,
    intake_fields: dict[str, Any],
    max_tokens:    int = 1024,
) -> str:
    """
    Controlled LangChain pipeline for non-RAG steps.

    Pipeline: prompt inputs → ChatPromptTemplate → Gemini

    Used by Steps 3, 6. Step 10 builds its own custom
    prompt template due to different variable structure.
    """
    prompt = _base_prompt_template()
    llm    = _get_llm(max_tokens=max_tokens)
    chain  = prompt | llm

    prompt_inputs = _make_prompt_inputs(
        step_number=step_number,
        step_question=step_question,
        intake=intake,
        intake_fields=intake_fields,
    )

    try:
        response = await _ainvoke_llm_with_retries(
            chain,
            prompt_inputs,
            step_name=f"step_{step_number}",
        )
        return _extract_llm_text(response)
    except Exception as e:
        raise RuntimeError(
            f"LangChain LLM-only step {step_number} failed: {e}"
        )


async def _run_controlled_rag_chain(
    *,
    step_number:     int,
    step_question:   str,
    intake:          dict,
    intake_fields:   dict[str, Any],
    retriever:       Callable[[dict], list[dict]],
    formatter:       Callable[[list[dict]], str],
    retrieved_label: str = "RETRIEVED LEGAL MATERIALS",
    max_tokens:      int = 1024,
) -> tuple[str, list[dict]]:
    """
    Controlled LangChain RAG pipeline.

    Pipeline:
        input payload
          → RunnableLambda (retrieval controlled by app code)
          → ChatPromptTemplate
          → Gemini

    The retriever lambda is deterministic application code.
    LangChain executes it as part of the pipeline, but does
    not decide what to retrieve or which collection to use.

    Returns:
        Tuple of (answer_text, retrieved_items)

    Used by Steps 2, 4, 5.
    Step 7 uses a direct ainvoke pattern instead (see run_step_7).
    """
    def retrieve_and_build(payload: dict) -> dict:
        retrieved_items   = retriever(payload)
        retrieved_content = formatter(retrieved_items)
        prompt_inputs     = _make_prompt_inputs(
            step_number=step_number,
            step_question=step_question,
            intake=payload["intake"],
            intake_fields=payload["intake_fields"],
            retrieved_content=retrieved_content,
            retrieved_label=retrieved_label,
        )
        # Carry retrieved_items through the pipeline for the caller
        prompt_inputs["_retrieved_items"] = retrieved_items
        return prompt_inputs

    retrieval_runnable = RunnableLambda(retrieve_and_build)
    prompt = _base_prompt_template()
    llm    = _get_llm(max_tokens=max_tokens)

    payload = {"intake": intake, "intake_fields": intake_fields}

    try:
        # Run retrieval separately so we can return retrieved_items
        prepared = await retrieval_runnable.ainvoke(payload)
        retrieved_items = prepared.pop("_retrieved_items", [])

        # Continue: prompt → Gemini
        response = await _ainvoke_llm_with_retries(
            prompt | llm,
            prepared,
            step_name=f"step_{step_number}",
        )
        answer   = _extract_llm_text(response)
        return answer, retrieved_items

    except Exception as e:
        raise RuntimeError(
            f"Controlled LangChain RAG step {step_number} failed: {e}"
        )


# ─────────────────────────────────────────────────────────────
# EXPLAINABILITY HELPERS
# Convert raw LLM answers into plain-English user-facing text
# for the Flutter explainability UI stages.
# ─────────────────────────────────────────────────────────────

def _explain_step_1(passed: bool, data: dict) -> str:
    """Plain-English explanation of the timeliness check."""
    if not passed:
        reason = data.get("reason", "")
        if reason == "incident_date_missing":
            return (
                "We could not determine when the incident occurred. "
                "Please provide the exact date before proceeding."
            )
        if reason == "incident_date_unparseable":
            return (
                "The date provided could not be read. "
                "Please provide it in a clear format such as 15 April 2025."
            )
        days = data.get("days_elapsed", 0)
        return (
            f"The incident occurred approximately {days} days ago, "
            f"which is outside the 30-day filing window required to "
            f"challenge a Fundamental Rights violation in the Supreme Court. "
            f"Unfortunately this application cannot proceed on timeliness grounds."
        )
    days_elapsed   = data.get("days_elapsed", 0)
    days_remaining = data.get("days_remaining", 0)
    return (
        f"The incident falls within the 30-day filing window. "
        f"Approximately {days_elapsed} day(s) have passed and "
        f"{days_remaining} day(s) remain to file a petition."
    )


def _explain_step_2(passed: bool, answer: str) -> str:
    """Plain-English explanation of the state actor check."""
    if not passed:
        return (
            "Based on the information provided, the party involved does not "
            "appear to be a state authority. Fundamental Rights petitions in "
            "Sri Lanka can only be brought against state actors. "
            "This application cannot proceed on this basis."
        )
    return (
        "The party involved appears to be a state authority or an entity "
        "acting under state power. This satisfies a key requirement for "
        "a Fundamental Rights petition."
    )


def _explain_step_3(answer: str) -> str:
    """Plain-English summary of the fact clarification."""
    # Extract the ACT/OMISSION line if the LLM followed the format
    match = re.search(
        r"ACT/OMISSION:\s*(.+?)(?:\n|CIRCUMSTANCES|$)",
        answer, re.DOTALL | re.IGNORECASE
    )
    if match:
        act = match.group(1).strip()[:300]
        return f"Based on your account, the complaint concerns: {act}"
    # Fallback: first two sentences of the answer
    sentences = answer.split(".")
    summary   = ". ".join(s.strip() for s in sentences[:2] if s.strip())
    return f"We identified the following from your situation: {summary}."


def _explain_step_4(articles: list[str], answer: str) -> str:
    """Plain-English explanation of which rights may apply."""
    if not articles:
        return (
            "Based on the facts described and the constitutional articles "
            "reviewed, we could not identify specific articles that clearly "
            "apply. This may indicate the situation falls outside Chapter 3 "
            "Fundamental Rights provisions."
        )
    article_list = ", ".join(f"Article {a}" for a in articles)
    return (
        f"The following constitutional provisions appear relevant to your "
        f"situation: {article_list}. These will be used to search for "
        f"similar cases."
    )


def _explain_step_5(answer: str) -> str:
    """Plain-English explanation of the nature of the violation."""
    lower = answer.lower()
    if "direct" in lower and "official capacity" in lower:
        return (
            "The alleged violation appears to be a direct act by a state "
            "authority acting in an official capacity."
        )
    if "exercising" in lower or "conferred" in lower:
        return (
            "The alleged violation appears to have been carried out by a "
            "person exercising power derived from the state."
        )
    return (
        "The nature of the alleged state action has been assessed. "
        "See the detailed reasoning for the full analysis."
    )

def _explain_step_6(harm_established: bool, answer: str) -> str:
    """
    Plain-English explanation of intent/systemic pattern and harm findings.

    This helper is careful not to misread phrases like
    'insufficient information to conclude intentional' as a positive
    finding of intent.
    """
    lower = answer.lower()

    negative_intent_signals = [
        "insufficient information to conclude",
        "insufficient information",
        "not enough information",
        "no information provided",
        "does not clearly establish",
        "cannot conclude",
        "cannot be concluded",
        "no reasonable basis to conclude",
    ]

    positive_intent_signals = [
        "appears to have been intentional",
        "reasonable basis to conclude",
        "systemic pattern",
        "forms part of a pattern",
    ]

    if any(signal in lower for signal in negative_intent_signals):
        intent_text = (
            "There is not enough information to conclude that the act was "
            "intentional or part of a systemic pattern."
        )
    elif any(signal in lower for signal in positive_intent_signals):
        intent_text = (
            "The act may have been intentional or part of a broader pattern, "
            "based on the available facts."
        )
    else:
        intent_text = (
            "The evidence of intent or a systemic pattern is limited based "
            "on the information provided."
        )

    harm_text = (
        "Tangible harm appears to have been suffered as a result of the "
        "alleged violation."
        if harm_established
        else "The information provided does not clearly establish tangible harm."
    )

    return f"{intent_text} {harm_text}"

def _explain_step_7(case_ids: list[str], stage_a_cases: list[dict]) -> str:
    """Plain-English explanation of similar cases found."""
    if not case_ids or not stage_a_cases:
        return (
            "No sufficiently similar cases were found in our database "
            "for the specific facts of your situation."
        )
    # Find names for the identified case_ids
    id_set = set(str(c) for c in case_ids)
    names  = [
        case["case_name"]
        for case in stage_a_cases
        if str(case.get("case_id", "")) in id_set
    ][:3]

    if names:
        name_list = "; ".join(names)
        return (
            f"We found {len(names)} similar case(s) in our database: "
            f"{name_list}. These will be analyzed for relevant precedent."
        )
    return (
        f"We identified {len(case_ids)} similar case(s) for precedent analysis."
    )


def _explain_step_8(answer: str, stage_b_cases: list[dict]) -> str:
    """Plain-English summary of precedent analysis."""
    if not stage_b_cases:
        return "No precedent analysis was possible as no similar cases were available."

    violated     = sum(
        1 for c in stage_b_cases if c.get("judgment") == "VIOLATED"
    )
    not_violated = len(stage_b_cases) - violated

    outcome_text = ""
    if violated > 0 and not_violated == 0:
        outcome_text = "All similar cases resulted in a finding of violation."
    elif not_violated > 0 and violated == 0:
        outcome_text = "Similar cases did not result in a finding of violation."
    else:
        outcome_text = (
            f"Of the similar cases, {violated} resulted in a finding of "
            f"violation and {not_violated} did not."
        )

    return (
        f"{outcome_text} The detailed reasoning from these cases has been "
        f"analyzed to assess its relevance to your situation."
    )


def _explain_step_9(consistent: bool, inconsistency_found: bool) -> str:
    """Plain-English explanation of the cross-validation result."""
    if inconsistency_found:
        return (
            "Our cross-check found some differences between the rights "
            "identified from the constitutional articles and the patterns "
            "seen in similar cases. This has been noted and will be "
            "reflected in the confidence assessment."
        )
    return (
        "Our cross-check confirms that the rights identified are consistent "
        "with the patterns seen in similar cases. This strengthens the "
        "reliability of the analysis."
    )


def _explain_step_10(answer: str) -> str:
    """
    For Step 10, the answer IS the user-facing content.
    Extract Section 5 (Overall Assessment) as the primary
    explanation shown in the summary card.
    """
    match = re.search(
        r"SECTION 5[^\n]*\n(.*?)(?:SECTION \d|$)",
        answer, re.DOTALL | re.IGNORECASE
    )
    if match:
        section5 = match.group(1).strip()
        if len(section5) > 600:
            section5 = section5[:600] + "..."
        return section5
    # Fallback: last 400 chars of answer
    return answer[-400:].strip() if len(answer) > 400 else answer


# ─────────────────────────────────────────────────────────────
# STEP 1 — TIMELINESS [PURE PYTHON — NO LLM, HARD GATE]
# ─────────────────────────────────────────────────────────────

async def run_step_1(intake: dict) -> dict:
    """
    Step 1 — Timeliness check.

    Pure Python date arithmetic — no LangChain, no LLM.
    FR petitions must be filed within 30 days (Article 126).
    Hard gate: fail → runner terminates chain (time_barred).
    """
    incident_date_str = intake.get("incident_date")

    if not incident_date_str:
        data = {"reason": "incident_date_missing"}
        return {
            "step":        "step_1",
            "answer":      (
                "The incident date could not be determined from the "
                "information provided. The petitioner must clarify the "
                "date of the incident before this application can proceed."
            ),
            "explanation": _explain_step_1(False, data),
            "passed":      False,
            "data":        data,
        }

    parsed_date = _parse_date(incident_date_str)

    if parsed_date is None:
        data = {"reason": "incident_date_unparseable"}
        return {
            "step":        "step_1",
            "answer":      (
                f"The incident date '{incident_date_str}' could not be "
                f"parsed into a specific date. The petitioner must provide "
                f"the exact date of the incident."
            ),
            "explanation": _explain_step_1(False, data),
            "passed":      False,
            "data":        data,
        }

    today        = datetime.now(timezone.utc).date()
    days_elapsed = (today - parsed_date).days

    if days_elapsed > 30:
        data = {
            "days_elapsed":  days_elapsed,
            "days_remaining": 0,
            "incident_date": str(parsed_date),
        }
        return {
            "step":        "step_1",
            "answer":      (
                f"The incident occurred approximately {days_elapsed} days ago "
                f"(on or around {parsed_date}). Fundamental Rights petitions "
                f"must be filed within 30 days of the alleged infringement "
                f"under Article 126 of the Constitution. This application "
                f"appears to be time-barred and cannot proceed."
            ),
            "explanation": _explain_step_1(False, data),
            "passed":      False,
            "data":        data,
        }

    days_remaining = 30 - days_elapsed
    data = {
        "days_elapsed":  days_elapsed,
        "days_remaining": days_remaining,
        "incident_date": str(parsed_date),
    }
    return {
        "step":        "step_1",
        "answer":      (
            f"The incident occurred approximately {days_elapsed} day(s) ago "
            f"(on or around {parsed_date}). This is within the 30-day filing "
            f"period required under Article 126. Approximately "
            f"{days_remaining} day(s) remain to file the petition."
        ),
        "explanation": _explain_step_1(True, data),
        "passed":      True,
        "data":        data,
    }


def _parse_date(date_str: str):
    """
    Parse a date string into a date object.
    Handles ISO format, common delimited formats, and simple
    relative English phrases (last month, last week, yesterday).
    Returns None if unparseable.
    """
    if not date_str:
        return None

    date_str = date_str.strip()

    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(date_str, fmt).date()
        except ValueError:
            continue

    today = datetime.now(timezone.utc).date()
    lower = date_str.lower()

    if "last month" in lower:
        if today.month == 1:
            return today.replace(year=today.year - 1, month=12, day=15)
        return today.replace(month=today.month - 1, day=15)
    if "last week"  in lower: return today - timedelta(days=7)
    if "yesterday"  in lower: return today - timedelta(days=1)
    if "two weeks"  in lower or "2 weeks" in lower:
        return today - timedelta(days=14)

    return None


# ─────────────────────────────────────────────────────────────
# STEP 2 — STATE ACTOR [CONTROLLED LANGCHAIN RAG, HARD GATE]
# ─────────────────────────────────────────────────────────────

async def run_step_2(intake: dict, article_collection) -> dict:
    """
    Step 2 — State actor verification.

    Controlled LangChain RAG: app-controlled article retrieval
    → ChatPromptTemplate → Gemini.
    Hard gate: fail → runner terminates chain (not_state_actor).
    """
    question = """Based on the actor described and the constitutional provisions provided, was the person or institution that acted a state authority, or was it a private person acting under the power or direction of the state?

Answer YES or NO first, then explain your reasoning with specific reference to the actor's role and the constitutional provisions provided.

If YES (state actor): Identify which type of state actor they appear to be.
If NO (not a state actor): Explain why fundamental rights law may not apply to this respondent.
If UNCERTAIN: State what additional information would be needed to determine this."""

    answer, articles = await _run_controlled_rag_chain(
        step_number=2,
        step_question=question,
        intake=intake,
        intake_fields={
            "Actor name":    intake.get("actor_name"),
            "Actor role":    intake.get("actor_role"),
            "What happened": intake.get("what_happened"),
        },
        retriever=lambda payload: retrieve_articles(
            article_collection, payload["intake"], "step_2"
        ),
        formatter=format_articles_for_prompt,
        retrieved_label="RETRIEVED CONSTITUTIONAL MATERIALS",
        max_tokens=1024,
    )

    answer_lower     = answer.lower()
    not_state_signals = [
        "no, ", "no.", "not a state", "private individual",
        "private person", "cannot be attributed",
    ]
    is_not_state = any(s in answer_lower for s in not_state_signals)
    is_uncertain = (
        "uncertain" in answer_lower
        or "insufficient_information" in answer_lower
    )
    passed = not is_not_state

    return {
        "step":        "step_2",
        "answer":      answer,
        "explanation": _explain_step_2(passed, answer),
        "passed":      passed,
        "data": {
            "is_state_actor":      passed,
            "is_uncertain":        is_uncertain,
            "retrieved_articles":  articles,
        },
    }


# ─────────────────────────────────────────────────────────────
# STEP 3 — FACT CLARIFICATION [CONTROLLED LANGCHAIN LLM]
# ─────────────────────────────────────────────────────────────

async def run_step_3(intake: dict) -> dict:
    """
    Step 3 — Fact clarification.

    Produces a clean, structured articulation of the legally
    material facts. No RAG — LLM reasoning only.
    """
    question = """Based on the user's account, describe clearly and concisely the specific act or omission being complained of.

Structure your answer as:
ACT/OMISSION: What the state actor did or failed to do.
CIRCUMSTANCES: The relevant circumstances surrounding the act.
DIRECT CONSEQUENCES: What immediately resulted from the act.

Do not make any legal conclusions. Do not identify which rights were violated. Only describe what happened factually."""

    answer = await _run_llm_only_chain(
        step_number=3,
        step_question=question,
        intake=intake,
        intake_fields={
            "What happened":    intake.get("what_happened"),
            "Actor role":       intake.get("actor_role"),
            "Location":         intake.get("incident_location"),
            "Date of incident": intake.get("incident_date"),
        },
    )

    return {
        "step":        "step_3",
        "answer":      answer,
        "explanation": _explain_step_3(answer),
        "passed":      True,
        "data":        {},
    }


# ─────────────────────────────────────────────────────────────
# STEP 4 — RIGHTS IDENTIFICATION [CONTROLLED LANGCHAIN RAG]
# ─────────────────────────────────────────────────────────────

async def run_step_4(intake: dict, article_collection) -> dict:
    """
    Step 4 — Identify potentially violated fundamental rights.

    Controlled LangChain RAG with article retrieval.
    The identified articles are stored in data["articles_identified"]
    and used as the metadata filter in Step 7's case retrieval.
    """
    question = """Based on the facts described and the constitutional articles provided above, which fundamental right(s) appear to have been potentially violated?

For each potentially applicable article:
1. State the article number and its subject matter.
2. Explain specifically why it appears to apply to these facts.
3. Identify any element of the facts that corresponds to each element of the right.

Use hedging language — "appears to", "may apply", "suggests a potential violation of".
Do NOT conclude that a violation has occurred — only that these rights appear relevant.
ONLY identify articles from the list provided above. Do not reference any article not in the provided materials."""

    answer, retrieved_articles = await _run_controlled_rag_chain(
        step_number=4,
        step_question=question,
        intake=intake,
        intake_fields={
            "What happened": intake.get("what_happened"),
            "Harm suffered": intake.get("harm_suffered"),
        },
        retriever=lambda payload: retrieve_articles(
            article_collection, payload["intake"], "step_4"
        ),
        formatter=format_articles_for_prompt,
        retrieved_label="RETRIEVED CONSTITUTIONAL ARTICLES",
        max_tokens=1500,
    )

    # Extract article numbers from LLM answer
    articles_identified = _extract_article_numbers(answer)

    # Supplement with keyword boost to ensure coverage
    # Supplement with keyword boost to ensure coverage
    boosted = get_keyword_boost_articles(intake)
    for article in boosted:
        if article not in articles_identified:
            articles_identified.append(article)

    # Remove broad bare articles like "13" when specific
    # sub-articles like "13(1)" or "13(2)" are present.
    articles_identified = _remove_bare_articles_when_subarticles_exist(
        articles_identified
    )

    logger.info(f"Step 4 identified articles: {articles_identified}")
    return {
        "step":        "step_4",
        "answer":      answer,
        "explanation": _explain_step_4(articles_identified, answer),
        "passed":      True,
        "data": {
            "articles_identified": articles_identified,
            "retrieved_articles":  retrieved_articles,
        },
    }


def _extract_article_numbers(text: str) -> list[str]:
    """
    Extract canonical article number strings from LLM answer text.

    Avoid broad bare articles like "13" when sub-articles such
    as "13(1)" and "13(2)" are already present.
    """
    # Capture full canonical article IDs, including nested sub-articles.
    # Examples captured correctly:
    #   13
    #   13(1)
    #   13(2)
    #   14(1)(g)
    #   14(A)
    #
    # The old word-boundary regex could incorrectly extract only "13"
    # from "13(1)" because "(" creates a word boundary after "13".
    pattern = r"(?<![A-Za-z0-9])(\d+(?:\([0-9A-Za-z]+\))*)(?![A-Za-z0-9])"
    matches = re.findall(pattern, text)

    valid: list[str] = []
    seen: set[str] = set()

    for match in matches:
        base_match = re.match(r"(\d+)", match)

        if not base_match:
            continue

        base = int(base_match.group(1))

        # Chapter 3 articles are 10–17, plus Article 126 and 4(d).
        if 10 <= base <= 20 or base in [4, 126]:
            if match not in seen:
                seen.add(match)
                valid.append(match)

    # Remove broad bare articles if specific sub-articles exist.
    # Example:
    # ["13", "13(1)", "13(2)"] → ["13(1)", "13(2)"]
    bases_with_subarticles: set[str] = set()

    for article in valid:
        if "(" in article:
            base = article.split("(")[0]
            bases_with_subarticles.add(base)

    cleaned: list[str] = []

    for article in valid:
        if article in bases_with_subarticles:
            continue
        cleaned.append(article)

    return cleaned

def _remove_bare_articles_when_subarticles_exist(
    articles: list[str],
) -> list[str]:
    """
    Remove broad bare articles like '13' when specific sub-articles
    like '13(1)' or '13(2)' are present.

    Example:
        ["13", "13(1)", "13(2)", "13(3)"]
        -> ["13(1)", "13(2)", "13(3)"]
    """
    bases_with_subarticles: set[str] = set()

    for article in articles:
        article = str(article).strip()
        if "(" in article:
            bases_with_subarticles.add(article.split("(")[0])

    cleaned: list[str] = []
    seen: set[str] = set()

    for article in articles:
        article = str(article).strip()

        if not article:
            continue

        # If "13(1)" exists, remove broad "13"
        if article in bases_with_subarticles:
            continue

        if article not in seen:
            seen.add(article)
            cleaned.append(article)

    return cleaned
# ─────────────────────────────────────────────────────────────
# STEP 5 — NATURE OF VIOLATION [CONTROLLED LANGCHAIN RAG]
# ─────────────────────────────────────────────────────────────

async def run_step_5(intake: dict, article_collection) -> dict:
    """
    Step 5 — Nature of violation (direct vs through state power).

    Controlled LangChain RAG with article retrieval.
    """
    question = """Based on the facts and the constitutional provisions provided, was the alleged violation:
(a) A DIRECT act by a state authority acting in official capacity, or
(b) An act by a person exercising power conferred by or derived from the state?

Explain your reasoning with reference to:
1. The role of the actor involved.
2. Whether they were acting in official capacity.
3. The relevant constitutional provisions regarding state action.
4. Any distinction between personal conduct and official conduct."""

    answer, retrieved_articles = await _run_controlled_rag_chain(
        step_number=5,
        step_question=question,
        intake=intake,
        intake_fields={
            "What happened": intake.get("what_happened"),
            "Actor role":    intake.get("actor_role"),
            "Actor name":    intake.get("actor_name"),
        },
        retriever=lambda payload: retrieve_articles(
            article_collection, payload["intake"], "step_5"
        ),
        formatter=format_articles_for_prompt,
        retrieved_label="RETRIEVED CONSTITUTIONAL MATERIALS",
        max_tokens=1024,
    )

    return {
        "step":        "step_5",
        "answer":      answer,
        "explanation": _explain_step_5(answer),
        "passed":      True,
        "data":        {"retrieved_articles": retrieved_articles},
    }


# ─────────────────────────────────────────────────────────────
# STEP 6 — INTENT + HARM [CONTROLLED LANGCHAIN LLM, MERGED]
# ─────────────────────────────────────────────────────────────

async def run_step_6(intake: dict) -> dict:
    """
    Step 6 — Intent and harm assessment (two questions, one call).

    Originally two separate steps, merged to reduce LLM calls.
    No RAG — LLM reasoning over intake facts only.
    """
    question = """Answer BOTH of the following questions based on the facts provided:

QUESTION A — INTENT OR SYSTEMIC PATTERN:
Is there a reasonable basis to conclude that the alleged act was intentional on the part of the state actor, or that it forms part of a systemic pattern of conduct?
Explain your reasoning based on the facts described. Use hedging language.

QUESTION B — ACTUAL HARM:
Has the petitioner suffered actual, tangible harm or infringement as a direct result of the alleged act?
Describe the nature and extent of the harm based on what was stated. Distinguish between:
- Direct harm (immediate physical, financial, or liberty deprivation)
- Consequential harm (losses that flowed from the act)
If no clear harm is described, state this explicitly."""

    answer = await _run_llm_only_chain(
        step_number=6,
        step_question=question,
        intake=intake,
        intake_fields={
            "What happened": intake.get("what_happened"),
            "Harm suffered": intake.get("harm_suffered"),
            "Actor role":    intake.get("actor_role"),
        },
        max_tokens=1200,
    )

    answer_lower    = answer.lower()
    no_harm_signals = [
        "no harm", "no clear harm", "no harm described",
        "no tangible harm", "harm not described",
    ]
    harm_established = not any(s in answer_lower for s in no_harm_signals)

    return {
        "step":        "step_6",
        "answer":      answer,
        "explanation": _explain_step_6(harm_established, answer),
        "passed":      True,
        "data":        {"harm_established": harm_established},
    }


# ─────────────────────────────────────────────────────────────
# STEP 7 — SIMILAR CASES [CONTROLLED LANGCHAIN RAG, TWO-STAGE]
# ─────────────────────────────────────────────────────────────

async def run_step_7(
    intake:               dict,
    case_collection,
    articles_from_step_4: list[str],
) -> dict:
    """
    Step 7 — Similar case retrieval (two-stage RAG).

    Stage A: App-controlled semantic search with article filter.
             LangChain assembles prompt and calls Gemini.

    Stage B: Deterministic direct fetch by case_id.
             Intentionally outside LangChain — this is a
             targeted lookup, not semantic reasoning.

    Important design choice:
        Himikama's FR case corpus is domain-specific and comparatively small.
        A single precedent may be valuable if it is very close, but a single
        loosely related precedent should not control the final outcome. Step 7
        therefore asks for up to 5 close cases and only supplements the LLM's
        selections with additional Stage A cases when those extra cases are
        reasonably close and article-relevant.
    """
    allowed_articles = _remove_bare_articles_when_subarticles_exist(
        _coerce_article_list(articles_from_step_4 or [])
    )

    question = """Based on the facts of this situation, which of the cases provided are most factually similar?

Select 3 to 5 cases if there are 3 to 5 genuinely close matches.
If only 1 or 2 cases are genuinely close, select only those cases and explain why the remaining retrieved cases are weaker.

For each selected case:
1. State the case name and case number exactly as shown.
2. Explain specifically which facts make it similar to the current situation.
3. Note any important factual differences.
4. State whether the case is a close match, partial match, or weak match.

Select cases based on FACTUAL similarity — similar acts, similar actors, similar circumstances.
Do not select cases based only on the articles cited.
Do not pad the answer with weak cases just to reach a number.
State the case_id number for each selected case in brackets at the end of each selection e.g. [case_id: 42]"""

    stage_a_cases = retrieve_cases_stage_a(
        case_collection,
        intake,
        filter_articles=allowed_articles,
    )

    if not stage_a_cases:
        return {
            "step":        "step_7",
            "answer":      (
                "No similar cases were found in the case law corpus for "
                "the facts and articles identified. This may indicate "
                "limited precedent for this specific situation."
            ),
            "explanation": _explain_step_7([], []),
            "passed":      True,
            "data":        {
                "case_ids":      [],
                "cases":         [],
                "stage_b_cases": [],
                "case_selection_profile": {
                    "selection_mode": "no_cases",
                    "minimum_target": 3,
                    "maximum_target": 5,
                    "supplemented_case_ids": [],
                    "note": "No Stage A cases were available.",
                },
            },
        }

    cases_formatted = format_cases_for_prompt(stage_a_cases)

    prompt = _base_prompt_template()
    llm    = _get_llm(max_tokens=1800)

    prompt_inputs = _make_prompt_inputs(
        step_number=7,
        step_question=question,
        intake=intake,
        intake_fields={
            "What happened": intake.get("what_happened"),
            "Harm suffered": intake.get("harm_suffered"),
            "Actor role":    intake.get("actor_role"),
        },
        retrieved_content=cases_formatted,
        retrieved_label="SIMILAR CASE SUMMARIES RETRIEVED FROM CASE CORPUS",
    )

    try:
        response = await _ainvoke_llm_with_retries(
            prompt | llm,
            prompt_inputs,
            step_name="step_7",
        )
        answer   = _extract_llm_text(response)
    except Exception as e:
        raise RuntimeError(f"Controlled LangChain Step 7 failed: {e}")

    llm_case_ids = _extract_case_ids_from_answer(answer, stage_a_cases)

    case_ids, case_selection_profile = _supplement_case_ids_with_close_stage_a_cases(
        llm_case_ids,
        stage_a_cases,
        allowed_articles=allowed_articles,
        minimum_cases=3,
        maximum_cases=5,
    )

    logger.info(
        "Step 7 identified case_ids: %s (selection_profile=%s)",
        case_ids,
        case_selection_profile,
    )

    stage_b_cases = retrieve_cases_stage_b(case_collection, case_ids)
    stage_b_cases = _merge_stage_a_metadata_into_stage_b_cases(
        stage_b_cases,
        stage_a_cases,
    )

    return {
        "step":        "step_7",
        "answer":      answer,
        "explanation": _explain_step_7(case_ids, stage_a_cases),
        "passed":      True,
        "data": {
            "case_ids":      case_ids,
            "llm_case_ids":  llm_case_ids,
            "cases":         stage_a_cases,
            "stage_b_cases": stage_b_cases,
            "case_selection_profile": case_selection_profile,
        },
    }


def _extract_case_ids_from_answer(
    answer:        str,
    stage_a_cases: list[dict],
) -> list[str]:
    """
    Extract case_ids from the Step 7 LLM answer.

    Method 1: Parse [case_id: N] tags the LLM was asked to include.
    Method 2: Match case names from answer text to Stage A results.
    Fallback:  Use top Stage A cases by similarity score.
    """
    case_ids: list[str] = []

    id_matches = re.findall(r"\[case_id:\s*(\d+)\]", answer, re.IGNORECASE)
    for match in id_matches[:5]:
        if match not in case_ids:
            case_ids.append(match)

    if len(case_ids) < 3:
        for case in stage_a_cases:
            name = case.get("case_name", "")
            if name and name[:20].lower() in answer.lower():
                cid = str(case.get("case_id", ""))
                if cid and cid not in case_ids:
                    case_ids.append(cid)
            if len(case_ids) >= 5:
                break

    if not case_ids:
        logger.warning(
            "Step 7: Could not extract case_ids from answer. "
            "Falling back to top Stage A cases."
        )
        for case in stage_a_cases[:3]:
            cid = str(case.get("case_id", ""))
            if cid and cid not in case_ids:
                case_ids.append(cid)

    return case_ids[:5]


def _supplement_case_ids_with_close_stage_a_cases(
    selected_case_ids: list[str],
    stage_a_cases: list[dict],
    *,
    allowed_articles: list[str],
    minimum_cases: int = 3,
    maximum_cases: int = 5,
) -> tuple[list[str], dict[str, Any]]:
    """
    Add additional Stage A cases only when they are close enough.

    This avoids two bad extremes:
        1. one misleading case controls the final outcome;
        2. weak filler cases are added merely to reach a fixed number.
    """
    selected = _dedupe_preserve_order([
        str(case_id).strip()
        for case_id in selected_case_ids
        if str(case_id).strip()
    ])[:maximum_cases]

    stage_a_by_id = {
        str(case.get("case_id", "")).strip(): case
        for case in stage_a_cases or []
        if str(case.get("case_id", "")).strip()
    }

    allowed_clean = _remove_bare_articles_when_subarticles_exist(
        _coerce_article_list(allowed_articles or [])
    )

    selected_sims = [
        _case_similarity(stage_a_by_id.get(case_id, {}))
        for case_id in selected
        if case_id in stage_a_by_id
    ]
    selected_sims = [sim for sim in selected_sims if sim is not None]

    if selected_sims:
        best_similarity = max(selected_sims)
    else:
        all_sims = [_case_similarity(case) for case in stage_a_cases or []]
        all_sims = [sim for sim in all_sims if sim is not None]
        best_similarity = max(all_sims) if all_sims else None

    relative_floor = (
        max(0.70, best_similarity - 0.08)
        if best_similarity is not None
        else 0.70
    )

    supplemented: list[str] = []
    rejected_candidates: list[dict[str, Any]] = []

    if len(selected) < minimum_cases:
        for case in stage_a_cases or []:
            if len(selected) >= minimum_cases or len(selected) >= maximum_cases:
                break

            case_id = str(case.get("case_id", "")).strip()
            if not case_id or case_id in selected:
                continue

            similarity = _case_similarity(case)
            shares_articles = _case_shares_any_article(case, allowed_clean)

            close_enough = (
                similarity is not None
                and similarity >= relative_floor
                and shares_articles
            )

            if close_enough:
                selected.append(case_id)
                supplemented.append(case_id)
            else:
                rejected_candidates.append({
                    "case_id": case_id,
                    "similarity": similarity,
                    "shares_articles": shares_articles,
                    "reason": "not_close_enough_for_automatic_supplement",
                })

    if not selected:
        for case in (stage_a_cases or [])[:min(minimum_cases, maximum_cases)]:
            case_id = str(case.get("case_id", "")).strip()
            if case_id and case_id not in selected:
                selected.append(case_id)
                supplemented.append(case_id)

    selection_mode = "llm_selected_only"
    if supplemented:
        selection_mode = "llm_selected_plus_close_stage_a_supplement"
    if len(selected) == 1:
        selection_mode = "single_case_selected"

    profile = {
        "selection_mode": selection_mode,
        "minimum_target": minimum_cases,
        "maximum_target": maximum_cases,
        "best_similarity": best_similarity,
        "relative_similarity_floor": relative_floor,
        "llm_case_ids": selected_case_ids,
        "supplemented_case_ids": supplemented,
        "final_case_ids": selected,
        "rejected_supplement_candidates": rejected_candidates[:10],
        "note": (
            "Additional Stage A cases were added only if they were close in "
            "similarity and shared at least one Step 4 article."
        ),
    }

    return selected[:maximum_cases], profile


def _merge_stage_a_metadata_into_stage_b_cases(
    stage_b_cases: list[dict],
    stage_a_cases: list[dict],
) -> list[dict]:
    """
    Stage B direct fetches may not carry similarity scores. Reattach Stage A
    similarity/article metadata so Steps 8–10 can judge precedent strength.
    """
    stage_a_by_id = {
        str(case.get("case_id", "")).strip(): case
        for case in stage_a_cases or []
        if str(case.get("case_id", "")).strip()
    }
    stage_a_order = list(stage_a_by_id.keys())

    merged: list[dict] = []
    for index, case in enumerate(stage_b_cases or [], start=1):
        if not isinstance(case, dict):
            continue

        case_id = str(case.get("case_id", "")).strip()
        source = stage_a_by_id.get(case_id, {})
        updated = dict(case)

        if updated.get("similarity") is None and source.get("similarity") is not None:
            updated["similarity"] = source.get("similarity")
        if not updated.get("articles_cited") and source.get("articles_cited"):
            updated["articles_cited"] = source.get("articles_cited")
        if not updated.get("legal_topic") and source.get("legal_topic"):
            updated["legal_topic"] = source.get("legal_topic")

        updated["stage_b_rank"] = index
        if source:
            try:
                updated["stage_a_rank"] = stage_a_order.index(case_id) + 1
            except ValueError:
                updated["stage_a_rank"] = None

        merged.append(updated)

    return merged


def _case_similarity(case: dict) -> float | None:
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


def _case_article_set(case: dict) -> set[str]:
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
        articles = _coerce_article_list(raw)
    else:
        articles = _coerce_article_list(str(raw).split(","))

    if not articles and raw:
        articles = re.findall(
            r"(?<![A-Za-z0-9])(\d+(?:\([0-9A-Za-z]+\))*)(?![A-Za-z0-9])",
            str(raw),
        )

    return set(_remove_bare_articles_when_subarticles_exist(articles))


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

    Sibling sub-articles are intentionally not equivalent: 13(1) is not
    article-specific support for rejecting 13(2).
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

            if similarity_value is not None and similarity_value < similarity_floor:
                continue

            for case_article in _coerce_article_list(case.get("articles", [])):
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


def _has_article_specific_negative_support(article: str, case_profile: dict[str, Any]) -> bool:
    """Return True when selected negative precedent supports rejecting this article."""
    support_map = case_profile.get("article_specific_negative_support", {})
    if not isinstance(support_map, dict):
        return False

    support = support_map.get(str(article or "").strip(), {})
    return bool(isinstance(support, dict) and support.get("has_support"))


def _case_shares_any_article(case: dict, allowed_articles: list[str]) -> bool:
    """Return True if a case shares at least one Step 4 article."""
    allowed = set(_remove_bare_articles_when_subarticles_exist(
        _coerce_article_list(allowed_articles or [])
    ))
    if not allowed:
        return True
    return bool(_case_article_set(case) & allowed)


# ─────────────────────────────────────────────────────────────
# STEP 8 — PRECEDENT ANALYSIS [CONTROLLED LANGCHAIN CONTEXT]
# ─────────────────────────────────────────────────────────────

async def run_step_8(intake: dict, stage_b_cases: list[dict]) -> dict:
    """
    Step 8 — Precedent analysis.

    No new retrieval. Uses Stage B cases from Step 7.
    LangChain assembles the context prompt and calls Gemini.
    """
    if not stage_b_cases:
        return {
            "step":        "step_8",
            "answer":      (
                "No similar cases were available for precedent analysis. "
                "The assessment will proceed without case law comparison."
            ),
            "explanation": _explain_step_8("", stage_b_cases),
            "passed":      True,
            "data":        {"stage_b_cases": stage_b_cases},
        }

    question = """For each of the cases provided, analyze the following:

1. KEY SIMILAR FACTS: Which specific facts in this case are similar to the current situation?

2. COURT DECISION: Did the court find a violation or not? What was the outcome?

3. RATIO DECIDENDI: What was the core legal reasoning that drove the court's decision? What principle did the court apply?

4. RELEVANCE TO CURRENT SITUATION: Based on this case, what does the precedent suggest about how a court might view the current situation?

Analyze each case separately. Use only the information provided in the case summaries above."""

    cases_formatted = format_cases_for_prompt(stage_b_cases)
    prompt  = _base_prompt_template()
    llm     = _get_llm(max_tokens=2000)

    prompt_inputs = _make_prompt_inputs(
        step_number=8,
        step_question=question,
        intake=intake,
        intake_fields={
            "What happened": intake.get("what_happened"),
            "Harm suffered": intake.get("harm_suffered"),
        },
        retrieved_content=cases_formatted,
        retrieved_label="SIMILAR CASES SELECTED FROM STEP 7",
    )

    try:
        response = await _ainvoke_llm_with_retries(
            prompt | llm,
            prompt_inputs,
            step_name="step_8",
        )
        answer   = _extract_llm_text(response)
    except Exception as e:
        raise RuntimeError(f"Controlled LangChain Step 8 failed: {e}")

    return {
        "step":        "step_8",
        "answer":      answer,
        "explanation": _explain_step_8(answer, stage_b_cases),
        "passed":      True,
        "data":        {"stage_b_cases": stage_b_cases},
    }


# ─────────────────────────────────────────────────────────────
# STEP 9 — CROSS-VALIDATION [CONTROLLED LANGCHAIN CONTEXT]
# ─────────────────────────────────────────────────────────────

async def run_step_9(
    intake:               dict,
    stage_b_cases:        list[dict],
    articles_from_step_4: list[str],
) -> dict:
    """
    Step 9 — Structured cross-validation and no-violation guard.

    This step is the hallucination-control checkpoint. It no longer returns
    only prose. It returns machine-readable article-level recommendations so
    Step 10 and runner.py can deterministically downgrade or reject weak
    candidate articles instead of treating every Step 4 candidate as finally
    supported.
    """
    allowed_articles = _remove_bare_articles_when_subarticles_exist(
        _coerce_article_list(articles_from_step_4 or [])
    )
    case_outcome_profile = _build_case_outcome_profile(stage_b_cases, allowed_articles=allowed_articles)

    if not stage_b_cases:
        structured = _normalize_step_9_structured_output(
            {},
            allowed_articles=allowed_articles,
            stage_b_cases=stage_b_cases,
        )
        answer = json.dumps(structured, ensure_ascii=False)
        return {
            "step":        "step_9",
            "answer":      answer,
            "explanation": _explain_step_9(True, False),
            "passed":      True,
            "data": {
                "consistent":            True,
                "inconsistencies_found": False,
                "stage_b_cases":         stage_b_cases,
                "case_outcome_profile":  case_outcome_profile,
                "structured_cross_validation": structured,
                **structured,
            },
        }

    cases_formatted = format_cases_for_prompt(stage_b_cases)
    step_4_articles = (
        ", ".join(allowed_articles)
        if allowed_articles else "None identified"
    )

    question = f"""PRIOR STEP 4 FINDING:
The earlier rights identification step identified these candidate articles: {step_4_articles}

YOUR TASK — Structured Independent Cross-Validation:
Use ONLY the selected case summaries below and the user facts in this prompt.

You must output ONLY one valid JSON object. Do not use markdown. Do not add text outside the JSON.

Purpose:
Step 4 is intentionally broad. Your job is to prevent overclaiming by deciding, article by article, whether the case-law pattern supports, weakly supports, does not support, or contradicts each Step 4 candidate.

Important legal safety rule:
A constitutional article being engaged, arguable, or mentioned is NOT enough to classify the claim as likely viable. If similar selected cases mostly resulted in NOT_VIOLATED, DISMISSED, PROCEDURAL_FAILURE, or no final violation, treat the current claim as not viable unless the current facts are clearly stronger than those negative cases.

Single-precedent rule for Himikama's small domain corpus:
A single selected case may be meaningful, but only if it is a close factual match and shares the same specific article. Do not treat one loosely related case as decisive. If only one case is selected and its factual/article match is not clearly strong, use case_viability_pattern="single_weak", recommended_overall_assessment="weak_or_uncertain", and prefer articles_to_downgrade rather than articles_to_reject. If one case is clearly very close and article-matched, use single_strong_positive or single_strong_negative depending on its judgment.

Classify each Step 4 article using this exact logic:
- supported: selected precedent positively supports a final violation on closely analogous facts.
- weak_or_uncertain: the article is legally relevant but support is limited, indirect, mixed, fact-dependent, or incomplete.
- rejected: the article is unrelated, legally unsupported, contradicted by the selected cases, or the required factual/legal elements are missing.

Use this exact JSON schema:

{{
  "overall_inconsistency_found": false,
  "negative_precedent_pattern": false,
  "article_specific_negative_support": {{}},
  "current_facts_stronger_than_negative_cases": false,
  "case_viability_pattern": "positive | weak | negative | mixed | unclear | no_cases | single_strong_positive | single_strong_negative | single_weak",
  "recommended_overall_assessment": "likely_viable | weak_or_uncertain | not_viable",
  "articles_to_keep_supported": [],
  "articles_to_downgrade": [],
  "articles_to_reject": [],
  "article_cross_validation": [
    {{
      "article": "",
      "case_law_support": "supports | weak | unsupported | contradicts | no_cases",
      "recommended_final_status": "supported | weak_or_uncertain | rejected",
      "reason": ""
    }}
  ],
  "faithfulness_notes": []
}}

Rules:
1. Every article in the Step 4 list must appear exactly once in article_cross_validation.
2. Article values must be copied exactly from the Step 4 candidate list.
3. Do not introduce new article IDs.
4. articles_to_keep_supported may contain only articles whose recommended_final_status is supported.
5. articles_to_downgrade may contain only articles whose recommended_final_status is weak_or_uncertain.
6. articles_to_reject may contain only articles whose recommended_final_status is rejected.
7. If selected cases are mostly negative and current facts are not clearly stronger, set negative_precedent_pattern=true and recommended_overall_assessment="not_viable" or "weak_or_uncertain".
8. Use not_viable when the case pattern is negative and no article is strongly supported.
9. Use weak_or_uncertain when the case pattern is mixed or support is indirect.
10. Use likely_viable only when at least one article is strongly supported by selected positive precedent.
11. A single case can support likely_viable or not_viable only when it is clearly close and article-matched. Otherwise classify the precedent pattern as single_weak and the overall assessment as weak_or_uncertain.
12. Do not reject all candidate articles based only on one weak negative case. Use downgrade unless the article is clearly legally inapplicable or contradicted.
13. Article-specific rejection rule: do not reject an article merely because the overall selected-case pattern is negative. Reject an article only when the negative precedent concerns that same specific article or a direct parent/sub-article equivalent. For example, negative cases about 13(1), 12(1), or 14(1)(b) must not by themselves reject 13(2).
14. If the negative precedent is broad or article-mismatched, classify the article as weak_or_uncertain rather than rejected.
15. Be strict. Do not preserve an article as supported merely because Step 4 identified it."""

    prompt = _base_prompt_template()
    llm = _get_llm(max_tokens=2500)

    prompt_inputs = _make_prompt_inputs(
        step_number=9,
        step_question=question,
        intake=intake,
        intake_fields={"What happened": intake.get("what_happened")},
        retrieved_content=cases_formatted,
        retrieved_label="SELECTED SIMILAR CASES FOR STRUCTURED CROSS-VALIDATION",
    )

    try:
        response = await _ainvoke_llm_with_retries(
            prompt | llm,
            prompt_inputs,
            step_name="step_9",
        )
        raw_answer = _extract_llm_text(response)
    except Exception as e:
        raise RuntimeError(f"Controlled LangChain Step 9 failed: {e}")

    structured = _parse_step_9_structured_output(
        raw_answer,
        allowed_articles=allowed_articles,
        stage_b_cases=stage_b_cases,
    )

    inconsistency_found = bool(
        structured.get("overall_inconsistency_found")
        or structured.get("articles_to_downgrade")
        or structured.get("articles_to_reject")
        or structured.get("negative_precedent_pattern")
    )

    answer = json.dumps(structured, ensure_ascii=False)

    return {
        "step":        "step_9",
        "answer":      answer,
        "explanation": _explain_step_9(not inconsistency_found, inconsistency_found),
        "passed":      True,
        "data": {
            "consistent":            not inconsistency_found,
            "inconsistencies_found": inconsistency_found,
            "stage_b_cases":         stage_b_cases,
            "case_outcome_profile":  case_outcome_profile,
            "raw_structured_cross_validation": raw_answer,
            "structured_cross_validation": structured,
            **structured,
        },
    }


def _parse_step_9_structured_output(
    raw_output: str,
    *,
    allowed_articles: list[str],
    stage_b_cases: list[dict],
) -> dict[str, Any]:
    """
    Parse Step 9 JSON and normalize it into a strict article-level safety map.
    """
    parsed = _extract_json_object(raw_output)
    if not isinstance(parsed, dict):
        parsed = {}

    return _normalize_step_9_structured_output(
        parsed,
        allowed_articles=allowed_articles,
        stage_b_cases=stage_b_cases,
    )


def _normalize_step_9_structured_output(
    raw: dict[str, Any],
    *,
    allowed_articles: list[str],
    stage_b_cases: list[dict],
) -> dict[str, Any]:
    """
    Normalize Step 9 cross-validation.

    This function deliberately prefers conservative safety defaults when the
    selected precedent pattern is negative. It does not use gold labels.
    """
    allowed_clean = _remove_bare_articles_when_subarticles_exist(
        _coerce_article_list(allowed_articles or [])
    )
    allowed_set = set(allowed_clean)
    case_profile = _build_case_outcome_profile(stage_b_cases, allowed_articles=allowed_clean)

    def has_specific_negative_support(article: str) -> bool:
        return _has_article_specific_negative_support(article, case_profile)

    raw = raw if isinstance(raw, dict) else {}

    def _safe_bool(value: Any) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in {"true", "yes", "1"}
        return bool(value)

    case_viability_pattern = str(
        raw.get("case_viability_pattern", "") or ""
    ).strip().lower()
    if case_viability_pattern not in {
        "positive",
        "weak",
        "negative",
        "mixed",
        "unclear",
        "no_cases",
        "single_strong_positive",
        "single_strong_negative",
        "single_weak",
    }:
        evidence_strength = str(case_profile.get("evidence_strength", "") or "")
        if not stage_b_cases:
            case_viability_pattern = "no_cases"
        elif evidence_strength == "single_strong_case":
            direction = str(case_profile.get("single_case_direction", "") or "")
            case_viability_pattern = (
                "single_strong_negative"
                if direction == "negative"
                else "single_strong_positive"
            )
        elif evidence_strength == "single_weak_case":
            case_viability_pattern = "single_weak"
        elif evidence_strength == "multi_case_negative":
            case_viability_pattern = "negative"
        elif evidence_strength == "multi_case_positive":
            case_viability_pattern = "positive"
        elif case_profile.get("positive_cases", 0) > 0 and case_profile.get("negative_cases", 0) > 0:
            case_viability_pattern = "mixed"
        else:
            case_viability_pattern = "unclear"

    evidence_strength = str(case_profile.get("evidence_strength", "") or "")
    raw_negative_precedent = _safe_bool(raw.get("negative_precedent_pattern", False))

    # A single weak negative precedent should not become a binding negative
    # pattern. It can justify downgrade, not automatic rejection/not_viable.
    if evidence_strength == "single_weak_case" and case_viability_pattern in {
        "negative",
        "single_strong_negative",
    }:
        case_viability_pattern = "single_weak"

    negative_precedent_pattern = bool(
        case_profile.get("strong_negative_precedent")
        or (
            raw_negative_precedent
            and evidence_strength not in {"single_weak_case", "no_cases", "unclassified_cases"}
        )
        or case_viability_pattern in {"negative", "single_strong_negative"}
    )

    current_facts_stronger = _safe_bool(
        raw.get("current_facts_stronger_than_negative_cases", False)
    )

    recommended_overall = str(
        raw.get("recommended_overall_assessment", "") or ""
    ).strip().lower()
    if recommended_overall not in {
        "likely_viable",
        "weak_or_uncertain",
        "not_viable",
    }:
        if negative_precedent_pattern and not current_facts_stronger:
            recommended_overall = "not_viable"
        elif case_viability_pattern in {"mixed", "weak", "unclear", "no_cases", "single_weak"}:
            recommended_overall = "weak_or_uncertain"
        elif case_viability_pattern == "single_strong_negative" and not current_facts_stronger:
            recommended_overall = "not_viable"
        else:
            recommended_overall = "likely_viable"

    raw_cross = raw.get("article_cross_validation", [])
    if not isinstance(raw_cross, list):
        raw_cross = []

    cross_by_article: dict[str, dict[str, Any]] = {}

    allowed_support_values = {
        "supports",
        "weak",
        "unsupported",
        "contradicts",
        "no_cases",
    }
    allowed_status_values = {
        "supported",
        "weak_or_uncertain",
        "rejected",
    }

    for item in raw_cross:
        if not isinstance(item, dict):
            continue

        article = _normalize_article_label(item.get("article", ""))
        if article not in allowed_set:
            repaired = _repair_article_against_allowed_articles(
                article,
                supporting_text=json.dumps(item, ensure_ascii=False),
                allowed_articles=allowed_clean,
            )
            article = repaired[0] if repaired else ""

        if article not in allowed_set or article in cross_by_article:
            continue

        support = str(item.get("case_law_support", "") or "").strip().lower()
        if support not in allowed_support_values:
            support = "weak"

        status = str(item.get("recommended_final_status", "") or "").strip().lower()
        if status not in allowed_status_values:
            if support == "supports":
                status = "supported"
            elif support in {"unsupported", "contradicts"}:
                status = "rejected"
            else:
                status = "weak_or_uncertain"

        # Single weak precedent is not enough to finally support or reject.
        # It should usually downgrade to weak_or_uncertain.
        if evidence_strength == "single_weak_case" and status in {"supported", "rejected"}:
            status = "weak_or_uncertain"
            if support in {"supports", "contradicts", "unsupported"}:
                support = "weak"

        # Article-specific rejection rule: a broad negative pattern can
        # downgrade an article, but cannot reject it unless a selected negative
        # case concerns the same specific article or a direct parent/sub-article
        # equivalent. Example: 13(1) does not reject 13(2).
        if status == "rejected" and not has_specific_negative_support(article):
            status = "weak_or_uncertain"
            if support in {"unsupported", "contradicts"}:
                support = "weak"

        # Conservative override: strong negative precedent without stronger
        # facts cannot recommend support unless the LLM clearly said support.
        if (
            negative_precedent_pattern
            and not current_facts_stronger
            and status == "supported"
            and support != "supports"
        ):
            status = (
                "rejected"
                if case_profile.get("all_negative") and has_specific_negative_support(article)
                else "weak_or_uncertain"
            )

        cross_by_article[article] = {
            "article": article,
            "case_law_support": support,
            "recommended_final_status": status,
            "reason": str(item.get("reason", "") or "").strip(),
        }

    # Complete omitted Step 4 candidates conservatively.
    for article in allowed_clean:
        if article in cross_by_article:
            continue

        if not stage_b_cases:
            support = "no_cases"
            status = "weak_or_uncertain"
        elif negative_precedent_pattern and not current_facts_stronger:
            support = "unsupported"
            status = (
                "rejected"
                if case_profile.get("all_negative") and has_specific_negative_support(article)
                else "weak_or_uncertain"
            )
        elif evidence_strength == "single_weak_case":
            support = "weak"
            status = "weak_or_uncertain"
        else:
            support = "weak"
            status = "weak_or_uncertain"

        cross_by_article[article] = {
            "article": article,
            "case_law_support": support,
            "recommended_final_status": status,
            "reason": (
                "Step 9 did not provide strong positive selected-case support "
                "for this Step 4 candidate, so it is not treated as finally supported."
            ),
        }

    article_cross_validation = [cross_by_article[a] for a in allowed_clean if a in cross_by_article]

    keep_supported = [
        item["article"]
        for item in article_cross_validation
        if item["recommended_final_status"] == "supported"
    ]
    downgrade = [
        item["article"]
        for item in article_cross_validation
        if item["recommended_final_status"] == "weak_or_uncertain"
    ]
    reject = [
        item["article"]
        for item in article_cross_validation
        if item["recommended_final_status"] == "rejected"
    ]

    # Raw lists can only make the result stricter, not looser.
    raw_downgrade = _filter_articles_from_allowed(
        raw.get("articles_to_downgrade", []),
        allowed_clean,
    )
    raw_reject = _filter_articles_from_allowed(
        raw.get("articles_to_reject", []),
        allowed_clean,
    )

    # A raw Step 9 rejection based on a single weak precedent is treated as a
    # downgrade. This preserves caution without killing a potentially viable
    # claim merely because one loosely related case was negative. The same
    # downgrade conversion applies when the rejection is not article-specific.
    converted_raw_reject_to_downgrade: list[str] = []
    if evidence_strength == "single_weak_case":
        converted_raw_reject_to_downgrade.extend(raw_reject)
        raw_reject = []
    else:
        kept_raw_reject: list[str] = []
        for article in raw_reject:
            if has_specific_negative_support(article):
                kept_raw_reject.append(article)
            else:
                converted_raw_reject_to_downgrade.append(article)
        raw_reject = kept_raw_reject

    raw_downgrade = _dedupe_preserve_order(raw_downgrade + converted_raw_reject_to_downgrade)

    for article in raw_reject:
        if article not in reject:
            reject.append(article)
        if article in keep_supported:
            keep_supported.remove(article)
        if article in downgrade:
            downgrade.remove(article)

    for article in raw_downgrade:
        if article not in reject and article not in downgrade:
            downgrade.append(article)
        if article in keep_supported:
            keep_supported.remove(article)

    keep_supported = _dedupe_preserve_order([
        article for article in keep_supported
        if article in allowed_set and article not in reject and article not in downgrade
    ])
    downgrade = _dedupe_preserve_order([
        article for article in downgrade
        if article in allowed_set and article not in reject and article not in keep_supported
    ])
    reject = _dedupe_preserve_order([
        article for article in reject
        if (
            article in allowed_set
            and article not in keep_supported
            and article not in downgrade
            and has_specific_negative_support(article)
        )
    ])

    overall_inconsistency = (
        _safe_bool(raw.get("overall_inconsistency_found", False))
        or bool(downgrade)
        or bool(reject)
        or (negative_precedent_pattern and not current_facts_stronger)
    )

    return {
        "overall_inconsistency_found": overall_inconsistency,
        "negative_precedent_pattern": bool(negative_precedent_pattern),
        "article_specific_negative_support": case_profile.get(
            "article_specific_negative_support",
            {},
        ),
        "current_facts_stronger_than_negative_cases": bool(current_facts_stronger),
        "case_viability_pattern": case_viability_pattern,
        "recommended_overall_assessment": recommended_overall,
        "articles_to_keep_supported": keep_supported,
        "articles_to_downgrade": downgrade,
        "articles_to_reject": reject,
        "article_cross_validation": article_cross_validation,
        "case_outcome_profile": case_profile,
        "faithfulness_notes": _coerce_string_list(raw.get("faithfulness_notes", [])),
    }


def _filter_articles_from_allowed(raw_articles: Any, allowed_articles: list[str]) -> list[str]:
    """
    Keep only allowed Step 4 articles, repairing broad labels when possible.
    """
    output: list[str] = []
    for article in _coerce_article_list(raw_articles):
        if article in allowed_articles:
            output.append(article)
            continue
        repaired = _repair_article_against_allowed_articles(
            article,
            supporting_text=article,
            allowed_articles=allowed_articles,
        )
        output.extend(repaired)

    return _dedupe_preserve_order([
        article for article in output
        if article in set(allowed_articles)
    ])


def _build_case_outcome_profile(
    stage_b_cases: list[dict],
    *,
    allowed_articles: list[str] | None = None,
) -> dict[str, Any]:
    """
    Summarize selected Step 7 precedent outcomes.

    This is deterministic and uses only retrieved case metadata. It now
    distinguishes a genuinely strong single precedent from a weak single
    precedent, which matters because Himikama's corpus is domain-specific and
    not large enough to require multiple precedents in every situation.
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

    allowed_clean = _remove_bare_articles_when_subarticles_exist(
        _coerce_article_list(allowed_articles or [])
    )

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
            shared_articles = sorted(_case_article_set(case) & set(allowed_clean))
            article_overlap = bool(shared_articles) if allowed_clean else True
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
                "articles": sorted(_case_article_set(case)),
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
        evidence_strength = (
            "single_strong_case"
            if single_case_is_strong
            else "single_weak_case"
        )
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
        set(allowed_clean),
        direction="negative",
    )
    article_specific_positive_support = _build_article_specific_support_profile(
        classified_cases,
        set(allowed_clean),
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


# ─────────────────────────────────────────────────────────────
# STEP 10 — FINAL SYNTHESIS [CONTROLLED LANGCHAIN LLM]
# ─────────────────────────────────────────────────────────────

async def run_step_10(
    intake: dict,
    all_answers: dict,
    allowed_articles: list[str] | None = None,
    selected_case_ids: list[str] | None = None,
) -> dict:
    """
    Step 10 — Final synthesis.

    Receives all prior step answers and synthesizes the final legal
    assessment into structured JSON.

    Important design choice:
        The LLM returns machine-readable JSON only.
        Python then renders the human-readable final answer from that
        JSON. This prevents the JSON and the final answer from
        contradicting each other.

    Step 10 does NOT retrieve new material.
    Step 10 must use only the completed reasoning chain passed in
    all_answers.
    """
    chain_summary = _build_chain_summary(all_answers)

    # Prefer exact structured values passed by runner.py.
    # Fallback to prose extraction only for backwards compatibility.
    allowed_step_4_articles = _remove_bare_articles_when_subarticles_exist(
        _coerce_article_list(allowed_articles or [])
    )
    if not allowed_step_4_articles:
        allowed_step_4_articles = _extract_allowed_step_4_articles_from_all_answers(
            all_answers,
            chain_summary,
        )

    selected_step_7_case_ids = _coerce_string_list(selected_case_ids or [])
    if not selected_step_7_case_ids:
        selected_step_7_case_ids = _extract_selected_step_7_case_ids_from_all_answers(
            all_answers,
            chain_summary,
        )

    allowed_step_4_articles_text = _format_allowed_articles_for_step_10_prompt(
        allowed_step_4_articles
    )
    selected_step_7_case_ids_text = _format_selected_case_ids_for_step_10_prompt(
        selected_step_7_case_ids
    )

    question = """You are now synthesizing the findings from a complete structured legal reasoning chain into a final assessment.

You must output ONLY one valid JSON object. Do not use markdown. Do not wrap the JSON in a code block. Do not add any explanation outside the JSON.

CRITICAL RESTRICTION RULES:
1. Only classify articles that were already identified in Step 4.
2. You must use the exact Step 4 article IDs listed below.
3. Do not collapse specific sub-articles into broad parent articles.
4. Do not convert "13(1)" or "13(2)" into "13".
5. Do not convert "14(1)(g)" into "14".
6. Only use supporting case IDs that were already selected in Step 7.
7. Article status must be exactly one of:

ALLOWED STEP 4 ARTICLE IDS:
__ALLOWED_STEP_4_ARTICLES__

SELECTED STEP 7 CASE IDS:
__SELECTED_STEP_7_CASE_IDS__

If an article ID is not listed above, do not use it.
If both a broad article and specific sub-articles appear, prefer the specific sub-articles.
For example, if "13(1)" and "13(2)" are listed, do not output "13" unless "13" itself is listed.
For example, if "14(1)(g)" is listed, do not output "14" unless "14" itself is listed.
   - supported
   - weak_or_uncertain
   - rejected

Do not invent new constitutional articles.
Do not invent new case IDs.
Do not invent facts.
Do not cite law or cases not present in the reasoning chain.
Use cautious legal language in reasons.

STRICT FINAL SUPPORT RULE:
Do not classify an article as "supported" merely because it was identified in Step 4, mentioned by the user, engaged by the facts, or arguable in the abstract.

An article may be classified as "supported" only if:
1. The core factual/legal elements of that article are clearly present;
2. Step 8 precedent analysis positively supports that article on closely analogous facts;
3. Step 9 does not recommend downgrading or rejecting that article;
4. Step 9 does not mark the case pattern as negative unless it also says the current facts are clearly stronger than the negative precedent;
5. The article is not merely a broad fairness, hardship, or procedural complaint.

NO-VIOLATION / NOT-VIABLE CONTROL RULE:
If Step 9 reports a negative_precedent_pattern, case_viability_pattern="negative", or recommended_overall_assessment="not_viable", you must not classify the current claim as likely_viable unless Step 9 also states current_facts_stronger_than_negative_cases=true and identifies at least one article in articles_to_keep_supported.

If selected similar cases mostly resulted in NOT_VIOLATED, DISMISSED, PROCEDURAL_FAILURE, or no final violation, treat the current case cautiously:
- Do not classify articles as "supported" unless the current facts are clearly stronger than those negative cases.
- If the current facts are similar to negative precedent, classify the article as weak_or_uncertain or rejected.
- If no article remains supported after this check, do not set overall_assessment to likely_viable.

STEP 9 BINDING SAFETY RULE:
When Step 9 provides structured cross-validation:
- articles_to_reject may be classified as rejected only when Step 9/runner evidence shows article-specific negative support for that same article, unless the article is plainly factually or legally unrelated.
- If Step 9's rejection is based on broad negative precedent that does not match the same specific article, classify the article as weak_or_uncertain rather than rejected.
- articles_to_downgrade must be classified as weak_or_uncertain unless Step 8 gives explicit stronger positive support.
- articles_to_keep_supported may be supported only if Step 8 also supports them.
- recommended_overall_assessment must be followed unless the Step 8 evidence clearly contradicts it.

Your task:
Read Steps 1–9 and classify the Step 4 candidate articles into:
- supported: the article remains strongly supported after precedent analysis and cross-validation.
- weak_or_uncertain: the article may apply, but support is limited, unclear, mixed, indirect, incomplete, or fact-dependent.
- rejected: the article was considered but should not be presented as a final potentially violated article because the facts/legal elements or selected precedent do not support it.

When setting the top-level precedent_alignment, consider only the supported articles, not the weak_or_uncertain or rejected candidate articles.

Use this exact JSON schema:

{
  "final_potentially_violated_articles": [],
  "final_weak_or_uncertain_articles": [],
  "final_rejected_articles": [],
  "overall_assessment": "likely_viable | weak_or_uncertain | not_viable | time_barred | not_state_actor",
  "precedent_alignment": "supports | mixed | weak | contradicts | no_cases | not_assessed",
  "article_assessments": [
    {
      "article": "",
      "status": "supported | weak_or_uncertain | rejected",
      "reason": "",
      "supporting_steps": [],
      "supporting_case_ids": [],
      "confidence": "high | medium | low"
    }
  ],
  "key_strengths": [],
  "key_weaknesses": [],
  "faithfulness_notes": []
}

Field rules:
- Article values must use canonical article IDs only, such as "13(1)", "13(2)", or "14(1)(g)".
- Article values must be copied exactly from ALLOWED STEP 4 ARTICLE IDS.
- Do not prefix article values with the word "Article".
- Do not output broad parent articles such as "13" or "14" when the allowed list contains only specific sub-articles such as "13(1)" or "14(1)(g)".
- final_potentially_violated_articles must contain only articles with status "supported".
- final_weak_or_uncertain_articles must contain only articles with status "weak_or_uncertain".
- final_rejected_articles must contain only articles with status "rejected".
- Every article listed in ALLOWED STEP 4 ARTICLE IDS must appear exactly once in article_assessments.
- Every article listed in ALLOWED STEP 4 ARTICLE IDS must appear in exactly one final bucket.
- Do not silently omit weak, uncertain, or rejected Step 4 candidate articles.
- article_assessments must include every article that you classify.
- supporting_steps must only contain step identifiers such as "step_4", "step_8", or "step_9".
- supporting_case_ids must only contain case IDs explicitly selected in Step 7.
- supporting_case_ids should identify selected cases that directly support or materially ground the reasoning for that specific article.
- If an article reason relies on precedent from a selected Step 7 case, include that selected case ID in supporting_case_ids.
- If only one Step 7 case ID is selected and the article is supported by precedent, use that case ID in supporting_case_ids.
- Do not include a case ID as supporting precedent for an article if the case is used only as a contradiction or distinguishable negative example.
- Top-level precedent_alignment must measure only how retrieved precedent supports the final_potentially_violated_articles.
- Do not set top-level precedent_alignment to "mixed" merely because weak_or_uncertain or rejected Step 4 candidate articles lack precedent support.
- Use "supports" when all final_potentially_violated_articles are directly supported by selected Step 7 precedent.
- Use "mixed" only when final_potentially_violated_articles themselves have mixed precedent support.
- Use "weak" when final_potentially_violated_articles exist but selected precedent support is weak, indirect, or absent.
- If no similar cases were found, use precedent_alignment = "no_cases".
- If precedent was not assessed, use precedent_alignment = "not_assessed".
- If the petition appears blocked by Step 1, use overall_assessment = "time_barred".
- If the petition appears blocked by Step 2, use overall_assessment = "not_state_actor".
- If supported articles exist and weaknesses are limited, use overall_assessment = "likely_viable".
- If there are only weak/uncertain articles, use overall_assessment = "weak_or_uncertain".
- If no supported or weak/uncertain articles remain, use overall_assessment = "not_viable".
- If an article has factual/legal relevance but precedent support is limited, indirect, or absent, classify it as weak_or_uncertain rather than rejected.
- Use rejected only when the article is factually or legally unrelated to the core alleged infringement, or when selected negative precedent specifically concerns that same article/legal element.

Remember: output JSON only."""

    question = question.replace(
        "__ALLOWED_STEP_4_ARTICLES__",
        allowed_step_4_articles_text,
    )
    question = question.replace(
        "__SELECTED_STEP_7_CASE_IDS__",
        selected_step_7_case_ids_text,
    )

    synthesis_prompt = ChatPromptTemplate.from_messages([
        ("system", SYSTEM_PROMPT),
        ("human", """USER NARRATIVE (original):
{user_narrative}

COMPLETE REASONING CHAIN:
{chain_summary}

FINAL STRUCTURED SYNTHESIS TASK:
{step_question}"""),
    ])

    llm   = _get_llm(max_tokens=6000)
    chain = synthesis_prompt | llm

    raw_output = ""
    raw_outputs: list[str] = []
    structured_assessment: dict[str, Any] = _empty_structured_assessment()
    parsing_failed = True
    parsing_attempts = 0

    # Step 10 is the only step that must produce strict machine-readable JSON.
    # Gemini can occasionally return a short, incomplete JSON fragment even
    # when max_output_tokens is high. Do not accept that as a final evaluation
    # output. Retry the same legal synthesis task with a stricter JSON-only
    # repair instruction before falling back to the empty parse-failure object.
    max_step_10_json_attempts = 3

    for attempt in range(1, max_step_10_json_attempts + 1):
        parsing_attempts = attempt
        step_question_for_attempt = question

        if attempt > 1:
            step_question_for_attempt = (
                question
                + _build_step_10_json_retry_instruction(
                    previous_raw_output=raw_output,
                    attempt=attempt,
                    max_attempts=max_step_10_json_attempts,
                )
            )

        try:
            response = await _ainvoke_llm_with_retries(
                chain,
                {
                    "user_narrative": intake.get("user_narrative", ""),
                    "chain_summary":  chain_summary,
                    "step_question":  step_question_for_attempt,
                },
                step_name=f"step_10_json_attempt_{attempt}",
            )
            raw_output = _extract_llm_text(response)
            raw_outputs.append(raw_output)
        except Exception as e:
            raise RuntimeError(f"Controlled LangChain Step 10 failed: {e}")

        structured_assessment = _parse_step_10_structured_output(
            raw_output,
            allowed_articles=allowed_step_4_articles,
            selected_case_ids=selected_step_7_case_ids,
            reasoning_context_text=chain_summary,
        )

        parsing_failed = _step_10_structured_parsing_failed(
            structured_assessment
        )

        if not parsing_failed:
            if attempt > 1:
                logger.warning(
                    "Step 10 structured JSON parsing recovered on attempt %s.",
                    attempt,
                )
            break

        if attempt < max_step_10_json_attempts:
            logger.warning(
                "Step 10 structured JSON parsing failed on attempt %s. "
                "Retrying Step 10 JSON generation.",
                attempt,
            )
        else:
            logger.error(
                "Step 10 structured JSON parsing failed after %s attempts.",
                max_step_10_json_attempts,
            )
    final_answer = _render_final_answer_from_structured_assessment(
        structured_assessment
    )

    # If parsing failed completely, keep the raw model output as the answer
    # so the caller can inspect/debug it. The structured fields will be empty.
    if not final_answer.strip():
        final_answer = raw_output

    return {
        "step":        "step_10",
        "answer":      final_answer,
        "explanation": _explain_step_10(final_answer),
        "passed":      True,
        "data": {
            "structured_assessment": structured_assessment,
            "final_potentially_violated_articles": structured_assessment.get(
                "final_potentially_violated_articles",
                [],
            ),
            "final_weak_or_uncertain_articles": structured_assessment.get(
                "final_weak_or_uncertain_articles",
                [],
            ),
            "final_rejected_articles": structured_assessment.get(
                "final_rejected_articles",
                [],
            ),
            "overall_assessment": structured_assessment.get(
                "overall_assessment",
                "",
            ),
            "precedent_alignment": structured_assessment.get(
                "precedent_alignment",
                "",
            ),
            "article_assessments": structured_assessment.get(
                "article_assessments",
                [],
            ),
            "raw_structured_output": raw_output,
            "raw_structured_outputs": raw_outputs,
            "step_10_json_parse_attempts": parsing_attempts,
            "step_10_json_retry_used": parsing_attempts > 1,
            "step_10_json_parse_failed": parsing_failed,
        },
    }


def _parse_step_10_structured_output(
    raw_output: str,
    allowed_articles: list[str] | None = None,
    selected_case_ids: list[str] | None = None,
    reasoning_context_text: str = "",
) -> dict[str, Any]:
    """
    Parse and normalize the JSON object returned by Step 10.

    This function deliberately avoids guessing legal conclusions.
    If JSON parsing fails, it returns an empty structured assessment.
    runner.py will also perform a second safety-normalization pass.
    """
    parsed = _extract_json_object(raw_output)

    if not isinstance(parsed, dict):
        logger.warning("Step 10 structured JSON parsing failed.")
        return _empty_structured_assessment()

    return _normalize_step_10_structured_assessment(
        parsed,
        allowed_articles=allowed_articles,
        selected_case_ids=selected_case_ids,
        reasoning_context_text=reasoning_context_text,
    )


def _extract_json_object(text: str) -> dict[str, Any] | None:
    """
    Extract the first valid JSON object from a model response.

    Handles responses with or without ```json fences.
    """
    if not text:
        return None

    cleaned = text.strip()

    # Remove common markdown fences if the model ignores instructions.
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned)

    # First try direct parsing.
    try:
        loaded = json.loads(cleaned)
        return loaded if isinstance(loaded, dict) else None
    except json.JSONDecodeError:
        pass

    # Fallback: find the first JSON object using raw_decode.
    decoder = json.JSONDecoder()
    for index, char in enumerate(cleaned):
        if char != "{":
            continue
        try:
            loaded, _ = decoder.raw_decode(cleaned[index:])
            return loaded if isinstance(loaded, dict) else None
        except json.JSONDecodeError:
            continue

    return None


def _empty_structured_assessment() -> dict[str, Any]:
    """
    Empty structured Step 10 assessment.

    Used only when Step 10 JSON parsing fails.
    """
    return {
        "final_potentially_violated_articles": [],
        "final_weak_or_uncertain_articles": [],
        "final_rejected_articles": [],
        "overall_assessment": "",
        "precedent_alignment": "",
        "article_assessments": [],
        "key_strengths": [],
        "key_weaknesses": [
            "Step 10 did not return valid structured JSON, so no final "
            "machine-readable article conclusion could be extracted."
        ],
        "faithfulness_notes": [
            "Structured parsing failed. Review raw_structured_output for debugging."
        ],
    }


def _step_10_structured_parsing_failed(
    structured_assessment: dict[str, Any],
) -> bool:
    """
    True only when Step 10 failed to produce parseable structured JSON.

    A legally valid no-violation answer may legitimately contain no final
    supported articles, so emptiness alone is not enough. We identify parser
    failure using the sentinel text produced by _empty_structured_assessment().
    """
    if not isinstance(structured_assessment, dict):
        return True

    weaknesses = structured_assessment.get("key_weaknesses", [])
    notes = structured_assessment.get("faithfulness_notes", [])

    if not isinstance(weaknesses, list):
        weaknesses = [weaknesses]
    if not isinstance(notes, list):
        notes = [notes]

    has_parse_failure_marker = any(
        "Step 10 did not return valid structured JSON" in str(item)
        for item in weaknesses
    ) or any(
        "Structured parsing failed" in str(item)
        for item in notes
    )

    if has_parse_failure_marker:
        return True

    # If the parser returned no article assessments and no overall assessment,
    # it is also unusable for the final evaluation output.
    return (
        not structured_assessment.get("overall_assessment")
        and not structured_assessment.get("article_assessments")
    )


def _build_step_10_json_retry_instruction(
    *,
    previous_raw_output: str,
    attempt: int,
    max_attempts: int,
) -> str:
    """
    Build a retry-only instruction for Step 10 JSON repair.

    This does not add new legal facts or change the legal task. It simply tells
    the model that the previous response was not valid complete JSON and asks
    it to regenerate the same schema as one complete JSON object.
    """
    previous_tail = (previous_raw_output or "")[-1200:]

    return f"""

STRICT JSON RETRY INSTRUCTION — ATTEMPT {attempt} OF {max_attempts}:
Your previous Step 10 response could not be parsed as complete valid JSON.
It may have been truncated, malformed, wrapped in markdown, or cut off before
closing all braces and arrays.

Regenerate the SAME legal synthesis using the SAME reasoning chain and the SAME
allowed article IDs. Do not add new law, facts, articles, or case IDs.

Output requirements for this retry:
1. Output exactly one complete JSON object only.
2. Start with {{ and end with }}.
3. Do not use markdown fences.
4. Do not include comments or explanation outside the JSON.
5. Close every string, array, and object.
6. Keep reasons concise so the full JSON object completes.
7. Preserve the exact schema required above.

Previous invalid output tail for awareness only:
{previous_tail}
"""


def _extract_allowed_step_4_articles_from_all_answers(
    all_answers: dict,
    chain_summary: str,
) -> list[str]:
    """
    Prefer Step 4 structured data for allowed article IDs.

    Falling back to prose extraction is useful, but structured
    Step 4 data is safer because Step 10 must classify the exact
    candidates produced by Step 4.
    """
    if isinstance(all_answers, dict):
        step_4 = all_answers.get("step_4")

        if isinstance(step_4, dict):
            data = step_4.get("data", {})
            if isinstance(data, dict):
                structured_articles = _coerce_article_list(
                    data.get("articles_identified", [])
                )
                if structured_articles:
                    return _remove_bare_articles_when_subarticles_exist(
                        structured_articles
                    )

        if isinstance(step_4, str):
            structured_articles = _extract_article_numbers(step_4)
            if structured_articles:
                return _remove_bare_articles_when_subarticles_exist(
                    structured_articles
                )

    return _extract_allowed_step_4_articles_from_chain_summary(chain_summary)


def _extract_selected_step_7_case_ids_from_all_answers(
    all_answers: dict,
    chain_summary: str,
) -> list[str]:
    """
    Extract selected Step 7 case IDs for Step 10 normalization.

    This is used to fill supporting_case_ids when Step 10 clearly
    relies on a selected precedent but omits the machine-readable ID.
    """
    case_ids: list[str] = []

    if isinstance(all_answers, dict):
        step_7 = all_answers.get("step_7")

        if isinstance(step_7, dict):
            data = step_7.get("data", {})
            if isinstance(data, dict):
                case_ids.extend(_coerce_string_list(data.get("case_ids", [])))

                for key in ("stage_b_cases", "cases"):
                    raw_cases = data.get(key, [])
                    if not isinstance(raw_cases, list):
                        continue

                    for case in raw_cases:
                        if not isinstance(case, dict):
                            continue
                        case_id = str(case.get("case_id", "")).strip()
                        if case_id:
                            case_ids.append(case_id)

        elif isinstance(step_7, str):
            case_ids.extend(_extract_case_ids_from_text(step_7))

    case_ids.extend(_extract_case_ids_from_text(chain_summary))

    return _dedupe_preserve_order([
        case_id
        for case_id in case_ids
        if case_id
    ])


def _extract_case_ids_from_text(text: str) -> list[str]:
    """
    Extract case IDs from Step 7/chain text.
    """
    if not text:
        return []

    matches = re.findall(
        r"\[case_id:\s*([0-9]+)\]|case_id\s*[:=]\s*['\"]?([0-9]+)",
        text,
        flags=re.IGNORECASE,
    )

    case_ids: list[str] = []
    for first, second in matches:
        case_id = first or second
        if case_id:
            case_ids.append(case_id)

    return _dedupe_preserve_order(case_ids)


def _extract_allowed_step_4_articles_from_chain_summary(
    chain_summary: str,
) -> list[str]:
    """
    Extract exact candidate article IDs from the Step 4 section.

    Step 10 must classify only Step 4 candidate articles. Because
    runner.py later enforces exact matching, Step 10 must be given
    exact sub-article IDs such as "13(1)" and "13(2)" rather than
    broad parent articles such as "13".

    This helper reads the Step 4 section of the synthesized chain
    summary and extracts article-like labels from that section only.
    """
    if not chain_summary:
        return []

    match = re.search(
        r"Step 4\s+—\s+Rights Identified:\n(.*?)(?:\n\n---\n\nStep 5\s+—|$)",
        chain_summary,
        flags=re.DOTALL | re.IGNORECASE,
    )

    step_4_text = match.group(1) if match else chain_summary
    articles = _extract_article_numbers(step_4_text)

    return _remove_bare_articles_when_subarticles_exist(articles)


def _format_selected_case_ids_for_step_10_prompt(
    selected_case_ids: list[str],
) -> str:
    """
    Format selected Step 7 case IDs for the Step 10 prompt.
    """
    clean_case_ids = _coerce_string_list(selected_case_ids)

    if not clean_case_ids:
        return (
            "No Step 7 case IDs were selected. Use an empty list for "
            "supporting_case_ids."
        )

    return ", ".join(clean_case_ids)


def _format_allowed_articles_for_step_10_prompt(
    allowed_articles: list[str],
) -> str:
    """
    Format allowed Step 4 article IDs for the Step 10 prompt.
    """
    clean_articles = _remove_bare_articles_when_subarticles_exist(
        _coerce_article_list(allowed_articles)
    )

    if not clean_articles:
        return (
            "No Step 4 article IDs could be extracted. Use only article "
            "IDs that are explicitly visible in the Step 4 reasoning."
        )

    return ", ".join(clean_articles)


def _get_article_base(article: str) -> str:
    """
    Return the numeric base of an article label.

    Examples:
        "13(1)" -> "13"
        "14(1)(g)" -> "14"
        "14" -> "14"
    """
    match = re.match(r"^(\d+)", str(article or "").strip())
    return match.group(1) if match else ""


def _article_has_subarticle(article: str) -> bool:
    """
    True when an article label contains a sub-article suffix.
    """
    return "(" in str(article or "")


def _allowed_specific_articles_for_base(
    article_base: str,
    allowed_articles: list[str] | None,
) -> list[str]:
    """
    Return allowed specific sub-articles for a broad article base.
    """
    if not article_base or not allowed_articles:
        return []

    return [
        article
        for article in _coerce_article_list(allowed_articles)
        if _get_article_base(article) == article_base
        and _article_has_subarticle(article)
    ]


def _extract_specific_allowed_articles_from_text(
    text: str,
    allowed_articles: list[str] | None,
    article_base: str | None = None,
) -> list[str]:
    """
    Extract exact allowed sub-article IDs that are mentioned in text.

    Used to repair model outputs such as article="13" when the
    reason says "Article 13(1)" or "Article 13(2)".
    """
    if not text or not allowed_articles:
        return []

    allowed_set = set(_coerce_article_list(allowed_articles))
    extracted = _coerce_article_list(_extract_article_numbers(text))

    results: list[str] = []

    for article in extracted:
        if article not in allowed_set:
            continue

        if article_base and _get_article_base(article) != article_base:
            continue

        if _article_has_subarticle(article):
            results.append(article)

    return _dedupe_preserve_order(results)


def _repair_article_against_allowed_articles(
    article: str,
    *,
    supporting_text: str = "",
    allowed_articles: list[str] | None = None,
) -> list[str]:
    """
    Repair a Step 10 article label against the allowed Step 4 list.

    If Gemini returns a valid exact article ID, keep it.
    If Gemini returns a broad parent article like "13" while the
    allowed list contains "13(1)" and "13(2)", try to recover the
    exact sub-articles from the reason/supporting text.

    This prevents valid final articles from being removed later by
    runner.py's exact allowed-article filtering.
    """
    normalized_article = _normalize_article_label(article)

    if not normalized_article:
        return []

    if not allowed_articles:
        return [normalized_article]

    allowed_clean = _coerce_article_list(allowed_articles)
    allowed_set = set(allowed_clean)

    if normalized_article in allowed_set:
        return [normalized_article]

    article_base = _get_article_base(normalized_article)

    # If the model returned a broad parent article but Step 4 allowed
    # specific sub-articles, recover only specific allowed articles
    # explicitly mentioned in the supporting text.
    if article_base and not _article_has_subarticle(normalized_article):
        specific_mentions = _extract_specific_allowed_articles_from_text(
            supporting_text,
            allowed_clean,
            article_base=article_base,
        )

        if specific_mentions:
            return specific_mentions

        specific_allowed = _allowed_specific_articles_for_base(
            article_base,
            allowed_clean,
        )

        if specific_allowed:
            logger.warning(
                "Step 10 returned broad article '%s' but Step 4 allowed "
                "specific sub-articles %s. Broad article was not kept.",
                normalized_article,
                specific_allowed,
            )
            return []

    return []


def _repair_article_list_against_allowed_articles(
    articles: list[str],
    *,
    supporting_text: str = "",
    allowed_articles: list[str] | None = None,
) -> list[str]:
    """
    Repair a list of Step 10 articles against the allowed Step 4 list.
    """
    repaired: list[str] = []

    for article in _coerce_article_list(articles):
        repaired.extend(
            _repair_article_against_allowed_articles(
                article,
                supporting_text=supporting_text,
                allowed_articles=allowed_articles,
            )
        )

    return _dedupe_preserve_order(repaired)



def _derive_step_10_final_supported_precedent_alignment(
    *,
    raw_alignment: Any,
    supported_articles: list[str],
    weak_articles: list[str],
    article_assessments: list[dict[str, Any]],
    selected_case_ids: list[str] | None,
) -> str:
    """
    Derive Step 10 top-level precedent_alignment from final supported
    articles only.

    This keeps the human-readable final answer consistent with the
    structured assessment before runner.py performs its second safety
    normalization pass.
    """
    clean_case_ids = _coerce_string_list(selected_case_ids or [])

    if not clean_case_ids:
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
        and assessment.get("status") == "supported"
    ]

    if not supported_assessments:
        normalized_raw = _normalize_precedent_alignment(raw_alignment)
        return normalized_raw if normalized_raw else "weak"

    directly_supported = [
        assessment
        for assessment in supported_assessments
        if _step_10_assessment_has_direct_supporting_precedent(assessment)
    ]

    if len(directly_supported) == len(supported_articles):
        return "supports"

    if directly_supported:
        return "mixed"

    normalized_raw = _normalize_precedent_alignment(raw_alignment)

    if normalized_raw == "contradicts":
        return "contradicts"

    return "weak"


def _step_10_assessment_has_direct_supporting_precedent(
    assessment: dict[str, Any],
) -> bool:
    """
    Return True when an article assessment appears directly supported
    by selected precedent.

    This avoids treating a distinguishable or negative precedent as
    direct support merely because its case ID appears in supporting_case_ids.
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


def _normalize_article_label(value: Any) -> str:
    """
    Normalize article labels returned by Step 10.

    Gemini may return labels such as:
        "Article 13(1)"
        "article 13(1)"
        "ARTICLE 13(1)"
        "Article Article 13(1)"
        "13(1)"

    The rest of the pipeline, especially runner.py, expects canonical
    article IDs such as:
        "13(1)"
        "13(2)"
        "14(1)(g)"

    Returning canonical labels prevents valid Step 10 articles from
    being filtered out during runner-level safety normalization.
    """
    text = str(value or "").strip()

    if not text:
        return ""

    # Remove repeated leading "Article" prefixes.
    # Example: "Article Article 13(1)" -> "13(1)"
    text = re.sub(
        r"^(?:article\s+)+",
        "",
        text,
        flags=re.IGNORECASE,
    ).strip()

    # Extract the first canonical article-like token.
    # Supports forms such as:
    #   11
    #   12(1)
    #   13(2)
    #   14(1)(g)
    #   14(A)
    match = re.search(
        r"(?<![A-Za-z0-9])(\d+(?:\([0-9A-Za-z]+\))*)(?![A-Za-z0-9])",
        text,
    )

    if match:
        return match.group(1).strip()

    return text


def _coerce_article_list(value: Any) -> list[str]:
    """
    Convert a value into a deduplicated list of canonical article labels.
    """
    raw_items = _coerce_string_list(value)
    normalized = [
        _normalize_article_label(item)
        for item in raw_items
    ]

    return _dedupe_preserve_order([
        article
        for article in normalized
        if article
    ])


def _normalize_step_10_structured_assessment(
    raw: dict[str, Any],
    allowed_articles: list[str] | None = None,
    selected_case_ids: list[str] | None = None,
    reasoning_context_text: str = "",
) -> dict[str, Any]:
    """
    Normalize Step 10 structured output.

    This layer enforces schema shape and allowed labels.
    It does not enforce allowed Step 4 articles or Step 7 case IDs;
    runner.py enforces those restrictions because runner.py has direct
    access to Step 4 and Step 7 structured data.
    """
    if not isinstance(raw, dict):
        return _empty_structured_assessment()

    raw_context_text = json.dumps(raw, ensure_ascii=False)
    normalization_context_text = f"{raw_context_text}\n\n{reasoning_context_text}"

    article_assessments = _normalize_step_10_article_assessments(
        raw.get("article_assessments", []),
        allowed_articles=allowed_articles,
        selected_case_ids=selected_case_ids,
        raw_context_text=normalization_context_text,
    )

    supported_articles = _repair_article_list_against_allowed_articles(
        _coerce_article_list(raw.get("final_potentially_violated_articles", [])),
        supporting_text=normalization_context_text,
        allowed_articles=allowed_articles,
    )
    weak_articles = _repair_article_list_against_allowed_articles(
        _coerce_article_list(raw.get("final_weak_or_uncertain_articles", [])),
        supporting_text=normalization_context_text,
        allowed_articles=allowed_articles,
    )
    rejected_articles = _repair_article_list_against_allowed_articles(
        _coerce_article_list(raw.get("final_rejected_articles", [])),
        supporting_text=normalization_context_text,
        allowed_articles=allowed_articles,
    )

    # Make top-level article buckets consistent with article_assessments.
    for assessment in article_assessments:
        article = _normalize_article_label(assessment.get("article", ""))
        status = assessment.get("status", "")

        if status == "supported":
            supported_articles.append(article)
        elif status == "weak_or_uncertain":
            weak_articles.append(article)
        elif status == "rejected":
            rejected_articles.append(article)

    supported_articles = _dedupe_preserve_order(supported_articles)

    # Prevent article duplication across buckets.
    weak_articles = [
        article for article in _dedupe_preserve_order(weak_articles)
        if article not in supported_articles
    ]
    rejected_articles = [
        article for article in _dedupe_preserve_order(rejected_articles)
        if article not in supported_articles and article not in weak_articles
    ]

    (
        article_assessments,
        supported_articles,
        weak_articles,
        rejected_articles,
    ) = _complete_missing_allowed_article_assessments(
        article_assessments=article_assessments,
        supported_articles=supported_articles,
        weak_articles=weak_articles,
        rejected_articles=rejected_articles,
        allowed_articles=allowed_articles,
        context_text=normalization_context_text,
        selected_case_ids=selected_case_ids,
    )

    return {
        "final_potentially_violated_articles": supported_articles,
        "final_weak_or_uncertain_articles": weak_articles,
        "final_rejected_articles": rejected_articles,
        "overall_assessment": _normalize_overall_assessment(
            raw.get("overall_assessment", "")
        ),
        "precedent_alignment": _derive_step_10_final_supported_precedent_alignment(
            raw_alignment=raw.get("precedent_alignment", ""),
            supported_articles=supported_articles,
            weak_articles=weak_articles,
            article_assessments=article_assessments,
            selected_case_ids=selected_case_ids,
        ),
        "article_assessments": article_assessments,
        "key_strengths": _coerce_string_list(raw.get("key_strengths", [])),
        "key_weaknesses": _coerce_string_list(raw.get("key_weaknesses", [])),
        "faithfulness_notes": _coerce_string_list(
            raw.get("faithfulness_notes", [])
        ),
    }


def _normalize_step_10_article_assessments(
    raw_assessments: Any,
    allowed_articles: list[str] | None = None,
    selected_case_ids: list[str] | None = None,
    raw_context_text: str = "",
) -> list[dict[str, Any]]:
    """
    Normalize Step 10 per-article assessment objects.
    """
    if not isinstance(raw_assessments, list):
        return []

    allowed_statuses = {"supported", "weak_or_uncertain", "rejected"}
    allowed_confidence = {"high", "medium", "low"}
    allowed_steps = {
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

    normalized: list[dict[str, Any]] = []
    seen_articles: set[str] = set()

    for item in raw_assessments:
        if not isinstance(item, dict):
            continue

        article = _normalize_article_label(item.get("article", ""))
        status = str(item.get("status", "")).strip().lower()
        reason = str(item.get("reason", "")).strip()

        if status not in allowed_statuses:
            continue

        repaired_articles = _repair_article_against_allowed_articles(
            article,
            supporting_text=f"{reason}\n{raw_context_text}",
            allowed_articles=allowed_articles,
        )

        if not repaired_articles:
            continue

        confidence = str(item.get("confidence", "")).strip().lower()
        if confidence not in allowed_confidence:
            confidence = ""

        supporting_steps = [
            step for step in _coerce_string_list(item.get("supporting_steps", []))
            if step in allowed_steps
        ]

        supporting_case_ids = _normalize_supporting_case_ids(
            item.get("supporting_case_ids", []),
            selected_case_ids=selected_case_ids,
            status=status,
            reason=reason,
            supporting_steps=supporting_steps,
        )

        for repaired_article in repaired_articles:
            if repaired_article in seen_articles:
                continue

            normalized.append({
                "article": repaired_article,
                "status": status,
                "reason": reason,
                "supporting_steps": _dedupe_preserve_order(supporting_steps),
                "supporting_case_ids": supporting_case_ids,
                "confidence": confidence,
            })
            seen_articles.add(repaired_article)

    return normalized


def _normalize_supporting_case_ids(
    raw_case_ids: Any,
    *,
    selected_case_ids: list[str] | None = None,
    status: str = "",
    reason: str = "",
    supporting_steps: list[str] | None = None,
) -> list[str]:
    """
    Normalize and safely fill supporting_case_ids.

    If Step 10 relies on precedent but omits the selected case ID,
    and Step 7 selected exactly one case, fill that ID. This keeps
    the structured output aligned with the human-readable reasoning.
    """
    selected_clean = _coerce_string_list(selected_case_ids or [])
    selected_set = set(selected_clean)

    case_ids = _coerce_string_list(raw_case_ids)

    if selected_set:
        case_ids = [
            case_id
            for case_id in case_ids
            if case_id in selected_set
        ]

    uses_precedent = _assessment_uses_precedent(
        reason=reason,
        supporting_steps=supporting_steps or [],
    )

    if (
        not case_ids
        and status == "supported"
        and uses_precedent
        and len(selected_clean) == 1
    ):
        case_ids = selected_clean

    return _dedupe_preserve_order(case_ids)


def _assessment_uses_precedent(
    *,
    reason: str,
    supporting_steps: list[str],
) -> bool:
    """
    Detect whether an article assessment relies on precedent.
    """
    lower_reason = str(reason or "").lower()

    if "step_8" in supporting_steps or "step_9" in supporting_steps:
        return True

    precedent_signals = [
        "precedent",
        "case",
        "case law",
        "dodampe",
        "atapattu",
        "similar case",
        "court found",
        "found a violation",
    ]

    return any(signal in lower_reason for signal in precedent_signals)


def _complete_missing_allowed_article_assessments(
    *,
    article_assessments: list[dict[str, Any]],
    supported_articles: list[str],
    weak_articles: list[str],
    rejected_articles: list[str],
    allowed_articles: list[str] | None,
    context_text: str,
    selected_case_ids: list[str] | None = None,
) -> tuple[list[dict[str, Any]], list[str], list[str], list[str]]:
    """
    Ensure every Step 4 candidate article is explicitly classified.

    Step 10 sometimes omits weak or rejected candidates. That is bad
    for evaluation because omitted candidates disappear from all final
    buckets. This deterministic pass adds any missing Step 4 candidate
    as rejected if the chain context clearly rejects it, otherwise as
    weak_or_uncertain.
    """
    allowed_clean = _remove_bare_articles_when_subarticles_exist(
        _coerce_article_list(allowed_articles or [])
    )

    if not allowed_clean:
        return (
            article_assessments,
            supported_articles,
            weak_articles,
            rejected_articles,
        )

    existing_articles = {
        _normalize_article_label(item.get("article", ""))
        for item in article_assessments
        if isinstance(item, dict)
    }

    completed_assessments = list(article_assessments)
    supported = _dedupe_preserve_order(supported_articles)
    weak = _dedupe_preserve_order([
        article for article in weak_articles
        if article not in supported
    ])
    rejected = _dedupe_preserve_order([
        article for article in rejected_articles
        if article not in supported and article not in weak
    ])

    for article in allowed_clean:
        if article in existing_articles:
            continue

        status = _classify_missing_allowed_article(article, context_text)
        reason = _build_missing_article_reason(article, status, context_text)

        assessment = {
            "article": article,
            "status": status,
            "reason": reason,
            "supporting_steps": _infer_missing_article_supporting_steps(
                article,
                context_text,
            ),
            "supporting_case_ids": [],
            "confidence": "medium" if status == "rejected" else "low",
        }

        completed_assessments.append(assessment)
        existing_articles.add(article)

        if status == "supported":
            supported.append(article)
        elif status == "rejected":
            rejected.append(article)
        else:
            weak.append(article)

    supported = _dedupe_preserve_order(supported)
    weak = [
        article for article in _dedupe_preserve_order(weak)
        if article not in supported
    ]
    rejected = [
        article for article in _dedupe_preserve_order(rejected)
        if article not in supported and article not in weak
    ]

    return completed_assessments, supported, weak, rejected


def _classify_missing_allowed_article(article: str, context_text: str) -> str:
    """
    Classify omitted Step 4 candidates without overclaiming.

    Missing articles are never promoted to supported. They are rejected
    only when the reasoning context contains clear negative language;
    otherwise they are retained as weak_or_uncertain.
    """
    window = _article_context_window(article, context_text)
    lower_window = window.lower()

    negative_signals = [
        "not supported",
        "does not support",
        "doesn't support",
        "no support",
        "lacks support",
        "lack of support",
        "not directly supported",
        "does not directly support",
        "not applicable",
        "does not apply",
        "unsupported",
        "rejected",
        "should not be presented",
        "should not be classified",
        "not aligned",
        "does not align",
        "not relevant",
        "insufficient support",
    ]

    if any(signal in lower_window for signal in negative_signals):
        return "rejected"

    return "weak_or_uncertain"


def _article_context_window(article: str, context_text: str, radius: int = 500) -> str:
    """
    Return text near the first mention of an article.
    """
    if not article or not context_text:
        return ""

    escaped_article = re.escape(article)
    match = re.search(escaped_article, context_text, flags=re.IGNORECASE)

    if not match:
        # Also search by broad base for contexts such as "Article 13".
        base = _get_article_base(article)
        if base:
            match = re.search(
                rf"Article\s+{re.escape(base)}\b|\b{re.escape(base)}\b",
                context_text,
                flags=re.IGNORECASE,
            )

    if not match:
        return context_text[: radius * 2]

    start = max(0, match.start() - radius)
    end = min(len(context_text), match.end() + radius)
    return context_text[start:end]


def _build_missing_article_reason(
    article: str,
    status: str,
    context_text: str,
) -> str:
    """
    Build a conservative reason for a deterministically completed article.
    """
    if status == "rejected":
        return (
            f"Article {article} was identified as a Step 4 candidate, but "
            f"the later precedent/cross-validation reasoning did not support "
            f"presenting it as a final potentially violated article."
        )

    return (
        f"Article {article} was identified as a Step 4 candidate, but Step 10 "
        f"did not provide enough structured support to classify it as supported "
        f"or rejected. It is therefore retained as weak or uncertain rather "
        f"than silently omitted."
    )


def _infer_missing_article_supporting_steps(
    article: str,
    context_text: str,
) -> list[str]:
    """
    Infer supporting step references for completed weak/rejected candidates.
    """
    window = _article_context_window(article, context_text)
    steps: list[str] = ["step_4"]

    if "step 9" in window.lower() or "step_9" in window.lower():
        steps.append("step_9")

    if "step 8" in window.lower() or "step_8" in window.lower():
        steps.append("step_8")

    return _dedupe_preserve_order(steps)


def _normalize_overall_assessment(value: Any) -> str:
    """
    Normalize Step 10 overall assessment label.
    """
    allowed = {
        "likely_viable",
        "weak_or_uncertain",
        "not_viable",
        "time_barred",
        "not_state_actor",
    }
    label = str(value or "").strip().lower()
    return label if label in allowed else ""


def _normalize_precedent_alignment(value: Any) -> str:
    """
    Normalize Step 10 precedent alignment label.
    """
    allowed = {
        "supports",
        "mixed",
        "weak",
        "contradicts",
        "no_cases",
        "not_assessed",
    }
    label = str(value or "").strip().lower()
    return label if label in allowed else ""


def _dedupe_preserve_order(items: list[str]) -> list[str]:
    """
    Deduplicate a list of strings while preserving original order.

    Used by Step 10 structured-output normalization.
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
    Convert a value to a clean list of strings.
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


def _render_final_answer_from_structured_assessment(
    assessment: dict[str, Any],
) -> str:
    """
    Render the final user-facing answer from the structured Step 10 JSON.

    The JSON is the source of truth. The final answer is a deterministic
    presentation layer built from that JSON.
    """
    if not isinstance(assessment, dict):
        return ""

    supported = _coerce_article_list(
        assessment.get("final_potentially_violated_articles", [])
    )
    weak = _coerce_article_list(
        assessment.get("final_weak_or_uncertain_articles", [])
    )
    rejected = _coerce_article_list(
        assessment.get("final_rejected_articles", [])
    )
    article_assessments = assessment.get("article_assessments", [])
    if not isinstance(article_assessments, list):
        article_assessments = []

    key_strengths = _coerce_string_list(assessment.get("key_strengths", []))
    key_weaknesses = _coerce_string_list(assessment.get("key_weaknesses", []))
    faithfulness_notes = _coerce_string_list(
        assessment.get("faithfulness_notes", [])
    )

    overall_assessment = str(assessment.get("overall_assessment", "")).strip()
    precedent_alignment = str(assessment.get("precedent_alignment", "")).strip()

    lines: list[str] = []

    lines.append("SECTION 1 — RIGHTS ASSESSMENT:")
    if supported:
        lines.append(
            "Based on the structured reasoning chain, the following article(s) "
            "appear to remain supported as potentially violated:"
        )
        for article in supported:
            reason = _find_article_reason(article, article_assessments)
            if reason:
                lines.append(f"- Article {article}: {reason}")
            else:
                lines.append(f"- Article {article}: Appears potentially supported.")
    else:
        lines.append(
            "No article was classified as clearly supported after the full "
            "reasoning chain."
        )

    if weak:
        lines.append("")
        lines.append("Weak or uncertain article(s):")
        for article in weak:
            reason = _find_article_reason(article, article_assessments)
            if reason:
                lines.append(f"- Article {article}: {reason}")
            else:
                lines.append(
                    f"- Article {article}: Potential relevance is uncertain."
                )

    if rejected:
        lines.append("")
        lines.append("Rejected or unsupported article(s):")
        for article in rejected:
            reason = _find_article_reason(article, article_assessments)
            if reason:
                lines.append(f"- Article {article}: {reason}")
            else:
                lines.append(
                    f"- Article {article}: Not sufficiently supported by the "
                    f"available materials."
                )

    lines.append("")
    lines.append("SECTION 2 — PRECEDENT:")
    if precedent_alignment:
        lines.append(
            f"The precedent alignment was classified as: {precedent_alignment}."
        )
    else:
        lines.append(
            "The precedent alignment could not be classified from the "
            "structured output."
        )

    lines.append("")
    lines.append("SECTION 3 — STRENGTHS OF THE CASE:")
    if key_strengths:
        for strength in key_strengths:
            lines.append(f"- {strength}")
    else:
        lines.append("- No specific strengths were identified in the structured output.")

    lines.append("")
    lines.append("SECTION 4 — WEAKNESSES AND UNCERTAINTIES:")
    if key_weaknesses:
        for weakness in key_weaknesses:
            lines.append(f"- {weakness}")
    else:
        lines.append(
            "- No specific weaknesses were identified in the structured output."
        )

    lines.append("")
    lines.append("SECTION 5 — OVERALL ASSESSMENT:")
    if overall_assessment:
        lines.append(
            f"Overall assessment: {overall_assessment}. This is a preliminary "
            f"AI-generated legal triage classification based only on the "
            f"provided facts and retrieved materials."
        )
    else:
        lines.append(
            "Overall assessment could not be classified from the structured output."
        )

    if faithfulness_notes:
        lines.append("")
        lines.append("FAITHFULNESS NOTES:")
        for note in faithfulness_notes:
            lines.append(f"- {note}")

    return "\n".join(lines).strip()


def _find_article_reason(article: str, article_assessments: list[Any]) -> str:
    """
    Find the reason attached to a specific article assessment.

    Article comparison is canonicalized so that "Article 13(1)"
    and "13(1)" are treated as the same article.
    """
    target_article = _normalize_article_label(article)

    for assessment in article_assessments:
        if not isinstance(assessment, dict):
            continue

        assessment_article = _normalize_article_label(
            assessment.get("article", "")
        )

        if assessment_article == target_article:
            return str(assessment.get("reason", "")).strip()

    return ""


def _build_chain_summary(all_answers: dict) -> str:
    """
    Format all prior step answers into a readable summary
    for the Step 10 synthesis prompt.
    Answers are truncated to keep the context manageable.
    """
    labels = {
        "step_1": "Step 1 — Timeliness",
        "step_2": "Step 2 — State Actor",
        "step_3": "Step 3 — Facts",
        "step_4": "Step 4 — Rights Identified",
        "step_5": "Step 5 — Nature of Violation",
        "step_6": "Step 6 — Intent and Harm",
        "step_7": "Step 7 — Similar Cases",
        "step_8": "Step 8 — Precedent Analysis",
        "step_9": "Step 9 — Cross-Validation",
    }

    lines = []
    for key, label in labels.items():
        answer = all_answers.get(key, "Not completed.")
        if isinstance(answer, dict):
            answer = answer.get("answer", str(answer))
        if isinstance(answer, str) and len(answer) > 1200:
            answer = answer[:1200] + "... [truncated for synthesis prompt]"
        lines.append(f"{label}:\n{answer}")

    return "\n\n---\n\n".join(lines)
