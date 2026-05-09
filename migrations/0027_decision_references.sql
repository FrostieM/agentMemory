-- 1.3.0: declarative file-path / symbol references on decisions.
-- Pre-1.3.0 the only way to know "what files does this decision affect"
-- was substring search in decision_text + rationale, which mixed false
-- positives. The references_json column stores an explicit list of
-- file paths or `path:symbol` markers. The /memory/explain_diff endpoint
-- and the /memory/cold_decisions report use this to decide whether a
-- diff or refactor touches a decision's territory without keyword guessing.
ALTER TABLE decisions ADD COLUMN references_json TEXT;
