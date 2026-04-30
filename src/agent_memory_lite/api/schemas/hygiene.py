"""Schemas for memory hygiene reports."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict


class HygieneFindingResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str
    severity: str
    target_type: str
    target_id: str
    summary: str
    details: dict[str, Any]


class HygieneReportResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str
    workspace_id: str
    generated_at: str
    counts: dict[str, int]
    findings: list[HygieneFindingResponse]


class QualityGateFindingResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str
    severity: str
    target_type: str
    target_id: str
    summary: str
    details: dict[str, Any]


class QualityGateResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str
    workspace_id: str
    generated_at: str
    counts: dict[str, int]
    findings: list[QualityGateFindingResponse]
