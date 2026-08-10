"""Strict request schemas for the Himikama API."""

from __future__ import annotations

import unicodedata
from datetime import date, datetime
from typing import Any, Literal
from uuid import UUID
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, field_validator


_BIDI_CONTROL_CHARACTERS = {
    "\u202a", "\u202b", "\u202c", "\u202d", "\u202e",
    "\u2066", "\u2067", "\u2068", "\u2069",
}

_SRI_LANKA_TIMEZONE = ZoneInfo("Asia/Colombo")


def _today_in_sri_lanka() -> date:
    """Use the user's legal date boundary even on a UTC deployment."""
    return datetime.now(_SRI_LANKA_TIMEZONE).date()


def _normalize_user_text(value: str | None) -> str | None:
    if value is None:
        return None

    normalized = unicodedata.normalize("NFC", value).strip()
    for character in normalized:
        codepoint = ord(character)
        if character in _BIDI_CONTROL_CHARACTERS:
            raise ValueError("Text contains unsupported directional controls")
        if (codepoint < 32 and character not in {"\n", "\r", "\t"}) or codepoint == 127:
            raise ValueError("Text contains unsupported control characters")

    return normalized


class StrictModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        validate_assignment=True,
    )


class ConfirmedIntake(StrictModel):
    incident_date: date = Field(
        ...,
        description="Date of the incident in YYYY-MM-DD format.",
    )
    incident_location: str | None = Field(default=None, max_length=200)
    actor_name: str | None = Field(default=None, max_length=200)
    actor_role: str = Field(..., min_length=2, max_length=120)
    what_happened: str = Field(..., min_length=10, max_length=2_000)
    harm_suffered: str | None = Field(default=None, max_length=1_000)
    user_narrative: str = Field(..., min_length=10, max_length=4_000)

    @field_validator(
        "incident_location",
        "actor_name",
        "actor_role",
        "what_happened",
        "harm_suffered",
        "user_narrative",
        mode="before",
    )
    @classmethod
    def normalize_text(cls, value: Any) -> Any:
        return _normalize_user_text(value) if isinstance(value, str) else value

    @field_validator("incident_date")
    @classmethod
    def reject_future_incident_date(cls, value: date) -> date:
        if value > _today_in_sri_lanka():
            raise ValueError("Incident date cannot be in the future")
        return value


class StructureIntakeRequest(StrictModel):
    raw_user_description: str = Field(..., min_length=10, max_length=4_000)

    @field_validator("raw_user_description", mode="before")
    @classmethod
    def normalize_description(cls, value: Any) -> Any:
        return _normalize_user_text(value) if isinstance(value, str) else value


class ExtractedIntake(StrictModel):
    """Editable Step 0 output; required fields may still be missing."""

    incident_date: date | None = None
    incident_location: str | None = None
    actor_name: str | None = None
    actor_role: str | None = None
    what_happened: str | None = None
    harm_suffered: str | None = None
    user_narrative: str

    @field_validator(
        "incident_location",
        "actor_name",
        "actor_role",
        "what_happened",
        "harm_suffered",
        "user_narrative",
        mode="before",
    )
    @classmethod
    def normalize_text(cls, value: Any) -> Any:
        return _normalize_user_text(value) if isinstance(value, str) else value

    @field_validator("incident_date")
    @classmethod
    def reject_future_incident_date(cls, value: date | None) -> date | None:
        if value is not None and value > _today_in_sri_lanka():
            raise ValueError("Incident date cannot be in the future")
        return value


class StructureIntakeResponse(StrictModel):
    success: bool
    status: Literal["needs_confirmation", "needs_clarification"]
    can_confirm: bool
    intake: ExtractedIntake
    confirmation_text: str
    missing_required_fields: list[str]
    clarifying_questions: list[str]


class AnalyzeRequest(StrictModel):
    attempt_id: UUID | None = Field(
        default=None,
        description=(
            "Client-generated idempotency key. Reusing it with the same intake "
            "returns the existing attempt instead of creating a duplicate."
        ),
    )
    intake: ConfirmedIntake


class CreateProfileRequest(StrictModel):
    display_name: str = Field(..., min_length=2, max_length=80)
    accept_terms: Literal[True]
    accept_privacy_policy: Literal[True]

    @field_validator("display_name", mode="before")
    @classmethod
    def normalize_display_name(cls, value: Any) -> Any:
        return _normalize_user_text(value) if isinstance(value, str) else value


class UpdateProfileRequest(StrictModel):
    display_name: str = Field(..., min_length=2, max_length=80)

    @field_validator("display_name", mode="before")
    @classmethod
    def normalize_display_name(cls, value: Any) -> Any:
        return _normalize_user_text(value) if isinstance(value, str) else value


class AcceptPoliciesRequest(StrictModel):
    accept_terms: Literal[True]
    accept_privacy_policy: Literal[True]


class AssessmentConsentRequest(StrictModel):
    accept_assessment_processing: Literal[True]


class DeleteAccountRequest(StrictModel):
    confirmation: Literal["DELETE"]


class AnalyzeResponse(StrictModel):
    success: bool
    result: dict[str, Any]


class HealthResponse(StrictModel):
    status: str
