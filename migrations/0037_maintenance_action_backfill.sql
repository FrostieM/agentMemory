-- v3.4 #6 hygiene action queue — backfill action_status for pre-existing rows.
--
-- Migration 0036 added action_status with DEFAULT 'open' so the schema
-- change was zero-risk. But rows that had already gone through the
-- substrate resolve path (status='resolved' or 'ignored') now show up
-- on the operator queue with action_status='open' — every historic
-- drift finding the watchdog had quietly resolved pollutes the queue.
--
-- This is a one-shot backfill: for any row whose substrate status is
-- terminal (resolved / ignored) but whose action_status is still the
-- default 'open', flip action_status to 'resolved' so the queue page
-- only surfaces actually-open work. The substrate columns are NOT
-- modified — this is purely the operator-side state catching up.

UPDATE maintenance_events
   SET action_status = 'resolved'
 WHERE action_status = 'open'
   AND status IN ('resolved', 'ignored');
