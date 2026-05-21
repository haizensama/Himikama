"""
himikama/backend/chain/intake.py
═══════════════════════════════════════════════════════════════
Phase 3 — Step 0: Intake Extraction

Responsibility:
    Run the single LLM call that extracts structured,
    objective facts from the user's free-text narrative.

    This is the ONLY step that processes raw user input.
    Everything downstream works from the structured intake
    object this module produces.

    NO legal interpretation happens here.
    NO article prediction.
    NO assumptions beyond what the user stated.

Design:
    The LLM is given a strict prompt that enforces factual
    extraction only. The output is validated and cleaned
    before being returned.

    A plain-English confirmation text is generated alongside
    the intake object so Flutter can display it to the user
    for review before the chain begins.

Input:
    user_narrative: str — the user's raw free-text description

Output:
    Tuple of:
        intake_object:     dict — structured extracted fields
        confirmation_text: str  — plain English for Flutter display

Anti-hallucination measures applied:
    - Temperature = 0 (deterministic output)
    - Closed-world instruction (extract only what was stated)
    - Explicit null instruction (missing fields → null)
    - Output format locked to JSON only
    - Input echo before extraction
    - Post-LLM validation and cleaning

Usage:
    from chain.intake import extract_intake

    intake_obj, confirm_text = await extract_intake(
        user_narrative="Police arrested me without a warrant..."
    )
═══════════════════════════════════════════════════════════════
"""

import json
import logging
import re

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────────────────────

# Fields the intake object must always contain.
# Value is None if the user did not mention it.
INTAKE_FIELDS = [
    "incident_date",
    "incident_location",
    "actor_name",
    "actor_role",
    "what_happened",
    "harm_suffered",
    "user_narrative",
]

# Fields that are required for the chain to proceed.
# If these are None after extraction, the user must clarify.
REQUIRED_FIELDS = [
    "incident_date",
    "what_happened",
]


# ─────────────────────────────────────────────────────────────
# PROMPT CONSTRUCTION
# ─────────────────────────────────────────────────────────────

def _build_intake_prompt(user_narrative: str) -> str:
    """
    Build the Step 0 intake extraction prompt.

    Follows all anti-hallucination principles:
        - Closed-world: extract only what was stated
        - Explicit null instruction: missing = null
        - Output format locked: JSON only, no preamble
        - Input echo: LLM restates before extracting
        - Role boundary: intake assistant only

    Args:
        user_narrative: Raw text submitted by the user.

    Returns:
        Complete prompt string for the LLM.
    """
    return f"""You are an intake assistant for a legal application in Sri Lanka.
Your only job is to extract factual information from the user's description of their situation.

RULES YOU MUST FOLLOW WITHOUT EXCEPTION:
1. Extract ONLY information the user explicitly stated.
2. Do NOT interpret anything legally.
3. Do NOT infer or predict which laws or articles apply.
4. Do NOT make assumptions beyond what was stated.
5. If a field is not mentioned, return null for that field.
6. For incident_date: extract the date if stated. If the user says "last month" or similar, estimate a date. If completely unclear, return null.
7. Return ONLY a valid JSON object. No explanation, no preamble, no markdown, no code blocks.
8. user_narrative must be an exact verbatim copy of the input text.

FIELDS TO EXTRACT:
- incident_date: The date the incident occurred (YYYY-MM-DD format if possible, else descriptive string)
- incident_location: Where it happened
- actor_name: Name of the person or institution that acted (if stated)
- actor_role: Role or type of the actor (e.g. "police officer", "army", "government ministry")
- what_happened: Clear concise description of the act or omission complained of
- harm_suffered: What harm, loss, or damage the user suffered as a result
- user_narrative: Exact verbatim copy of the user's input

USER INPUT:
{user_narrative}

Return this exact JSON structure with values filled in:
{{
  "incident_date": null,
  "incident_location": null,
  "actor_name": null,
  "actor_role": null,
  "what_happened": null,
  "harm_suffered": null,
  "user_narrative": null
}}"""


# ─────────────────────────────────────────────────────────────
# LLM CALL
# ─────────────────────────────────────────────────────────────

async def _call_gemini(prompt: str) -> str:
    """
    Call the Gemini LLM with the intake prompt.

    Uses temperature=0 for deterministic, consistent output.
    Gemini Flash is used for this step — it is fast and
    sufficient for structured extraction tasks.

    Args:
        prompt: The complete intake extraction prompt.

    Returns:
        Raw text response from the LLM.

    Raises:
        RuntimeError: If the API call fails.
    """
    try:
        import google.generativeai as genai
        from api.config import config

        genai.configure(api_key=config.gemini_api_key)

        model = genai.GenerativeModel("gemini-2.5-flash")

        response = model.generate_content(
            prompt,
            generation_config=genai.GenerationConfig(
                temperature=0,          # deterministic output
                max_output_tokens=1024,  # intake is small
            ),
        )

        return response.text

    except Exception as e:
        raise RuntimeError(f"Gemini API call failed: {e}")


# ─────────────────────────────────────────────────────────────
# RESPONSE PARSING
# ─────────────────────────────────────────────────────────────

def _parse_llm_response(raw_response: str) -> dict:
    """
    Parse and validate the LLM JSON response.

    Handles common LLM output issues:
        - Markdown code blocks (```json ... ```)
        - Leading/trailing whitespace
        - Smart quotes from some model outputs

    Args:
        raw_response: Raw text from the LLM.

    Returns:
        Parsed dict with all intake fields.

    Raises:
        ValueError: If JSON cannot be parsed after cleaning.
    """
    text = raw_response.strip()

    # Remove markdown code blocks if present
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"\s*```$", "", text, flags=re.MULTILINE)
    text = text.strip()

    # Replace smart quotes with straight quotes
    text = text.replace("\u201c", '"').replace("\u201d", '"')
    text = text.replace("\u2018", "'").replace("\u2019", "'")

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        # Check if response was truncated (no closing brace)
        if text.count("{") > text.count("}"):
            raise ValueError(
                f"LLM response was truncated before JSON completed. "
                f"Increase max_output_tokens. "
                f"Raw response: {raw_response[:300]}"
            )
        # Try to extract JSON object if wrapped in other text
        json_match = re.search(r"\{.*\}", text, re.DOTALL)
        if json_match:
            try:
                parsed = json.loads(json_match.group(), strict=False)
            except json.JSONDecodeError as e:
                raise ValueError(
                    f"Could not parse LLM response as JSON: {e}\n"
                    f"Raw response: {raw_response[:300]}"
                )
        else:
            raise ValueError(
                f"No JSON object found in LLM response.\n"
                f"Raw response: {raw_response[:300]}"
            )
    return parsed


# ─────────────────────────────────────────────────────────────
# POST-PARSE VALIDATION AND CLEANING
# ─────────────────────────────────────────────────────────────

def _validate_and_clean(
    parsed: dict,
    user_narrative: str,
) -> dict:
    """
    Validate and clean the parsed intake object.

    Ensures:
        - All required fields are present as keys
        - user_narrative is always the original verbatim text
          (LLM occasionally truncates or paraphrases it)
        - Empty strings are normalised to None
        - No unexpected fields are added

    Args:
        parsed:         Parsed dict from LLM response.
        user_narrative: Original user input (source of truth).

    Returns:
        Cleaned intake object dict.
    """
    intake = {}

    for field in INTAKE_FIELDS:
        value = parsed.get(field)

        # Normalise empty strings to None
        if isinstance(value, str) and not value.strip():
            value = None

        intake[field] = value

    # Always use the original narrative verbatim
    # The LLM should copy it but may not do so perfectly
    intake["user_narrative"] = user_narrative

    return intake


def _check_missing_required(intake: dict) -> list[str]:
    """
    Check which required fields are None after extraction.

    Args:
        intake: Cleaned intake object.

    Returns:
        List of field names that are None but required.
    """
    return [
        field for field in REQUIRED_FIELDS
        if intake.get(field) is None
    ]


# ─────────────────────────────────────────────────────────────
# CONFIRMATION TEXT GENERATION
# ─────────────────────────────────────────────────────────────

def _build_confirmation_text(intake: dict) -> str:
    """
    Build a plain-English confirmation text for Flutter to
    display to the user before the chain begins.

    This is the "Here's what we understood — is this correct?"
    moment in the UX. The user must confirm before proceeding.

    Args:
        intake: Cleaned intake object.

    Returns:
        Human-readable string summarising what was extracted.
    """
    lines = ["Here is what we understood from your situation:\n"]

    if intake.get("what_happened"):
        lines.append(f"• What happened: {intake['what_happened']}")

    if intake.get("actor_role"):
        name = intake.get("actor_name", "")
        role = intake["actor_role"]
        actor_str = f"{name} ({role})" if name else role
        lines.append(f"• Who was involved: {actor_str}")

    if intake.get("harm_suffered"):
        lines.append(f"• Harm suffered: {intake['harm_suffered']}")

    if intake.get("incident_date"):
        lines.append(f"• When this happened: {intake['incident_date']}")

    if intake.get("incident_location"):
        lines.append(f"• Where: {intake['incident_location']}")

    if not intake.get("incident_date"):
        lines.append(
            "\n⚠ We could not determine when this happened. "
            "Please provide the date of the incident before proceeding."
        )

    lines.append(
        "\nIs this correct? Please confirm or correct any "
        "details before we proceed with the legal analysis."
    )

    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────
# PUBLIC FUNCTION
# ─────────────────────────────────────────────────────────────

async def extract_intake(
    user_narrative: str,
) -> tuple[dict, str]:
    """
    Step 0 — Full intake extraction pipeline.

    Runs the complete intake extraction flow:
        1. Build the prompt
        2. Call Gemini LLM (temperature=0)
        3. Parse JSON response
        4. Validate and clean
        5. Build confirmation text

    The returned intake_object is returned to Flutter for
    user review. Flutter then calls POST /confirm with the
    (potentially corrected) intake object.

    Args:
        user_narrative: Raw free-text from the user.

    Returns:
        Tuple of:
            intake_object:     Structured extracted fields dict.
                               Fields not mentioned = None.
            confirmation_text: Plain English for Flutter display.

    Raises:
        RuntimeError: If LLM call fails.
        ValueError:   If LLM response cannot be parsed.
    """
    logger.info("Step 0 — Running intake extraction...")

    # Build prompt
    prompt = _build_intake_prompt(user_narrative)

    # Call LLM
    raw_response = await _call_gemini(prompt)
    logger.debug(f"Raw LLM response: {raw_response[:200]}")

    # Parse response
    parsed = _parse_llm_response(raw_response)

    # Validate and clean
    intake = _validate_and_clean(parsed, user_narrative)

    # Check for missing required fields
    missing = _check_missing_required(intake)
    if missing:
        logger.warning(
            f"Required intake fields are None: {missing}. "
            f"Flutter should prompt the user to clarify."
        )

    # Build confirmation text
    confirmation_text = _build_confirmation_text(intake)

    logger.info(
        f"Intake extraction complete. "
        f"Missing required fields: {missing if missing else 'none'}"
    )

    return intake, confirmation_text
