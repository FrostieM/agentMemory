"""Pure helpers for v1.10: rule distillation, throttle counter,
candidate builder, and audit row writers. Splitting these out keeps
``correction_extractor.py`` under the 150-SLOC ceiling."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

from agent_memory_lite.extraction.correction_patterns import CorrectionMatch
from agent_memory_lite.models.candidates import MemoryCandidate, TemporalSpan
from agent_memory_lite.models.enums import MemoryCandidateKind
from agent_memory_lite.models.episodes import Episode

_CLIP_CHARS = 160


def count_corrections_today(conn: sqlite3.Connection, workspace_id: str) -> int:
    """Count memory_candidates(kind='correction') rows written today (UTC)
    in the given workspace. Returns 0 when the table is missing."""
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    sql = (
        "SELECT COUNT(*) FROM memory_candidates "
        "WHERE workspace_id = ? AND kind = 'correction' "
        "AND substr(created_at, 1, 10) = ?"
    )
    try:
        row = conn.execute(sql, (workspace_id, today)).fetchone()
    except sqlite3.OperationalError:
        return 0
    return int(row[0]) if row else 0


def build_correction_candidate(
    *,
    episode: Episode,
    match: CorrectionMatch,
    claim_text: str,
    claim_episode_id: str | None,
    correction_text: str,
    subject: str,
) -> MemoryCandidate:
    """Construct a MemoryCandidate(kind=CORRECTION) from the matched pair."""
    claim_clipped = clip(claim_text, _CLIP_CHARS)
    correction_clipped = clip(correction_text, _CLIP_CHARS)
    return MemoryCandidate(
        kind=MemoryCandidateKind.CORRECTION,
        subject=subject,
        predicate="should_avoid",
        evidence=f"agent claim: {claim_clipped}\nuser correction: {correction_clipped}",
        confidence=match.confidence,
        importance=match.confidence,
        trust_level=episode.trust_level,
        temporal=TemporalSpan(observed_at=episode.created_at, valid_from=episode.created_at),
        source_episode_id=episode.id,
        write_targets=["behavior_instruction", "audit"],
        metadata={
            "agent_claim_episode_id": claim_episode_id,
            "claim_clipped": claim_clipped,
            "correction_clipped": correction_clipped,
            "matched_opener": match.opener_match,
            "matched_body": match.body_match,
        },
    )


def is_correction_target(metadata_raw: object) -> bool:
    """SECURITY: True when episode metadata marks it as correction target.

    Accepts the v1.10 namespaced marker ``metadata.correction_role
    == "claim"`` (preferred — least likely to collide with future
    metadata.kind uses) OR the legacy ``metadata.kind ==
    "correction_target"`` for episodes ingested before the namespace
    rename. Tolerates missing / unparseable metadata by returning
    False.
    """
    import json as _json  # noqa: PLC0415

    if metadata_raw is None:
        return False
    raw = str(metadata_raw)
    if not raw:
        return False
    try:
        meta = _json.loads(raw)
    except (ValueError, _json.JSONDecodeError):
        return False
    if not isinstance(meta, dict):
        return False
    return meta.get("correction_role") == "claim" or meta.get("kind") == "correction_target"


def record_throttle_rejection(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    episode_id: str,
    max_per_day: int,
) -> None:
    """Persist a throttle rejection to audit_log, swallowing legacy-schema errors.

    Imports ``insert_audit`` lazily to keep the distill helpers
    free of repository deps for the unit tests that patch the
    correction_distill module in isolation.
    """
    from agent_memory_lite.repositories.audit_repo import insert_audit  # noqa: PLC0415

    try:
        insert_audit(
            conn,
            workspace_id=workspace_id,
            action="extraction.correction_rejected_throttled",
            target_type="episode",
            target_id=episode_id,
            source_episode_id=episode_id,
            after={"max_per_day": max_per_day},
        )
        conn.commit()
    except sqlite3.OperationalError:
        pass


def clip(text: str, n: int) -> str:
    """Trim text to ``n`` chars, appending an ellipsis when truncated."""
    text = (text or "").strip()
    if len(text) <= n:
        return text
    return text[: max(0, n - 1)].rstrip() + "…"


# Leading-correction openers stripped before the rule body so the final
# line reads cleanly ("Verify before claiming: <body>" — never "Verify
# before claiming: нет, <body>"). Keep the prefixes lowercased and
# matched case-insensitively at the start of the trimmed text.
_RULE_PREFIXES: tuple[str, ...] = (
    "нет, ",
    "нет,",
    "нет ",
    "no, ",
    "no,",
    "no ",
    "wait, ",
    "wait,",
    "actually, ",
    "actually,",
    "поправляю ",
    "поправляю,",
    "неправильно, ",
    "неверно, ",
)


def distill_rule(claim: str, correction: str) -> str:
    """One-line "Verify before claiming: ..." rule from the correction.

    Takes the first sentence (split on .!? or newline), strips leading
    negation openers, clips to 120 chars. Operator can override at
    promote with ``rule_text_override``. ``claim`` is reserved for a
    future LLM-aware distillation.
    """
    _ = claim
    text = (correction or "").strip()
    for sep in ("\n", ". ", "! ", "? "):
        if sep in text:
            text = text.split(sep, 1)[0]
            break
    text = text.strip().rstrip(".!?")
    for prefix in _RULE_PREFIXES:
        if text.lower().startswith(prefix):
            text = text[len(prefix) :].strip()
            break
    if not text:
        return ""
    if len(text) > 120:
        text = text[:119].rstrip() + "…"
    return f"Verify before claiming: {text}"
