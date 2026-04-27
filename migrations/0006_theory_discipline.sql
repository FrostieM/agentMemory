-- Theory discipline fields.
--
-- Theories need explicit validation criteria and links to decisions that depend
-- on them. Evidence summary columns make the current state visible without
-- forcing every caller to inspect all evidence rows.

ALTER TABLE theories ADD COLUMN validation_criteria_json TEXT NOT NULL DEFAULT '[]';
ALTER TABLE theories ADD COLUMN dependent_decision_ids_json TEXT NOT NULL DEFAULT '[]';
ALTER TABLE theories ADD COLUMN evidence_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE theories ADD COLUMN evidence_strength REAL NOT NULL DEFAULT 0.0;

UPDATE theories
SET evidence_count = (
        SELECT COUNT(*)
        FROM theory_evidence
        WHERE theory_evidence.theory_id = theories.id
    ),
    evidence_strength = COALESCE((
        SELECT SUM(
            CASE theory_evidence.kind
                WHEN 'supporting' THEN theory_evidence.confidence
                WHEN 'refuting' THEN -theory_evidence.confidence
                WHEN 'mixed' THEN -0.5 * theory_evidence.confidence
                ELSE 0
            END
        )
        FROM theory_evidence
        WHERE theory_evidence.theory_id = theories.id
    ), 0.0);
