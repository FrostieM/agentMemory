"""Detector that fires on user_correction episodes (v1.10).

Pipeline: hook ingests claim + user_correction pair → auto_promote
runs this extractor → emits MemoryCandidate(kind=CORRECTION) →
candidate surfaces in <pending_review> → operator promotes via
/memory/promote_candidate_to_behavior. Active operator flow lives in
``docs/OPERATIONS.md``; historical design notes live in git history.

SECURITY: ``_resolve_claim`` filters by workspace_id AND requires
the referenced episode to carry ``metadata.kind="correction_target"``.
Both layers are needed to prevent forged ids from leaking arbitrary
episode text.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from agent_memory_lite.extraction.correction_distill import (
    build_correction_candidate,
    count_corrections_today,
    distill_rule,
    is_correction_target,
    record_throttle_rejection,
)
from agent_memory_lite.extraction.correction_patterns import match_correction
from agent_memory_lite.logging_setup import get_logger
from agent_memory_lite.models.candidates import MemoryCandidate
from agent_memory_lite.models.episodes import Episode

_log = get_logger("extraction.correction")

# Both keys are accepted: ``correction_role`` is the v1.10 namespaced
# marker; ``kind`` is the legacy field kept for backward compatibility
# with episodes ingested by older hook versions.
_USER_CORRECTION_KIND = "user_correction"
_USER_CORRECTION_ROLE = "user_correction"


class CorrectionExtractor:
    """Extractor that emits CORRECTION candidates from paired episodes.

    Receives a sqlite connection at construction so it can resolve the
    paired claim text and enforce the per-day throttle. The
    auto_promote builder is responsible for passing the connection —
    see ingestion/auto_promote.py.
    """

    name = "correction"

    def __init__(
        self,
        conn: sqlite3.Connection,
        *,
        min_user_len: int = 30,
        min_agent_len: int = 50,
        min_confidence: float = 0.5,
        max_per_day: int = 20,
    ) -> None:
        self._conn = conn
        self._min_user_len = min_user_len
        self._min_agent_len = min_agent_len
        self._min_confidence = min_confidence
        self._max_per_day = max_per_day

    def extract(self, episode: Episode) -> list[MemoryCandidate]:
        meta: dict[str, Any] = dict(episode.metadata or {})
        # Accept both the namespaced role and the legacy kind field so
        # episodes ingested before the v1.10 rename still parse.
        if (
            meta.get("correction_role") != _USER_CORRECTION_ROLE
            and meta.get("kind") != _USER_CORRECTION_KIND
        ):
            return []

        match = match_correction(
            episode.raw_text or "",
            min_user_len=self._min_user_len,
            min_confidence=self._min_confidence,
        )
        if not match.matched:
            return []

        if self._throttled(episode.workspace_id):
            _log.info(
                "correction_rejected_throttled",
                episode_id=episode.id,
                workspace_id=episode.workspace_id,
                max_per_day=self._max_per_day,
            )
            record_throttle_rejection(
                self._conn,
                workspace_id=episode.workspace_id,
                episode_id=episode.id,
                max_per_day=self._max_per_day,
            )
            return []

        claim_text, claim_episode_id = self._resolve_claim(meta, workspace_id=episode.workspace_id)
        if not claim_text or len(claim_text.strip()) < self._min_agent_len:
            return []

        correction_text = (episode.raw_text or "").strip()
        subject = distill_rule(claim_text, correction_text)
        if len(subject) < 20:
            _log.debug(
                "correction_no_lesson",
                episode_id=episode.id,
                claim_id=claim_episode_id,
                subject_len=len(subject),
            )
            return []

        return [
            build_correction_candidate(
                episode=episode,
                match=match,
                claim_text=claim_text,
                claim_episode_id=claim_episode_id,
                correction_text=correction_text,
                subject=subject,
            )
        ]

    def _throttled(self, workspace_id: str) -> bool:
        return count_corrections_today(self._conn, workspace_id) >= self._max_per_day

    def _resolve_claim(
        self, meta: dict[str, Any], *, workspace_id: str
    ) -> tuple[str | None, str | None]:
        """Resolve the paired claim text scoped to ``workspace_id``.

        SECURITY layers:
        * WHERE workspace_id = ? — blocks cross-workspace forgery (R1).
        * metadata_json check — the referenced episode must have been
          ingested as ``metadata.kind="correction_target"``. Without this,
          an attacker who knows any same-workspace episode id (e.g. a
          sensitive past episode) could forge it as the claim and have
          its text leaked into the correction candidate's evidence
          field. The hook always sets the marker; legitimate paths
          always pass.
        """
        claim_id = meta.get("correction_target_episode_id")
        if not isinstance(claim_id, str) or not claim_id:
            return None, None
        try:
            row = self._conn.execute(
                "SELECT raw_text, metadata_json FROM episodes WHERE id = ? AND workspace_id = ?",
                (claim_id, workspace_id),
            ).fetchone()
        except sqlite3.OperationalError:
            return None, None
        if row is None:
            return None, claim_id
        # sqlite3.Row indexes by name; tuples use index access.
        try:
            text = row["raw_text"]
            metadata_raw = row["metadata_json"]
        except (IndexError, KeyError):
            text = row[0]
            metadata_raw = row[1] if len(row) > 1 else "{}"
        if not is_correction_target(metadata_raw):
            _log.info(
                "correction_target_provenance_rejected",
                claim_id=claim_id,
                workspace_id=workspace_id,
            )
            return None, claim_id
        return (str(text) if text is not None else None, claim_id)
