"""Test that secrets in storage.writer audit are not redacted."""
import sqlite3
import json
from pathlib import Path
from agent_memory_lite.db.connection import open_connection, close_connection
from agent_memory_lite.db.migrations import MIGRATION_DIR, apply_migrations
from agent_memory_lite.storage.writer import write

# Create a test database
tmp_db = Path("/tmp/test_storage_audit.db")
if tmp_db.exists():
    tmp_db.unlink()

conn = open_connection(tmp_db)
apply_migrations(conn, MIGRATION_DIR)

# Write an insight with secrets - this should be redacted by write_canonical in normal flow
# but let's test the direct storage.writer path
result = write(
    conn,
    workspace_id="test-ws",
    kind="insight",
    payload={
        "summary": "API key is sk-ant-abc123def456xyz789",
        "detail": "Token is ghp_1234567890abcdefghijklmnop1234567890",
        "insight_type": "pattern",
        "status": "active"
    },
    agent_id="test-agent"
)

# Check audit log
row = conn.execute(
    "SELECT after_json FROM audit_log WHERE target_type = 'insight' ORDER BY created_at DESC LIMIT 1"
).fetchone()

if row:
    after_from_db = json.loads(row[0])
    print(f"Audit after_json from storage.writer:\n{json.dumps(after_from_db, indent=2)}")
    
    # Check for secrets
    db_str = json.dumps(after_from_db)
    secrets_found = []
    
    if "sk-ant-abc123def456xyz789" in db_str:
        secrets_found.append("Anthropic API key (sk-ant-)")
    if "ghp_1234567890abcdefghijklmnop1234567890" in db_str:
        secrets_found.append("GitHub PAT (ghp_)")
    
    if secrets_found:
        print("\n[CRITICAL] Secrets found in cleartext in storage.writer audit:")
        for secret in secrets_found:
            print(f"  - {secret}")
    else:
        print("\n[OK] Secrets appear to be redacted in storage.writer audit")
else:
    print("No audit row found")
            
close_connection(conn)
if tmp_db.exists():
    tmp_db.unlink()
