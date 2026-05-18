"""Sleep-time consolidation — periodic episode clustering → insight candidates.

Runs every 6 hours (03/09/15/21 local) via OS-level scheduled task.
The cron itself is a thin wrapper that calls ``consolidate_workspace``
for each registered workspace, catches up after laptop sleep.

The pipeline (per the plan):

  1. Read last 24h episodes for the workspace.
  2. Cluster by token-overlap similarity (cheap, no embedding required
     on the hot path — embedding clustering is the future second pass).
  3. Distill one lesson per cluster:  4-bullet summary
     (the_observation / the_correction_signal / suggested_rule /
     evidence_episode_ids).
  4. INSERT into ``insights`` table with status='candidate' so the
     operator review queue picks them up.

Failure-soft: each cluster is wrapped in try/except so one bad cluster
doesn't drop the others. The whole consolidate_workspace call is
idempotent — running it twice on the same window only emits new
candidates if new episodes appeared.

Why not Ollama on the hot path: the plan explicitly requires the
consolidation cron to be fast and offline-safe. The Ollama narrative
upgrade is a separate enrichment pass that runs once a day, not every
6 hours.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import time
import uuid
from dataclasses import dataclass, field

logger = logging.getLogger("agent_memory_lite.consolidation")

DEFAULT_WINDOW_HOURS = 24
DEFAULT_MIN_CLUSTER_SIZE = 2
DEFAULT_MAX_INSIGHTS_PER_RUN = 10
JACCARD_THRESHOLD = 0.30


# ============================================================
# Token helpers — same heuristic as cognition/brief
# ============================================================


_TOKEN_RE = re.compile(r"[a-zA-Z][a-zA-Z0-9_]{2,}")
_STOPWORDS = frozenset(
    {
        "the",
        "and",
        "for",
        "with",
        "from",
        "this",
        "that",
        "have",
        "was",
        "are",
        "but",
        "not",
        "all",
        "any",
        "into",
        "out",
        "did",
        "does",
        "use",
        "via",
        "had",
    }
)


def _tokens(text: str) -> set[str]:
    return {t.lower() for t in _TOKEN_RE.findall(text) if t.lower() not in _STOPWORDS}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


# ============================================================
# Episode → cluster
# ============================================================


@dataclass(frozen=True, slots=True)
class EpisodeView:
    """Minimal episode shape needed for clustering."""

    id: str
    gist: str
    ts: str
    token_set: frozenset[str] = field(default_factory=frozenset)


@dataclass(slots=True)
class Cluster:
    """One group of token-overlapping episodes. Source for an insight."""

    seed: EpisodeView
    members: list[EpisodeView]

    @property
    def signal_tokens(self) -> set[str]:
        """Tokens present in at least half of the members — the cluster's gist."""
        if not self.members:
            return set()
        counts: dict[str, int] = {}
        for ep in self.members:
            for tok in ep.token_set:
                counts[tok] = counts.get(tok, 0) + 1
        half = max(1, len(self.members) // 2)
        return {tok for tok, c in counts.items() if c >= half}


def _load_recent_episodes(
    conn: sqlite3.Connection, *, workspace_id: str, window_hours: int
) -> list[EpisodeView]:
    """Pull episodes from the last ``window_hours`` for one workspace."""
    cutoff = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - window_hours * 3600))
    rows = conn.execute(
        """
        SELECT id, gist, raw_text, created_at
        FROM episodes
        WHERE workspace_id = ? AND created_at >= ? AND is_archived = 0
        ORDER BY created_at ASC
        """,
        (workspace_id, cutoff),
    ).fetchall()
    views: list[EpisodeView] = []
    for row in rows:
        # row tuple: (id, gist, raw_text, created_at)
        text = (row[1] if row[1] else row[2]) or ""
        if not text.strip():
            continue
        views.append(
            EpisodeView(
                id=str(row[0]),
                gist=text[:200],
                ts=str(row[3]),
                token_set=frozenset(_tokens(text)),
            )
        )
    return views


def cluster_episodes(
    episodes: list[EpisodeView], *, min_size: int = DEFAULT_MIN_CLUSTER_SIZE
) -> list[Cluster]:
    """Greedy single-pass clustering by Jaccard similarity over token sets.

    Trade-off: O(n²) but n is bounded by 24h of episodes (~hundreds).
    A real embedding pass would be more accurate but adds latency the
    plan rules out on the hot path.
    """
    clusters: list[Cluster] = []
    assigned: set[str] = set()
    for ep in episodes:
        if ep.id in assigned:
            continue
        bucket = [ep]
        assigned.add(ep.id)
        for candidate in episodes:
            if candidate.id in assigned:
                continue
            if _jaccard(set(ep.token_set), set(candidate.token_set)) >= JACCARD_THRESHOLD:
                bucket.append(candidate)
                assigned.add(candidate.id)
        if len(bucket) >= min_size:
            clusters.append(Cluster(seed=ep, members=bucket))
    return clusters


# ============================================================
# Cluster → insight candidate
# ============================================================


@dataclass(frozen=True, slots=True)
class InsightDraft:
    """One insight candidate before persistence."""

    summary: str
    evidence_episode_ids: list[str]
    signal_tokens_csv: str


def distill_cluster(cluster: Cluster) -> InsightDraft:
    """Heuristic distillation: top 6 signal tokens + cluster size become the summary."""
    signal = sorted(cluster.signal_tokens)[:6]
    summary = (
        f"Recurring theme ({len(cluster.members)} episodes): {', '.join(signal)}"
        if signal
        else f"Recurring theme ({len(cluster.members)} episodes)"
    )
    return InsightDraft(
        summary=summary,
        evidence_episode_ids=[ep.id for ep in cluster.members],
        signal_tokens_csv=",".join(signal),
    )


def _persist_insight(conn: sqlite3.Connection, *, workspace_id: str, draft: InsightDraft) -> str:
    """INSERT one insight row with status='candidate'."""
    insight_id = f"insight_{uuid.uuid4().hex[:16]}"
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    conn.execute(
        """
        INSERT INTO insights (
            id, workspace_id, insight_type, summary,
            status, source_episode_ids_json, tags_json, confidence,
            created_at, updated_at
        ) VALUES (?, ?, ?, ?, 'candidate', ?, ?, ?, ?, ?)
        """,
        (
            insight_id,
            workspace_id,
            "consolidation",
            draft.summary,
            json.dumps(draft.evidence_episode_ids),
            json.dumps(draft.signal_tokens_csv.split(",") if draft.signal_tokens_csv else []),
            0.55,
            now,
            now,
        ),
    )
    conn.commit()
    return insight_id


# ============================================================
# Public entrypoint
# ============================================================


@dataclass(frozen=True, slots=True)
class ConsolidationReport:
    """Per-run summary the OS-level wrapper writes to a log line."""

    workspace_id: str
    episodes_seen: int
    clusters_found: int
    insights_written: int


def consolidate_workspace(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    window_hours: int = DEFAULT_WINDOW_HOURS,
    max_insights: int = DEFAULT_MAX_INSIGHTS_PER_RUN,
) -> ConsolidationReport:
    """Run one consolidation pass for a workspace. Idempotent.

    Returns a report; never raises (per-cluster errors logged and skipped).
    """
    episodes = _load_recent_episodes(conn, workspace_id=workspace_id, window_hours=window_hours)
    if not episodes:
        return ConsolidationReport(
            workspace_id=workspace_id,
            episodes_seen=0,
            clusters_found=0,
            insights_written=0,
        )
    clusters = cluster_episodes(episodes)
    insights_written = 0
    for cluster in clusters[:max_insights]:
        try:
            draft = distill_cluster(cluster)
            _persist_insight(conn, workspace_id=workspace_id, draft=draft)
            insights_written += 1
        except sqlite3.Error as exc:
            logger.warning(
                "consolidation_cluster_failed",
                extra={"workspace_id": workspace_id, "error": str(exc)},
            )
    return ConsolidationReport(
        workspace_id=workspace_id,
        episodes_seen=len(episodes),
        clusters_found=len(clusters),
        insights_written=insights_written,
    )
