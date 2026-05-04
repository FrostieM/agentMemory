#!/usr/bin/env bash
# Live memory carousel for /ui video recording.
# Open http://127.0.0.1:8765/ui, select workspace=demo, start screen
# recorder, run this script. Each step is followed by a 3s pause so
# the family bubble counter, spoke colours, and live trail update
# visibly before the next event.
#
# 15 steps, ~50 seconds total. Covers every action category:
# search, ingest, write_decision, pin, upsert (concept/skill),
# link_capability, archive (decision/episode), promote/reject
# candidate, update_task_state, explain.

set -e
cd "$(dirname "$0")/.."
# Windows-style path required by SQLite — Git Bash's /c/Users/... is unix-style
# and can't be opened on Windows.
DB="C:/Users/Osino/Desktop/work/agent-memory-lite/.agent_memory/demo/memory.db"

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
post /memory/get_context \
  '{"workspace_id":"demo","query":"RRF retrieval hybrid fusion","max_tokens":2000}' \
  "search RRF"
sleep 3

echo "=== 2/15: Ingest new episode (green spoke on Episodes) ==="
post /memory/ingest_episode \
  '{"workspace_id":"demo","raw_text":"Live demo: profiling scheduler lock contention under 100 concurrent get_context. Per-workspace lock holds steady, no double-spawn.","importance":0.7,"trust_level":"agent_observed"}' \
  "ingest_episode (live demo)"
sleep 3

echo "=== 3/15: Write new decision (green spoke on Decisions) ==="
post /memory/write_decision \
  '{"workspace_id":"demo","title":"Pre-push gate runs full crash test","decision_text":"Every git push to main runs scripts/crash_test --skip-llm. 26 phases / 122 assertions must pass before push allowed.","rationale":"Local fast feedback loop catches regressions before CI sees them.","confidence":0.9,"importance":0.85}' \
  "write_decision (pre-push gate)"
sleep 3

echo "=== 4/15: Pin that decision (yellow-green spoke) ==="
NEW_DEC=$(py "import sqlite3; c=sqlite3.connect('.agent_memory/demo/memory.db'); print(c.execute(\"SELECT id FROM decisions WHERE title='Pre-push gate runs full crash test' AND workspace_id='demo'\").fetchone()[0])")
post /memory/pin \
  "{\"workspace_id\":\"demo\",\"kind\":\"decision\",\"id\":\"$NEW_DEC\",\"pinned\":true}" \
  "pin (yellow-green)"
sleep 3

echo "=== 5/15: Search 'pre-push crash test' (new decision in RRF) ==="
post /memory/get_context \
  '{"workspace_id":"demo","query":"pre-push crash test gate","max_tokens":2000}' \
  "search pre-push"
sleep 3

echo "=== 6/15: Upsert concept (green spoke on Research) ==="
post /memory/upsert_concept \
  '{"workspace_id":"demo","name":"pre-push gate","kind":"gate","definition":"Local crash-test that runs before git push. 26 phases / 122 assertions must pass.","tags":["ci","gate"]}' \
  "upsert_concept (pre-push gate)"
sleep 3

echo "=== 7/15: Upsert skill (green spoke on Skills) ==="
post /memory/upsert_agent_skill \
  '{"workspace_id":"demo","name":"Crash test maintenance","summary":"Add new phase to scripts/crash_test, ensure assertions are deterministic + fast.","when_to_use":["When adding a new feature loop"],"confidence":0.85}' \
  "upsert_agent_skill"
sleep 3

echo "=== 8/15: Link skill -> new decision (capability_link) ==="
post /memory/link_capability \
  "{\"workspace_id\":\"demo\",\"target_type\":\"decision\",\"target_id\":\"$NEW_DEC\",\"capability_type\":\"skill\",\"capability_name\":\"Crash test maintenance\",\"relation\":\"method\",\"rationale\":\"Decision is enforced by this skill.\",\"strength\":0.95}" \
  "link_capability (skill->decision)"
sleep 3

echo "=== 9/15: Write a noisy decision we'll archive ==="
post /memory/write_decision \
  '{"workspace_id":"demo","title":"Try cloud LLM fallback","decision_text":"Consider OpenAI fallback when Ollama is unreachable.","rationale":"Speculative idea.","confidence":0.3,"importance":0.4}' \
  "write_decision (noisy)"
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

echo "=== 12/15: Accept pending insight_candidate (+0.7 EWMA) ==="
# Inject a fresh pending insight_candidate so the demo always exercises
# the accept path. Real candidates come from v1.8 reflective compaction;
# for demo determinism we seed one ourselves.
PENDING_IC=$(py "
import sqlite3, secrets, datetime
c = sqlite3.connect('.agent_memory/demo/memory.db')
cid = 'icand_demo_' + secrets.token_hex(6)
now = datetime.datetime.now(datetime.timezone.utc).isoformat()
c.execute('''INSERT INTO insight_candidates
  (id, workspace_id, insight_type, summary, proposed_action,
   confidence, status, source_episode_ids_json, tags_json, created_at, updated_at)
  VALUES (?, 'demo', 'lesson', 'Pre-push gate catches regressions before CI sees them.',
          'Lock the pre-push gate as a default convention.',
          0.78, 'pending', '[]', '[\"demo\",\"gate\"]', ?, ?)''', (cid, now, now))
c.commit()
print(cid)
")
if [ -n "$PENDING_IC" ]; then
  post "/memory/insight_candidates/$PENDING_IC/accept" \
    '{"workspace_id":"demo","decided_by":"demo-carousel"}' \
    "accept_insight_candidate"
fi
sleep 3

echo "=== 13/15: Reject pending decision_candidate ==="
# Inject a fresh pending decision_candidate so the demo always exercises
# the reject path. Real candidates come from v1.7 theory→decision bridge;
# for demo determinism we seed one ourselves.
PENDING_DC=$(py "
import sqlite3, secrets, datetime
c = sqlite3.connect('.agent_memory/demo/memory.db')
cid = 'dcand_demo_' + secrets.token_hex(6)
now = datetime.datetime.now(datetime.timezone.utc).isoformat()
c.execute('''INSERT INTO decision_candidates
  (id, workspace_id, theory_id, proposed_title, proposed_decision_text,
   proposed_rationale, evidence_count, evidence_strength, confidence,
   status, created_at, updated_at)
  VALUES (?, 'demo', 'th_demo_speculative',
          'Cloud LLM fallback for offline Ollama',
          'Switch to OpenAI when Ollama unreachable for >30s.',
          'Speculative — only 1 incident on record.',
          1, 0.4, 0.45, 'pending', ?, ?)''', (cid, now, now))
c.commit()
print(cid)
")
if [ -n "$PENDING_DC" ]; then
  post "/memory/decision_candidates/$PENDING_DC/reject" \
    '{"workspace_id":"demo","decided_by":"demo-carousel","reason":"Not enough independent evidence yet."}' \
    "reject_decision_candidate"
fi
sleep 3

echo "=== 14/15: Update task state (Tasks orb activates) ==="
post /memory/update_task_state \
  '{"workspace_id":"demo","task_id":"demo-readme-video","goal":"Record README video showing live observatory","status":"in_progress","current_plan":["Open UI on demo workspace","Record memory churn sequence","Export GIF"],"completed_steps":["Bootstrap demo workspace","Populate 16 kinds"],"next_action":"Stop recording and assemble"}' \
  "update_task_state"
sleep 3

echo "=== 15/15: Explain on 'RRF' (explainability view) ==="
post /memory/explain_context \
  '{"workspace_id":"demo","query":"RRF retrieval ranking","max_tokens":2000}' \
  "explain_context"
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
