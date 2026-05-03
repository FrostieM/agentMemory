-- agent-memory-lite v1.9 — hygiene recurrence + sentinel trends.
--
-- maintenance_events grows three counters: how many times the same
-- (kind, target_type, target_id) was seen, and the timestamps of the
-- first and last occurrence. Hygiene findings flow through the
-- recurrence detector via dedup-and-increment, so a recurring "stale
-- candidate" doesn't generate a brand-new event every scan.
--
-- retrieval_sentinel_results captures the watchdog's per-case verdict
-- (pass/fail/error + match counts) so /memory/sentinel_trends can show
-- regression history rather than just the latest pass/fail snapshot.

ALTER TABLE maintenance_events ADD COLUMN recurrence_count INTEGER NOT NULL DEFAULT 1;
ALTER TABLE maintenance_events ADD COLUMN first_seen_at TEXT;
ALTER TABLE maintenance_events ADD COLUMN last_seen_at TEXT;

CREATE INDEX IF NOT EXISTS idx_maintenance_events_dedup
    ON maintenance_events(workspace_id, kind, target_type, target_id, status);

CREATE TABLE IF NOT EXISTS retrieval_sentinel_results (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    case_name TEXT NOT NULL,
    status TEXT NOT NULL,
    matched_count INTEGER NOT NULL DEFAULT 0,
    expected_count INTEGER NOT NULL DEFAULT 0,
    failures_json TEXT NOT NULL DEFAULT '[]',
    metrics_json TEXT NOT NULL DEFAULT '{}',
    run_id TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_retrieval_sentinel_results_workspace_case
    ON retrieval_sentinel_results(workspace_id, case_name, created_at);

CREATE INDEX IF NOT EXISTS idx_retrieval_sentinel_results_run
    ON retrieval_sentinel_results(run_id);
