"""POST /memory/explain_diff — surface memory items affected by a diff (1.3.0).

Operator pastes a unified-diff (or just a list of changed file paths)
and the endpoint returns the active decisions / theories / behavior
instructions whose territory the diff touches. Two matching modes:

* **Declarative match (precise):** ``decisions.references_json`` is a
  list of file paths or ``path:symbol`` markers. When a diff path appears
  in any decision's references list, that decision is reported with
  ``match='declarative'``.

* **Substring fallback (approximate):** when no declarative match is
  available, scan ``decision_text`` and ``rationale`` for the file
  path. This is the pre-1.3.0 implicit behaviour, kept so older
  decisions written without ``references`` still surface.

Read-only; never mutates. Designed to be called from a pre-commit
hook or by the agent before a non-trivial edit.
"""

from __future__ import annotations

import json
import re

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

from agent_memory_lite.api.deps import DbDep, SettingsDep, ensure_workspace_readable

router = APIRouter()


class ExplainDiffRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str = "default"
    # Either pass a unified-diff text (the endpoint extracts file paths
    # via ``+++ b/<path>`` headers) OR pass an explicit file path list.
    # When both are present, ``files`` takes priority.
    diff_text: str | None = None
    files: list[str] = Field(default_factory=list)
    # Cap the per-section result to keep responses bounded.
    limit_per_section: int = Field(default=10, ge=1, le=50)


class DiffMatchedDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision_id: str
    title: str
    status: str
    importance: float
    match: str  # 'declarative' | 'substring'
    matched_path: str  # which file path triggered the match


class ExplainDiffResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    workspace_id: str
    files: list[str]
    decisions_matched: list[DiffMatchedDecision]
    summary: str


_FILE_LINE_RE = re.compile(r"^\+\+\+ b/(.+)$", re.MULTILINE)


def _extract_files_from_diff(diff_text: str) -> list[str]:
    """Pull file paths from a unified-diff's ``+++ b/<path>`` headers."""
    out: list[str] = []
    seen: set[str] = set()
    for match in _FILE_LINE_RE.finditer(diff_text):
        path = match.group(1).strip()
        if path and path not in seen and path != "/dev/null":
            out.append(path)
            seen.add(path)
    return out


@router.post("/memory/explain_diff", response_model=ExplainDiffResponse)
def explain_diff_route(  # noqa: PLR0912 — declarative-then-substring matching is naturally branchy
    body: ExplainDiffRequest,
    conn: DbDep,
    settings: SettingsDep,
) -> ExplainDiffResponse:
    ensure_workspace_readable(body.workspace_id, settings)

    files = body.files or _extract_files_from_diff(body.diff_text or "")
    files = [f for f in files if f][:50]  # safety cap

    matched: dict[str, DiffMatchedDecision] = {}
    if files:
        rows = conn.execute(
            """
            SELECT id, title, status, importance, decision_text, rationale,
                   COALESCE(
                       (SELECT references_json),
                       NULL
                   ) AS references_json
            FROM decisions
            WHERE workspace_id = ? AND status = 'active'
            """,
            (body.workspace_id,),
        ).fetchall()
        for row in rows:
            decision_id = str(row["id"])
            # Pass 1: declarative match via references_json.
            refs_raw = row["references_json"] if "references_json" in row.keys() else None  # noqa: SIM118
            refs: list[str] = []
            if refs_raw:
                try:
                    parsed = json.loads(refs_raw)
                    if isinstance(parsed, list):
                        refs = [str(x).lower() for x in parsed]
                except (ValueError, TypeError):
                    refs = []
            decl_hit: str | None = None
            for f in files:
                fl = f.lower()
                if any(fl == r or r.startswith(fl + ":") or fl.startswith(r + "/") for r in refs):
                    decl_hit = f
                    break
            if decl_hit is not None:
                matched[decision_id] = DiffMatchedDecision(
                    decision_id=decision_id,
                    title=str(row["title"] or ""),
                    status=str(row["status"]),
                    importance=float(row["importance"]),
                    match="declarative",
                    matched_path=decl_hit,
                )
                continue
            # Pass 2: substring fallback in decision_text + rationale.
            haystack = (str(row["decision_text"] or "") + " " + str(row["rationale"] or "")).lower()
            for f in files:
                if f.lower() in haystack:
                    matched[decision_id] = DiffMatchedDecision(
                        decision_id=decision_id,
                        title=str(row["title"] or ""),
                        status=str(row["status"]),
                        importance=float(row["importance"]),
                        match="substring",
                        matched_path=f,
                    )
                    break

    matched_list = sorted(matched.values(), key=lambda d: (-d.importance, d.title))[
        : body.limit_per_section
    ]
    if not files:
        summary = "no file paths in diff/files; nothing to match"
    elif not matched_list:
        summary = (
            f"{len(files)} files in diff; no active decisions reference them. "
            f"This may be true low-impact change or decisions lack `references` "
            f"declarations — consider populating decisions.references when "
            f"you next edit those areas."
        )
    else:
        decl = sum(1 for d in matched_list if d.match == "declarative")
        sub = len(matched_list) - decl
        summary = (
            f"{len(files)} files; {len(matched_list)} active decisions matched "
            f"({decl} declarative, {sub} substring fallback). Review high-importance "
            f"matches before committing."
        )
    return ExplainDiffResponse(
        workspace_id=body.workspace_id,
        files=files,
        decisions_matched=matched_list,
        summary=summary,
    )
