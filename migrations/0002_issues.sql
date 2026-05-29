-- 0002_issues.sql
-- First-class issue / debt / risk store (release-readiness plan step 9.2).
--
-- A durable, queryable record of bugs, tech debt, risks, and errors carrying
-- severity, lifecycle status, evidence links, and a retest command. This is
-- distinct from plan_steps (execution backlog) and maintenance_events (infra
-- events): it is the engineering-debt knowledge surface that feeds the brief
-- (step 9.5) and the scorecard (step 8), and is auto-populated from audits +
-- error-fix episodes (step 9.4).
--
-- Additive forward migration: it adds one table + two indexes and does not
-- touch the canonical v3 baseline, so validate_canonical_baseline still
-- passes (issues is neither a canonical sentinel table nor a legacy v2 table).

CREATE TABLE IF NOT EXISTS issues (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    title TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT 'bug',       -- bug | tech_debt | risk | error
    severity TEXT NOT NULL DEFAULT 'minor',     -- critical | major | minor
    status TEXT NOT NULL DEFAULT 'open',        -- open | in_progress | fixed | wontfix | accepted
    gist TEXT,
    description TEXT,
    evidence_json TEXT NOT NULL DEFAULT '[]',   -- file paths, episode/decision ids, line refs
    retest_command TEXT,
    source TEXT NOT NULL DEFAULT 'agent',        -- agent | audit | operator
    signature TEXT,                              -- dedup key (normalized title + category)
    discovered_at TEXT,
    resolved_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    outcome_score REAL NOT NULL DEFAULT 0.0
);

CREATE INDEX IF NOT EXISTS idx_issues_workspace_status
    ON issues(workspace_id, status, severity);

CREATE INDEX IF NOT EXISTS idx_issues_signature
    ON issues(workspace_id, signature);
