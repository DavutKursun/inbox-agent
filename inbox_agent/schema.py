"""Shared data models: incoming email, tool arguments, agent result."""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

FinishIntent = Literal["booking", "reschedule_cancel", "question", "billing", "complaint", "out_of_scope", "suspicious"]
Intent = Literal[FinishIntent, "unfinished"]  # "unfinished" is set by the loop when the model never called finish; never by the model
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
class ToolArgs(BaseModel):
    """Base for every tool's arguments: unknown fields are rejected, and the JSON schema says additionalProperties: false."""

    model_config = ConfigDict(extra="forbid")


def _iso_date(v: str) -> str:
    try:
        date.fromisoformat(v)
    except ValueError:
        raise ValueError("must be a date like 2026-10-19") from None
    return v


def _iso_local_datetime(v: str) -> str:
    try:
        d = datetime.fromisoformat(v)
    except ValueError:
        raise ValueError("must be a local datetime like 2026-10-19T09:00") from None
    if d.tzinfo is not None:
        raise ValueError("must be a plain local time without a timezone offset, like 2026-10-19T09:00")
    return v


class LookupCustomerArgs(ToolArgs):
    email: EmailStr


class SearchKBArgs(ToolArgs):
    query: str = Field(min_length=2, max_length=300)


class FindSlotsArgs(ToolArgs):
    date_from: str = Field(description="YYYY-MM-DD")
    date_to: str = Field(description="YYYY-MM-DD, at most 14 days after date_from")
    duration_min: int = Field(default=30, ge=15, le=120)

    @field_validator("date_from", "date_to")
    @classmethod
    def _dates(cls, v: str) -> str:
        return _iso_date(v)


class BookSlotArgs(ToolArgs):
    customer_id: int
    start: str = Field(description="Local datetime of a slot returned by find_slots, e.g. 2026-10-19T09:00")
    duration_min: int = Field(default=30, ge=15, le=120)
    reason: str = Field(default="", max_length=200)

    @field_validator("start")
    @classmethod
    def _start(cls, v: str) -> str:
        return _iso_local_datetime(v)


class CancelBookingArgs(ToolArgs):
    booking_id: str
    reason: str = Field(default="", max_length=200)


class DraftReplyArgs(ToolArgs):
    subject: str = Field(max_length=200)
    body: str = Field(min_length=10, max_length=3000)
    citations: list[str] = Field(default_factory=list, description="chunk_ids returned by search_kb in this thread that the body relies on")


class EscalateArgs(ToolArgs):
    reason: str = Field(min_length=5, max_length=300)


class FinishArgs(ToolArgs):
    intent: FinishIntent
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
    customer_email: str = ""  # the sender the action was queued for; ownership is re-checked against it at execution
    status: Literal["pending", "approved", "rejected", "executed", "failed"] = "pending"
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
    stopped_reason: Literal["finish", "max_turns", "no_tool_calls", "error"] = "finish"
