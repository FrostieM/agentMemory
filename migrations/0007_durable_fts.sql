-- 0007: FTS5/BM25 relevance retrieval for the durable knowledge kinds
-- (decision / theory / behavior / skill / concept / insight).
--
-- These rows are the agent's highest-trust curated memory, but until now they
-- were searchable only via the interim LIKE token-overlap path in
-- storage/reader.search_kind (score = 0.5 * matched / tokens, no relevance
-- ranking, no stop-token down-weighting) -- whose own docstring flagged that "A
-- future plan adds FTS5 + rerank to these kinds". This is that plan (FTS5 only).
--
-- One app-managed FTS5 table mirroring chunks_fts: NO triggers -- every durable
-- write/edit syncs through fts/durable_fts.py (wired into write_canonical and
-- storage.writer.edit). `content` is the kind's free-text columns concatenated;
-- object_id/workspace_id/kind are UNINDEXED filter columns. Search MATCHes
-- content, filters by workspace_id+kind, ranks by bm25, then JOINs back to the
-- source table so the live archive/status filter + projection still apply.
--
-- The INSERTs below backfill the rows that already exist when this migration
-- first applies (no-ops on a fresh DB). Column lists mirror reader._KIND_FTS_COLUMNS.

CREATE VIRTUAL TABLE IF NOT EXISTS durable_fts USING fts5(
    object_id UNINDEXED,
    workspace_id UNINDEXED,
    kind UNINDEXED,
    content,
    tokenize = 'unicode61'
);

INSERT INTO durable_fts (object_id, workspace_id, kind, content)
SELECT id, workspace_id, 'decision',
       COALESCE(title, '') || ' ' || COALESCE(decision_text, '') || ' '
       || COALESCE(rationale, '') || ' ' || COALESCE(gist, '')
  FROM decisions;

INSERT INTO durable_fts (object_id, workspace_id, kind, content)
SELECT id, workspace_id, 'theory',
       COALESCE(title, '') || ' ' || COALESCE(claim, '') || ' '
       || COALESCE(mechanism, '') || ' ' || COALESCE(gist, '')
  FROM theories;

INSERT INTO durable_fts (object_id, workspace_id, kind, content)
SELECT id, workspace_id, 'behavior',
       COALESCE(name, '') || ' ' || COALESCE(rule, '') || ' '
       || COALESCE(rationale, '') || ' ' || COALESCE(rule_one_line, '')
  FROM behaviors;

INSERT INTO durable_fts (object_id, workspace_id, kind, content)
SELECT id, workspace_id, 'skill',
       COALESCE(name, '') || ' ' || COALESCE(summary, '') || ' '
       || COALESCE(when_to_use_short, '')
  FROM skills;

INSERT INTO durable_fts (object_id, workspace_id, kind, content)
SELECT id, workspace_id, 'concept',
       COALESCE(name, '') || ' ' || COALESCE(definition, '') || ' '
       || COALESCE(definition_one_line, '')
  FROM concepts;

INSERT INTO durable_fts (object_id, workspace_id, kind, content)
SELECT id, workspace_id, 'insight',
       COALESCE(summary, '') || ' ' || COALESCE(gist, '') || ' '
       || COALESCE(proposed_action, '')
  FROM insights;
