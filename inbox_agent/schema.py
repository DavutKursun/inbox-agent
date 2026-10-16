"""Shared data models: incoming email, tool arguments, agent result."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, EmailStr, Field, field_validator

Intent = Literal["booking", "reschedule_cancel", "question", "billing", "complaint", "out_of_scope", "suspicious"]
Risk = Literal["auto", "approval", "forbidden"]


class Email(BaseModel):
    from_email: EmailStr = Field(alias="from")
    subject: str = Field(max_length=300)
    body: str = Field(min_length=1)
    model_config = {"populate_by_name": True}

    @field_validator("body")
    @classmethod
    def _strip(cls, v: str) -> str:
        return v.strip()


# --- tool argument schemas (the model can only produce these) ----------------------
class LookupCustomerArgs(BaseModel):
    email: EmailStr


class SearchKBArgs(BaseModel):
    query: str = Field(min_length=2, max_length=300)


class FindSlotsArgs(BaseModel):
    date_from: str = Field(description="YYYY-MM-DD")
    date_to: str = Field(description="YYYY-MM-DD, at most 14 days after date_from")
    duration_min: int = Field(default=30, ge=15, le=120)


class BookSlotArgs(BaseModel):
    customer_id: int
    start: str = Field(description="ISO datetime of a slot returned by find_slots")
    duration_min: int = Field(default=30, ge=15, le=120)
    reason: str = Field(default="", max_length=200)


class CancelBookingArgs(BaseModel):
    booking_id: str
    reason: str = Field(default="", max_length=200)


class DraftReplyArgs(BaseModel):
    subject: str = Field(max_length=200)
    body: str = Field(min_length=10, max_length=3000)
    citations: list[str] = Field(default_factory=list, description="KB page ids the body relies on")


class EscalateArgs(BaseModel):
    reason: str = Field(min_length=5, max_length=300)


class FinishArgs(BaseModel):
    intent: Intent
    summary: str = Field(min_length=5, max_length=500)


# --- results -----------------------------------------------------------------------
class ToolCall(BaseModel):
    id: str
    name: str
    args: dict


class ToolEvent(BaseModel):
    """One executed (or queued / refused) tool call, as logged in the trace."""

    turn: int
    call: ToolCall
    status: Literal["ok", "queued", "refused", "error"]
    result: dict | list | str | None = None


class PendingAction(BaseModel):
    action_id: str
    thread_id: str
    tool: str
    args: dict
    status: Literal["pending", "approved", "rejected", "executed"] = "pending"
    result: dict | str | None = None


class Draft(BaseModel):
    subject: str
    body: str
    citations: list[str] = Field(default_factory=list)
    unverified_claim: bool = False  # states a policy/price without a citation


class AgentResult(BaseModel):
    thread_id: str
    intent: Intent
    summary: str
    draft_reply: Draft | None = None
    actions_taken: list[ToolEvent] = Field(default_factory=list)
    actions_pending: list[PendingAction] = Field(default_factory=list)
    escalated: bool = False
    escalation_reason: str = ""
    turns: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    model: str = ""
    latency_ms: float = 0.0
    stopped_reason: Literal["finish", "max_turns", "error"] = "finish"
