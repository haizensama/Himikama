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

import logging
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
        response = await chain.ainvoke(prompt_inputs)
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
        response = await (prompt | llm).ainvoke(prepared)
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
    pattern = r"\b(\d+(?:\(\d+\))?(?:\([a-zA-Z]\))?)\b"
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
             LLM identifies 2-3 most factually similar cases.

    Stage B: Deterministic direct fetch by case_id.
             Intentionally outside LangChain — this is a
             targeted lookup, not semantic reasoning.

    The Stage B cases are stored for Steps 8 and 9.
    """
    question = """Based on the facts of this situation, which 2 or 3 of the cases provided are most factually similar?

For each selected case:
1. State the case name and case number exactly as shown.
2. Explain specifically which facts make it similar to the current situation.
3. Note any important factual differences.

Select cases based on FACTUAL similarity — similar acts, similar actors, similar circumstances.
Do not select cases based only on the articles cited.
State the case_id number for each selected case in brackets at the end of each selection e.g. [case_id: 42]"""

    # ── Stage A: App-controlled retrieval (before LangChain call) ──
    # Guard runs before LangChain to avoid wasting an LLM call
    stage_a_cases = retrieve_cases_stage_a(
        case_collection,
        intake,
        filter_articles=articles_from_step_4,
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
            },
        }

    # ── LangChain: prompt assembly + Gemini call ──
    cases_formatted = format_cases_for_prompt(stage_a_cases)

    prompt = _base_prompt_template()
    llm    = _get_llm(max_tokens=1500)

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
        response = await (prompt | llm).ainvoke(prompt_inputs)
        answer   = _extract_llm_text(response)
    except Exception as e:
        raise RuntimeError(f"Controlled LangChain Step 7 failed: {e}")

    # ── App code: extract case_ids from LLM answer ──
    case_ids = _extract_case_ids_from_answer(answer, stage_a_cases)
    logger.info(f"Step 7 identified case_ids: {case_ids}")

    # ── Stage B: Deterministic targeted fetch — outside LangChain ──
    stage_b_cases = retrieve_cases_stage_b(case_collection, case_ids)

    return {
        "step":        "step_7",
        "answer":      answer,
        "explanation": _explain_step_7(case_ids, stage_a_cases),
        "passed":      True,
        "data": {
            "case_ids":      case_ids,
            "cases":         stage_a_cases,
            "stage_b_cases": stage_b_cases,
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
    Fallback:  Use top 2 Stage A cases by similarity score.
    """
    case_ids: list[str] = []

    # Method 1 — explicit tags
    id_matches = re.findall(r"\[case_id:\s*(\d+)\]", answer, re.IGNORECASE)
    for match in id_matches[:3]:
        if match not in case_ids:
            case_ids.append(match)

    # Method 2 — name matching
    if len(case_ids) < 2:
        for case in stage_a_cases:
            name = case.get("case_name", "")
            if name and name[:20].lower() in answer.lower():
                cid = str(case.get("case_id", ""))
                if cid and cid not in case_ids:
                    case_ids.append(cid)
            if len(case_ids) >= 3:
                break

    # Fallback — top Stage A results
    if not case_ids:
        logger.warning(
            "Step 7: Could not extract case_ids from answer. "
            "Falling back to top 2 Stage A cases."
        )
        for case in stage_a_cases[:2]:
            cid = str(case.get("case_id", ""))
            if cid:
                case_ids.append(cid)

    return case_ids[:3]


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
        response = await (prompt | llm).ainvoke(prompt_inputs)
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
    Step 9 — Cross-validation (hallucination catch).

    Independently infers which articles apply from case patterns,
    then compares against Step 4. Flags inconsistencies.
    No new retrieval — uses Step 7 Stage B cases.
    """
    if not stage_b_cases:
        return {
            "step":        "step_9",
            "answer":      (
                "Cross-validation could not be performed as no similar "
                "cases were available for comparison."
            ),
            "explanation": _explain_step_9(True, False),
            "passed":      True,
            "data": {
                "consistent":            True,
                "inconsistencies_found": False,
            },
        }

    cases_formatted  = format_cases_for_prompt(stage_b_cases)
    step_4_articles  = (
        ", ".join(articles_from_step_4)
        if articles_from_step_4 else "None identified"
    )

    # The Step 4 finding is embedded in the question itself so
    # the LLM can compare independently — not in the system prompt
    question = f"""PRIOR STEP 4 FINDING:
The earlier rights identification step identified these articles as potentially applicable: {step_4_articles}

YOUR TASK — Independent Cross-Validation:
Based ONLY on the patterns in the case law provided below, and WITHOUT being influenced by the Step 4 finding above:

A) Which constitutional articles would you independently expect to be relevant to facts like those described, based purely on what you observe in these cases?

B) Compare your independent finding with the Step 4 finding:
   - Do they align?
   - Are there articles in Step 4 that the cases do not support?
   - Are there articles the cases suggest that Step 4 missed?

C) If there are inconsistencies, explain them. If they align, confirm this.

Be objective. If the case law strongly supports Step 4, say so. If it contradicts Step 4, flag this clearly."""

    prompt = _base_prompt_template()
    llm    = _get_llm(max_tokens=1500)

    prompt_inputs = _make_prompt_inputs(
        step_number=9,
        step_question=question,
        intake=intake,
        intake_fields={"What happened": intake.get("what_happened")},
        retrieved_content=cases_formatted,
        retrieved_label="SIMILAR CASES FOR CROSS-VALIDATION",
    )

    try:
        response = await (prompt | llm).ainvoke(prompt_inputs)
        answer   = _extract_llm_text(response)
    except Exception as e:
        raise RuntimeError(f"Controlled LangChain Step 9 failed: {e}")

    answer_lower          = answer.lower()
    inconsistency_signals = [
        "inconsisten", "contradict", "does not support",
        "not supported", "conflict", "discrepan", "flag", "mismatch",
    ]
    inconsistency_found = any(s in answer_lower for s in inconsistency_signals)

    return {
        "step":        "step_9",
        "answer":      answer,
        "explanation": _explain_step_9(not inconsistency_found, inconsistency_found),
        "passed":      True,
        "data": {
            "consistent":            not inconsistency_found,
            "inconsistencies_found": inconsistency_found,
            "stage_b_cases":         stage_b_cases,
        },
    }


# ─────────────────────────────────────────────────────────────
# STEP 10 — FINAL SYNTHESIS [CONTROLLED LANGCHAIN LLM]
# ─────────────────────────────────────────────────────────────

async def run_step_10(intake: dict, all_answers: dict) -> dict:
    """
    Step 10 — Final synthesis.

    Receives all prior step answers and synthesizes a
    structured legal assessment. Uses a custom prompt
    template (different variable structure from other steps).

    This is the most token-heavy step. gemini-2.5-pro can be
    configured here if higher quality is needed — change
    GEMINI_MODEL in .env.
    """
    chain_summary = _build_chain_summary(all_answers)

    question = """You are now synthesizing the findings from a complete structured legal reasoning chain into a final assessment.

Before writing your assessment, reason through each finding in order:
1. Was the timeliness requirement met?
2. Was a state actor confirmed?
3. What specifically happened?
4. Which rights appear potentially violated?
5. Was the violation direct or through state power?
6. Was there intent and actual harm?
7-9. What do similar cases and cross-validation show?

Then produce a structured final assessment covering:

SECTION 1 — RIGHTS ASSESSMENT:
Which fundamental rights appear to have been potentially violated, and why. Reference specific articles.

SECTION 2 — PRECEDENT:
How similar cases were decided and what this suggests about the current situation.

SECTION 3 — STRENGTHS OF THE CASE:
What factual and legal elements support a viable petition.

SECTION 4 — WEAKNESSES AND UNCERTAINTIES:
What elements are weak, unclear, or may undermine the petition.

SECTION 5 — OVERALL ASSESSMENT:
Based on all of the above, does this situation appear to present a viable basis to file a Fundamental Rights petition in the Supreme Court of Sri Lanka?

Use appropriate hedging language throughout. Do not provide legal advice. Do not tell the user what to do. Present findings objectively."""

    # Step 10 uses a custom prompt template with different variables
    synthesis_prompt = ChatPromptTemplate.from_messages([
        ("system", SYSTEM_PROMPT),
        ("human", """USER NARRATIVE (original):
{user_narrative}

COMPLETE REASONING CHAIN:
{chain_summary}

FINAL SYNTHESIS TASK:
{step_question}"""),
    ])

    llm   = _get_llm(max_tokens=3000)
    chain = synthesis_prompt | llm

    try:
        response = await chain.ainvoke({
            "user_narrative": intake.get("user_narrative", ""),
            "chain_summary":  chain_summary,
            "step_question":  question,
        })
        answer = _extract_llm_text(response)
    except Exception as e:
        raise RuntimeError(f"Controlled LangChain Step 10 failed: {e}")

    return {
        "step":        "step_10",
        "answer":      answer,
        "explanation": _explain_step_10(answer),
        "passed":      True,
        "data":        {},
    }


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
