-- agent-memory-lite v1.7 — theory -> decision-candidate bridge.
--
-- When a theory transitions to status='validated' with at least
-- MEMORY_THEORY_BRIDGE_MIN_EVIDENCE supporting evidence rows, the
-- promotion bridge writes a row here. This table NEVER drives the
-- ``decisions`` table directly; the operator must explicitly promote
-- a candidate through the /memory/decision_candidates/{id}/promote
-- endpoint, which is the only path that creates an actual decision row.
-- This preserves the trust-gate invariant: validated research surfaces
-- as a proposal, never as a fait accompli.

CREATE TABLE IF NOT EXISTS decision_candidates (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    theory_id TEXT NOT NULL,
    proposed_title TEXT NOT NULL,
    proposed_decision_text TEXT NOT NULL,
    proposed_rationale TEXT,
    evidence_count INTEGER NOT NULL DEFAULT 0,
    evidence_strength REAL NOT NULL DEFAULT 0.0,
    confidence REAL NOT NULL DEFAULT 0.7,
    status TEXT NOT NULL DEFAULT 'pending',
    promoted_decision_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    decided_at TEXT,
    decided_by TEXT,
    FOREIGN KEY(theory_id) REFERENCES theories(id),
    FOREIGN KEY(promoted_decision_id) REFERENCES decisions(id)
);

CREATE INDEX IF NOT EXISTS idx_decision_candidates_workspace_status
    ON decision_candidates(workspace_id, status, updated_at);

CREATE INDEX IF NOT EXISTS idx_decision_candidates_theory
    ON decision_candidates(theory_id);

-- Dedup index: at most one pending candidate per (theory_id, status='pending').
-- Enforced via partial unique index so a previously rejected candidate doesn't
-- block a fresh proposal after the theory regathers evidence.
CREATE UNIQUE INDEX IF NOT EXISTS uniq_decision_candidates_open_per_theory
    ON decision_candidates(theory_id) WHERE status = 'pending';
