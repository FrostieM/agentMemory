"""Suggest capabilities for a decision/theory at write time (Move 3).

Move 1 closes provenance (source_episode_id), Move 2 bundles the
3-step ritual into one call. Both still leave the agent to PICK which
capability to link. Move 3 makes the pick easier: server ranks the
workspace's roles/skills/playbooks by token overlap with the decision
text and returns the top-3 suggestions in the response.

Read-only — never auto-links. Returning suggestions is the additive
change; the agent (or operator via /ui/review) decides whether to
accept any of them. Auto-link is intentionally NOT implemented in v1
because (a) it would require a higher confidence bar than token
overlap to be safe, and (b) it crosses the trust-gate philosophy of
"system suggests, operator confirms".

Strategy is simple Jaccard on tokenized fields — fast (microseconds
per workspace), no embedding pass, deterministic. If quality turns
out poor in practice, swap _score_for_text for an embedding-based
cosine without changing the public surface.
"""

from __future__ import annotations

import contextlib
import json
import re
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass

from agent_memory_lite.ingestion._capability_suggester_stopwords import STOPWORDS as _STOPWORDS

_TOKEN_RE = re.compile(r"\w+", re.UNICODE)


@dataclass(frozen=True, slots=True)
class CapabilitySuggestion:
    capability_type: str  # role / skill / playbook
    capability_id: str
    capability_name: str
    score: float  # 0.0..1.0 overlap-coefficient (|hits| / min(|target|, |cap|))
    snippet: str  # first ~80 chars of the capability's primary text


def _tokenize(text: str) -> set[str]:
    return {
        tok.lower()
        for tok in _TOKEN_RE.findall(text or "")
        if len(tok) > 2 and tok.lower() not in _STOPWORDS
    }


def _overlap_coefficient(a: set[str], b: set[str]) -> float:
    """Sørensen / overlap coefficient: |a ∩ b| / min(|a|, |b|).

    Pure Jaccard penalises asymmetric set sizes — a 13-token decision
    against a 90-token capability description gets ratio < 0.05 even
    when half the decision tokens hit. Overlap-coefficient ignores the
    larger set's size and asks "what fraction of the SMALLER set
    matched", which is the right question for "is this capability
    relevant to this decision?".
    """
    if not a or not b:
        return 0.0
    return len(a & b) / min(len(a), len(b))


def _capability_text_skill(row: sqlite3.Row) -> str:
    parts = [str(row["name"] or ""), str(row["summary"] or "")]
    for col in ("when_to_use_json", "inputs_json", "outputs_json"):
        with contextlib.suppress(TypeError, ValueError, json.JSONDecodeError):
            parts.extend(json.loads(row[col] or "[]"))
    return " ".join(p for p in parts if p)


def _capability_text_role(row: sqlite3.Row) -> str:
    parts = [str(row["name"] or ""), str(row["purpose"] or "")]
    for col in ("responsibilities_json", "boundaries_json"):
        with contextlib.suppress(TypeError, ValueError, json.JSONDecodeError):
            parts.extend(json.loads(row[col] or "[]"))
    return " ".join(p for p in parts if p)


def _capability_text_playbook(row: sqlite3.Row) -> str:
    parts = [str(row["name"] or ""), str(row["goal"] or "")]
    for col in ("triggers_json", "steps_json", "success_criteria_json"):
        with contextlib.suppress(TypeError, ValueError, json.JSONDecodeError):
            parts.extend(json.loads(row[col] or "[]"))
    return " ".join(p for p in parts if p)


_TextFn = Callable[[sqlite3.Row], str]
_TABLE_CONFIG: tuple[tuple[str, str, str, _TextFn], ...] = (
    ("skill", "agent_skills", "summary", _capability_text_skill),
    ("role", "agent_roles", "purpose", _capability_text_role),
    ("playbook", "agent_playbooks", "goal", _capability_text_playbook),
)


def suggest_capabilities_for_decision(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    title: str,
    text: str,
    rationale: str | None = None,
    limit: int = 3,
) -> list[CapabilitySuggestion]:
    """Convenience wrapper: tokenize title+text+rationale and rank."""
    parts = " ".join(filter(None, [title, text, rationale]))
    return suggest_capabilities(conn, workspace_id=workspace_id, text=parts, limit=limit)


def suggest_capabilities(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    text: str,
    limit: int = 3,
    min_score: float = 0.1,
) -> list[CapabilitySuggestion]:
    """Return top-N capability suggestions ranked by overlap-coefficient.

    Empty list when the workspace has no capabilities, when the text
    has no scoreable tokens, or when no candidate clears ``min_score``.
    """
    target = _tokenize(text)
    if not target:
        return []
    out: list[CapabilitySuggestion] = []
    conn.row_factory = sqlite3.Row
    for kind, table, primary_col, text_fn in _TABLE_CONFIG:
        try:
            rows = conn.execute(
                f"SELECT * FROM {table} WHERE workspace_id = ?", (workspace_id,)
            ).fetchall()
        except sqlite3.OperationalError:
            continue
        for row in rows:
            cap_text = text_fn(row)
            score = _overlap_coefficient(target, _tokenize(cap_text))
            if score < min_score:
                continue
            primary = str(row[primary_col] or "")[:80]
            out.append(
                CapabilitySuggestion(
                    capability_type=kind,
                    capability_id=str(row["id"]),
                    capability_name=str(row["name"]),
                    score=round(score, 4),
                    snippet=primary,
                )
            )
    out.sort(key=lambda s: -s.score)
    return out[:limit]
