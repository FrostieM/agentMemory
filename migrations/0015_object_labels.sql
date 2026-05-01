-- Optional short visual labels for memory objects.
--
-- Each chunk and episode already has a technical id (chk_xxx / ep_xxx) and
-- the actual content (chunks.text, episodes.raw_text). The label column is
-- a small free-text hint shown in the UI ("agentLight bootstrap",
-- "Hook 400 fix") so a human or live observatory dashboard can read at a
-- glance what an object is without opening it.
--
-- Labels carry NO retrieval weight: FTS, vector search, scoring, ranking,
-- and budget allocation continue to use text/raw_text only. This is purely
-- a display-time annotation. The column is nullable; clients that don't
-- supply a label fall back to a derived snippet, exactly like before.

ALTER TABLE chunks ADD COLUMN label TEXT;
ALTER TABLE episodes ADD COLUMN label TEXT;
