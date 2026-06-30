"""Test that secrets in the main table row are not redacted."""
import sqlite3
import json
from pathlib import Path
from agent_memory_lite.db.connection import open_connection, close_connection
from agent_memory_lite.db.migrations import MIGRATION_DIR, apply_migrations
from agent_memory_lite.storage.writer import write

# Create a test database
tmp_db = Path("/tmp/test_storage_table.db")
if tmp_db.exists():
    tmp_db.unlink()

conn = open_connection(tmp_db)
apply_migrations(conn, MIGRATION_DIR)

# Write an insight with secrets
result = write(
    conn,
    workspace_id="test-ws",
    kind="insight",
    payload={
        "summary": "API key is sk-ant-abc123def456xyz789",
        "insight_type": "pattern",
        "status": "active"
    },
    agent_id="test-agent"
)

# Check the actual table row
row = conn.execute(
    "SELECT * FROM insights WHERE workspace_id = 'test-ws'"
).fetchone()

if row:
    row_dict = dict(row)
    print(f"Insight row summary: {row_dict.get('summary')}")
    
    # Check for secrets
    full_str = json.dumps(row_dict)
    secrets_found = []
    
    if "sk-ant-abc123def456xyz789" in full_str:
        secrets_found.append("Anthropic API key (sk-ant-)")
    
    if secrets_found:
        print("\n[CRITICAL] Secrets found in cleartext in insights table:")
        for secret in secrets_found:
            print(f"  - {secret}")
    else:
        print("\n[OK] Secrets appear to be redacted in table")
else:
    print("No row found")
            
close_connection(conn)
if tmp_db.exists():
    tmp_db.unlink()
