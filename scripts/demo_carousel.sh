#!/usr/bin/env bash
# Live memory carousel for /ui video recording.
# Open http://127.0.0.1:8765/ui, select workspace=demo, start screen
# recorder, run this script. Each step is followed by a 3s pause so
# the family bubble counter, spoke colours, and live trail update
# visibly before the next event.
#
# 15 steps, ~50 seconds total. Covers every action category:
# search, write episode/decision, pin, write concept/skill,
# capability suggestions, archive (decision/episode), promote/reject
# candidate, write task, brief.

set -e
cd "$(dirname "$0")/.."
# Windows-style path required by SQLite; derive it from the current checkout so
# the demo stays portable across operator machines.
DB="$(.venv/Scripts/python -c 'from pathlib import Path; print((Path.cwd()/".agent_memory/demo/memory.db").resolve().as_posix())')"

post() {
  curl -s -X POST "http://127.0.0.1:8765$1" \
    -H "Content-Type: application/json" \
    -H "X-Memory-DB-Path: $DB" \
    -d "$2" -o NUL -w "  [%{http_code}] %{time_total}s  $3\n"
}

py() { .venv/Scripts/python -c "$1"; }

echo "=== START — switch to UI tab now (5s grace) ==="
sleep 5

echo ""
echo "=== 1/15: Search 'RRF retrieval' (RECALL→FUSE→ANSWER cycle) ==="
post /memory/search \
  '{"workspace_id":"demo","query":"RRF retrieval hybrid fusion","limit":8}' \
  "search RRF"
sleep 3

echo "=== 2/15: Ingest new episode (green spoke on Episodes) ==="
post /memory/ingest_episode \
  '{"workspace_id":"demo","raw_text":"Live demo: profiling scheduler lock contention under 100 concurrent memory_brief calls. Per-workspace lock holds steady, no double-spawn.","importance":0.7,"trust_level":"agent_observed"}' \
  "ingest_episode (live demo)"
sleep 3

echo "=== 3/15: Write new decision (green spoke on Decisions) ==="
post /memory/write \
  '{"workspace_id":"demo","kind":"decision","payload":{"title":"Pre-push gate runs full crash test","decision_text":"Every git push to main runs scripts/crash_test --skip-llm. 26 phases / 122 assertions must pass before push allowed.","rationale":"Local fast feedback loop catches regressions before CI sees them.","confidence":0.9,"importance":0.85}}' \
  "memory_write decision (pre-push gate)"
sleep 3

echo "=== 4/15: Pin that decision (yellow-green spoke) ==="
NEW_DEC=$(py "import sqlite3; c=sqlite3.connect('.agent_memory/demo/memory.db'); print(c.execute(\"SELECT id FROM decisions WHERE title='Pre-push gate runs full crash test' AND workspace_id='demo'\").fetchone()[0])")
post /memory/pin \
  "{\"workspace_id\":\"demo\",\"kind\":\"decision\",\"id\":\"$NEW_DEC\",\"pinned\":true}" \
  "pin (yellow-green)"
sleep 3

echo "=== 5/15: Search 'pre-push crash test' (new decision in RRF) ==="
post /memory/search \
  '{"workspace_id":"demo","query":"pre-push crash test gate","limit":8}' \
  "search pre-push"
sleep 3

echo "=== 6/15: Upsert concept (green spoke on Research) ==="
post /memory/upsert_concept \
  '{"workspace_id":"demo","name":"pre-push gate","kind":"gate","definition":"Local crash-test that runs before git push. 26 phases / 122 assertions must pass.","tags":["ci","gate"]}' \
  "upsert_concept (pre-push gate)"
sleep 3

echo "=== 7/15: Write skill (green spoke on Skills) ==="
post /memory/write \
  '{"workspace_id":"demo","kind":"skill","payload":{"name":"Crash test maintenance","summary":"Add new phase to scripts/crash_test, ensure assertions are deterministic + fast.","body_md":"Add new phase to scripts/crash_test, ensure assertions are deterministic + fast.","trigger":"When adding a new feature loop","confidence":0.85}}' \
  "memory_write skill"
sleep 3

echo "=== 8/15: Write theory to show capability suggestions ==="
post /memory/write \
  '{"workspace_id":"demo","kind":"theory","payload":{"title":"Crash test maintenance catches regressions","claim":"Crash test maintenance should catch endpoint regressions before release.","status":"testing","confidence":0.6}}' \
  "memory_write theory + suggestions"
sleep 3

echo "=== 9/15: Write a noisy decision we'll archive ==="
post /memory/write \
  '{"workspace_id":"demo","kind":"decision","payload":{"title":"Try cloud LLM fallback","decision_text":"Consider OpenAI fallback when Ollama is unreachable.","rationale":"Speculative idea.","confidence":0.3,"importance":0.4}}' \
  "memory_write decision (noisy)"
sleep 3

echo "=== 10/15: Archive noisy decision (red-orange + -1.0 EWMA) ==="
NOISY_DEC=$(py "import sqlite3; c=sqlite3.connect('.agent_memory/demo/memory.db'); print(c.execute(\"SELECT id FROM decisions WHERE title='Try cloud LLM fallback' AND workspace_id='demo'\").fetchone()[0])")
post /memory/archive \
  "{\"workspace_id\":\"demo\",\"kind\":\"decision\",\"id\":\"$NOISY_DEC\",\"archive\":true}" \
  "archive (noisy decision)"
sleep 3

echo "=== 11/15: Archive old episode (red-orange on Episodes) ==="
OLD_EP=$(py "import sqlite3; c=sqlite3.connect('.agent_memory/demo/memory.db'); print(c.execute(\"SELECT id FROM episodes WHERE workspace_id='demo' ORDER BY created_at LIMIT 1\").fetchone()[0])")
post /memory/archive \
  "{\"workspace_id\":\"demo\",\"kind\":\"episode\",\"id\":\"$OLD_EP\",\"archive\":true}" \
  "archive (old episode)"
sleep 3

echo "=== 12/15: Seed insight review candidate ==="
# Inject a fresh canonical insight candidate so the demo always exercises
# the review queue. Real candidates come from reflective compaction.
PENDING_IC=$(py "
import json, sqlite3, secrets, datetime
c = sqlite3.connect('.agent_memory/demo/memory.db')
cid = 'cand_demo_' + secrets.token_hex(6)
now = datetime.datetime.now(datetime.timezone.utc).isoformat()
c.execute('''INSERT INTO candidates
  (id, workspace_id, kind, subject, predicate, object, evidence,
   confidence, importance, trust_level, temporal_json, write_targets_json,
   metadata_json, source_episode_id, status, created_at, updated_at)
  VALUES (?, 'demo', 'insight',
          'Pre-push gate catches regressions before CI sees them.',
          'should_promote_to_insight',
          'Lock the pre-push gate as a default convention.',
          'Pre-push gate catches regressions before CI sees them.',
          0.78, 0.78, 'agent_inferred', '{}', '[\"insight\"]',
          ?, NULL, 'new', ?, ?)''',
          (cid, json.dumps({'insight_type':'lesson'}), now, now))
c.commit()
print(cid)
")
if [ -n "$PENDING_IC" ]; then
  post "/memory/review_queue" \
    '{"workspace_id":"demo","limit_per_kind":10}' \
    "review_queue insight candidate"
fi
sleep 3

echo "=== 13/15: Seed decision review candidate ==="
# Inject a fresh canonical decision candidate so the demo always exercises
# the review queue. Real candidates come from the theory bridge.
PENDING_DC=$(py "
import json, sqlite3, secrets, datetime
c = sqlite3.connect('.agent_memory/demo/memory.db')
cid = 'cand_demo_' + secrets.token_hex(6)
now = datetime.datetime.now(datetime.timezone.utc).isoformat()
c.execute('''INSERT INTO candidates
  (id, workspace_id, kind, subject, predicate, object, evidence,
   confidence, importance, trust_level, temporal_json, write_targets_json,
   metadata_json, source_episode_id, status, created_at, updated_at)
  VALUES (?, 'demo', 'decision',
          'Cloud LLM fallback for offline Ollama',
          'should_promote_to_decision',
          'Switch to OpenAI when Ollama unreachable for >30s.',
          'Speculative; only 1 incident on record.',
          0.45, 0.45, 'agent_inferred', '{}', '[\"decision\"]',
          ?, NULL, 'new', ?, ?)''',
          (cid, json.dumps({'theory_id':'th_demo_speculative'}), now, now))
c.commit()
print(cid)
")
if [ -n "$PENDING_DC" ]; then
  post "/memory/review_queue" \
    '{"workspace_id":"demo","limit_per_kind":10}' \
    "review_queue decision candidate"
fi
sleep 3

echo "=== 14/15: Update task state (Tasks orb activates) ==="
post /memory/write \
  '{"workspace_id":"demo","kind":"task","payload":{"task_id":"demo-readme-video","goal":"Record README video showing live observatory","status":"in_progress","current_plan":["Open UI on demo workspace","Record memory churn sequence","Export GIF"],"completed_steps":["Bootstrap demo workspace","Populate 16 kinds"],"next_action":"Stop recording and assemble"}}' \
  "memory_write task"
sleep 3

echo "=== 15/15: Brief on 'RRF' (compact v3 read surface) ==="
curl -s "http://127.0.0.1:8765/memory/brief?workspace_id=demo&task=RRF%20retrieval%20ranking&max_tokens=800" \
  -H "X-Memory-DB-Path: $DB" \
  -o NUL -w "  [%{http_code}] %{time_total}s  memory_brief\n"
sleep 2

echo ""
echo "=== END of carousel ==="
.venv/Scripts/python <<'PYEOF'
import sqlite3
c = sqlite3.connect('.agent_memory/demo/memory.db')
def n(sql, *args):
    return c.execute(sql, args).fetchone()[0]
ws = ('demo',)
ep_active = n("SELECT COUNT(*) FROM episodes WHERE workspace_id=? AND COALESCE(is_archived,0)=0", *ws)
ep_arch   = n("SELECT COUNT(*) FROM episodes WHERE workspace_id=? AND COALESCE(is_archived,0)=1", *ws)
dec_active = n("SELECT COUNT(*) FROM decisions WHERE workspace_id=? AND status='active'", *ws)
dec_pin    = n("SELECT COUNT(*) FROM decisions WHERE workspace_id=? AND COALESCE(pinned,0)=1", *ws)
dec_sup    = n("SELECT COUNT(*) FROM decisions WHERE workspace_id=? AND status='superseded'", *ws)
fb         = n("SELECT COUNT(*) FROM memory_usage_feedback WHERE workspace_id=?", *ws)
audit      = n("SELECT COUNT(*) FROM audit_log WHERE workspace_id=?", *ws)
print(f"  episodes:   {ep_active} active, {ep_arch} archived")
print(f"  decisions:  {dec_active} active, {dec_pin} pinned, {dec_sup} superseded")
print(f"  feedback:   {fb} rows")
print(f"  audit_log:  {audit} entries")
PYEOF
