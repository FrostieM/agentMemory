-- agent-memory-lite v1.6 — cold-memory tracking.
--
-- `last_retrieved_at` is set by the retrieval pipeline whenever a row makes
-- it into the top-K of a /memory/get_context build. The cold scanner
-- (maintenance/cold_scanner.py) surfaces rows untouched past the configured
-- threshold as `cold_candidate` maintenance events — it NEVER archives
-- automatically. Pinned items (decisions/theories with pinned=1) are
-- excluded by the scanner so operator-anchored invariants don't get
-- flagged just for being durable.
--
-- All four columns are nullable / NULL-default. Existing rows behave as
-- "never retrieved" until the next get_context that surfaces them, which
-- means the cold scanner won't fire on them until the timestamp is set
-- and ages past the threshold — no false-positive flood on first run.

ALTER TABLE chunks ADD COLUMN last_retrieved_at TEXT;
ALTER TABLE decisions ADD COLUMN last_retrieved_at TEXT;
ALTER TABLE theories ADD COLUMN last_retrieved_at TEXT;
ALTER TABLE domain_concepts ADD COLUMN last_retrieved_at TEXT;
