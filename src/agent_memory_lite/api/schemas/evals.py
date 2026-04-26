"""Wire-side schemas for `memory_run_evals`."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class RunEvalsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str = "default"


class RunEvalsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cases_run: int
    cases_passed: int
    retrieval_recall_at_10: float
    retrieval_precision_at_10: float
    stale_fact_rate: float
    secret_leak_count: int
    prompt_injection_failures: int
    failures: list[str]
