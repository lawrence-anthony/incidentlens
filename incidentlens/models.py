from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

Confidence = Literal["high", "medium", "low"]


class Evidence(BaseModel):
    evidence_id: str
    source_kind: Literal["pipeline_log", "change_event"]
    line_no: int
    ts: datetime | None = None
    level: str | None = None
    component: str | None = None
    event_type: str | None = None
    actor: str | None = None
    target: str | None = None
    message: str
    attributes: dict[str, Any] = Field(default_factory=dict)
    raw: str


class Citation(BaseModel):
    evidence_id: str
    quote: str = ""
    why: str = ""


class Contradiction(BaseModel):
    evidence_id: str
    why: str = ""


class Hypothesis(BaseModel):
    id: str
    statement: str
    category: str = "unknown"
    supporting: list[Citation] = Field(default_factory=list)
    contradicting: list[Contradiction] = Field(default_factory=list)
    confidence: Confidence
    reasoning: str = ""


class MissingEvidence(BaseModel):
    need: str
    why: str = ""


class RecoveryAction(BaseModel):
    id: str
    action: str
    rationale: str = ""
    evidence_ids: list[str] = Field(default_factory=list)
    risk: Literal["low", "medium", "high"] = "medium"
    verify: str = ""
    rollback: str = ""


class ModelAnalysis(BaseModel):
    summary: str
    verdict: Literal["probable_cause", "insufficient_evidence"]
    hypotheses: list[Hypothesis] = Field(default_factory=list)
    missing_evidence: list[MissingEvidence] = Field(default_factory=list)
    recovery_actions: list[RecoveryAction] = Field(default_factory=list)
