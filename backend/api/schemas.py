"""
himikama/backend/api/schemas.py
═══════════════════════════════════════════════════════════════
Pydantic request/response schemas for Himikama FastAPI backend.
═══════════════════════════════════════════════════════════════
"""

from typing import Any

from pydantic import BaseModel, Field


class ConfirmedIntake(BaseModel):
    """
    Confirmed intake object passed into the Himikama reasoning chain.

    This should match what chain.runner.run_full_chain() expects.
    """

    incident_date: str = Field(
        ...,
        description="Date of incident. Preferred format: YYYY-MM-DD.",
        examples=["2026-05-10"],
    )

    incident_location: str | None = Field(
        default=None,
        description="Where the incident occurred.",
        examples=["Colombo"],
    )

    actor_name: str | None = Field(
        default=None,
        description="Name of actor or institution, if known.",
        examples=["Sri Lanka Police"],
    )

    actor_role: str = Field(
        ...,
        description="Role of the actor.",
        examples=["police officers"],
    )

    what_happened: str = Field(
        ...,
        min_length=10,
        description="Confirmed factual description of what happened.",
        examples=[
            "The user was arrested by police officers without being shown a warrant."
        ],
    )

    harm_suffered: str | None = Field(
        default=None,
        description="Harm suffered by the user.",
        examples=["Loss of liberty, distress, and missed work."],
    )

    user_narrative: str = Field(
        ...,
        min_length=10,
        description="Original or confirmed user narrative.",
        examples=[
            "On 10 May 2026, police officers arrested me in Colombo without showing a warrant."
        ],
    )


class AnalyzeRequest(BaseModel):
    """
    Request body for running analysis.
    """

    intake: ConfirmedIntake


class AnalyzeResponse(BaseModel):
    """
    Response returned by the analysis endpoint.

    result is flexible because runner.py returns a large nested object.
    """

    success: bool
    result: dict[str, Any]


class HealthResponse(BaseModel):
    status: str
