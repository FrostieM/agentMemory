-- Plan steps — a task's plan as first-class, ordered step rows.
--
-- Until now a "plan" was two flat JSON string lists on the single
-- task_state row (current_plan_json / completed_steps_json), overwritten
-- in place on every update. That model has no per-step identity, status,
-- ordering, or history: you cannot mark one step active, link a step to
-- the decision it serves, or see how the plan evolved.
--
-- plan_steps makes each step its own row. (workspace_id, task_id) links a
-- step to its plan header (the task_state row); there is deliberately no
-- hard FK on that pair, so the header table can be renamed independently.
--
--   status              pending -> active -> done; blocked / skipped off-path
--   rank                REAL, so a step can be inserted between two others
--                        (midpoint) without renumbering the rest
--   parent_step_id       optional self-reference for sub-steps (flat by default)
--   supersedes_step_id   a re-planned step points at the one it replaced
--   valid_from/valid_to  bi-temporal: a step removed in a re-plan gets
--                        valid_to = now() and drops from the active plan
--                        view, while staying queryable for the trajectory
--
-- Idempotent — CREATE TABLE / INDEX IF NOT EXISTS, safe to re-run.

CREATE TABLE IF NOT EXISTS plan_steps (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    parent_step_id TEXT,
    rank REAL NOT NULL,
    title TEXT NOT NULL,
    body TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'active', 'done', 'blocked', 'skipped')),
    supersedes_step_id TEXT,
    source_episode_id TEXT,
    valid_from TEXT NOT NULL,
    valid_to TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(parent_step_id) REFERENCES plan_steps(id),
    FOREIGN KEY(supersedes_step_id) REFERENCES plan_steps(id),
    FOREIGN KEY(source_episode_id) REFERENCES episodes(id)
);

-- Primary access: every step of one plan, in rank order.
CREATE INDEX IF NOT EXISTS idx_plan_steps_task
    ON plan_steps(workspace_id, task_id, rank);

-- Find a plan's active / blocked steps without a table scan.
CREATE INDEX IF NOT EXISTS idx_plan_steps_status
    ON plan_steps(workspace_id, task_id, status);
