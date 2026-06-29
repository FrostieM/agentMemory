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
    # Exclude auto-ingest event episodes (file_indexed, ...). On a code-indexed
    # workspace they are 95%+ of the corpus and their token surface (module /
    # directory names) yields file-shaped "insights" that are pure noise. Reuse
    # the blindspot scanner's exclusion list so the two filters stay in lockstep
    # (no parallel env var). NULL-safe: a NULL source_type is genuine content.
    from agent_memory_lite.maintenance.blindspot_filters import (  # noqa: PLC0415
        excluded_source_types,
    )

    excluded = excluded_source_types()
    src_clause = ""
    params: list[object] = [workspace_id, cutoff]
    if excluded:
        placeholders = ", ".join("?" * len(excluded))
        src_clause = f"AND (source_type IS NULL OR source_type NOT IN ({placeholders}))"
        params.extend(excluded)
    rows = conn.execute(
        f"""
        SELECT id, gist, raw_text, created_at
        FROM episodes
        WHERE workspace_id = ? AND created_at >= ? AND is_archived = 0
          {src_clause}
        ORDER BY created_at ASC
        """,
        params,
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


# v3.7 memory-quality: the heuristic fallback summary below is a
# word-frequency token bag ("Recurring theme (N episodes): a, b"), not a
# real insight. consolidate_workspace skips persisting an insight whose
# summary matches this — otherwise every pass regenerates the same brief
# noise the operator must reject. compaction/promote_insight_to_behavior.py
# keeps its own copy for the promotion gate (sibling defense-in-depth).
_HEURISTIC_NOISE_RE = re.compile(r"^\s*Recurring theme\s*\(\d+\s*episode", re.IGNORECASE)


def _heuristic_summary(cluster: Cluster, signal: list[str]) -> str:
    """Legacy word-frequency summary — used as fallback when LLM is off
    or unreachable."""
    return (
        f"Recurring theme ({len(cluster.members)} episodes): {', '.join(signal)}"
        if signal
        else f"Recurring theme ({len(cluster.members)} episodes)"
    )


def _try_llm_summary(cluster: Cluster, signal: list[str]) -> str | None:
    """v3.2: Ollama-augmented 1-line pattern statement. Returns ``None``
    on any failure so the caller falls back to ``_heuristic_summary``."""
    try:
        from agent_memory_lite.cognition.consolidation_llm import (  # noqa: PLC0415
            llm_distill_cluster,
        )
        from agent_memory_lite.config.settings import get_settings  # noqa: PLC0415
    except ImportError:
        return None
    try:
        settings = get_settings()
        if not settings.consolidation_llm_enabled:
            return None
        base_url = str(getattr(settings, "llm_base_url", "") or "")
        model = str(getattr(settings, "llm_model", "") or "")
    except Exception:  # pragma: no cover - defensive
        return None
    excerpts = [ep.gist for ep in cluster.members[:5] if ep.gist.strip()]
    if not excerpts:
        return None
    return llm_distill_cluster(
        excerpts=excerpts,
        signal_tokens=signal,
        member_count=len(cluster.members),
        base_url=base_url,
        model=model,
    )


def distill_cluster(cluster: Cluster) -> InsightDraft:
    """v3.2: LLM-augmented distillation with heuristic fallback.

    Tries the Ollama path first (``_try_llm_summary``) so insights read
    like "Pattern: agent ingests file_indexed events from pre-commit
    hook" instead of the word-frequency salad "Recurring theme (5
    episodes): docs, file_indexed". Falls back to the v3.1 heuristic
    when LLM is disabled / unreachable / returns NO_PATTERN.
    """
    signal = sorted(cluster.signal_tokens)[:6]
    summary = _try_llm_summary(cluster, signal) or _heuristic_summary(cluster, signal)
    return InsightDraft(
        summary=summary,
        evidence_episode_ids=[ep.id for ep in cluster.members],
        signal_tokens_csv=",".join(signal),
    )


def _persist_insight(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    draft: InsightDraft,
    insight_type: str = "consolidation",
) -> str:
    """INSERT one insight row with status='candidate'."""
    insight_id = f"insight_{uuid.uuid4().hex[:16]}"
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    # Redact the LLM/heuristic-derived summary BEFORE it reaches SQLite + durable_fts
    # (CLAUDE.md: "Never store secrets"). The summary is built from episode gists
    # (themselves redacted at ingest), but redact here too so the durable-row
    # invariant is uniform and defends against an LLM echoing a secret-shaped token.
    from agent_memory_lite.redaction.redactor import redact  # noqa: PLC0415

    summary_safe = redact(draft.summary).text
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
            insight_type,
            summary_safe,
            json.dumps(draft.evidence_episode_ids),
            json.dumps(draft.signal_tokens_csv.split(",") if draft.signal_tokens_csv else []),
            0.55,
            now,
            now,
        ),
    )
    # M1 (write-atomicity batch): consolidation runs from its own cron, INSERTing
    # the insight row directly and bypassing write_canonical's FTS sync choke
    # point. insight is a DURABLE_FTS_KIND, so without this sync the consolidated
    # insight (its searchable summary) is invisible to memory_search until a later
    # brain-pass durable_fts rebuild tick. Failure-soft -- never breaks the write.
    from agent_memory_lite.fts.durable_fts import sync_durable_fts  # noqa: PLC0415

    if not sync_durable_fts(conn, kind="insight", object_id=insight_id, workspace_id=workspace_id):
        logger.warning(
            "durable_fts_sync_skipped_on_consolidation_insight",
            extra={"workspace_id": workspace_id, "insight_id": insight_id},
        )
    conn.commit()
    return insight_id


def _pinned_behavior_token_sets(
    conn: sqlite3.Connection, *, workspace_id: str
) -> list[frozenset[str]]:
    """Return token sets of all pinned, active behavior rules.

    Used by the behavior_reinforcement detector: a cluster whose
    signal_tokens overlap >= 0.6 Jaccard with a pinned behavior is
    almost certainly the SAME rule re-emerging, not a novel insight.
    Tag it as ``behavior_reinforcement`` instead of generic
    consolidation so the operator review queue can act on it.
    """
    try:
        rows = conn.execute(
            "SELECT name, rule, rule_one_line FROM behaviors "
            "WHERE workspace_id = ? AND active = 1 AND pinned = 1",
            (workspace_id,),
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    sets: list[frozenset[str]] = []
    for row in rows:
        text = " ".join(str(row[i] or "") for i in range(3))  # name + rule + rule_one_line
        sets.append(frozenset(_tokens(text)))
    return sets


def _matches_pinned_behavior(
    cluster_tokens: set[str], behavior_token_sets: list[frozenset[str]], threshold: float = 0.6
) -> bool:
    """Return True if cluster_tokens overlap any behavior_token_set >= threshold."""
    if not cluster_tokens or not behavior_token_sets:
        return False
    for behavior_tokens in behavior_token_sets:
        if _jaccard(cluster_tokens, set(behavior_tokens)) >= threshold:
            return True
    return False


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
    # v3.7: clusters whose only summary was word-frequency heuristic
    # noise — skipped, not persisted (see _HEURISTIC_NOISE_RE).
    insights_skipped: int = 0


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
    insights_skipped = 0
    # Phase 3: precompute pinned behavior token sets so we can detect
    # clusters that re-encode an already-installed rule.
    pinned_behavior_tokens = _pinned_behavior_token_sets(conn, workspace_id=workspace_id)
    for cluster in clusters[:max_insights]:
        try:
            draft = distill_cluster(cluster)
            # v3.7 memory-quality: a cluster whose only summary is the
            # word-frequency heuristic fallback ("Recurring theme (N
            # episodes): tok, tok") is a token bag, not an insight.
            # Persisting it just regenerates the same brief noise every
            # pass — skip it. A real insight needs the LLM "Pattern: ..."
            # distillation (or any non-heuristic summary).
            if _HEURISTIC_NOISE_RE.match(draft.summary):
                insights_skipped += 1
                continue
            insight_type = (
                "behavior_reinforcement"
                if _matches_pinned_behavior(cluster.signal_tokens, pinned_behavior_tokens)
                else "consolidation"
            )
            _persist_insight(
                conn,
                workspace_id=workspace_id,
                draft=draft,
                insight_type=insight_type,
            )
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
        insights_skipped=insights_skipped,
    )
