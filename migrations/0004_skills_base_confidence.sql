-- 0004: capture a stable base-confidence anchor for the capability maturity
-- curve (release task #121).
--
-- `skills.confidence` becomes the live, outcome-adjusted value; the new
-- `base_confidence` column preserves the operator/extractor confidence supplied
-- at upsert time so `capability.maturity.confidence_from_maturity` can decay
-- back toward it when a capability falls idle, instead of drifting forever
-- toward the observed success rate.
--
-- Forward-only + additive: existing rows backfill `base_confidence` from their
-- current `confidence`, which is still the pristine operator value because the
-- maturity curve had no callers before this migration. Going forward the
-- capability upsert repositories set `base_confidence` at write time (and
-- re-anchor it when an operator re-upserts with a new confidence); the brain
-- pass keeps a defensive capture-when-NULL fallback for any row that slips
-- through with no anchor.
ALTER TABLE skills ADD COLUMN base_confidence REAL;
UPDATE skills SET base_confidence = confidence WHERE base_confidence IS NULL;
