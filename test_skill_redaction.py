"""Test that skill redaction doesn't work because payload is re-created."""
import sqlite3
import json
from pathlib import Path
from agent_memory_lite.db.connection import open_connection, close_connection
from agent_memory_lite.db.migrations import MIGRATION_DIR, apply_migrations
from agent_memory_lite.ingestion.canonical_writer import write_canonical

# Create a test database
tmp_db = Path("/tmp/test_skill.db")
if tmp_db.exists():
    tmp_db.unlink()

conn = open_connection(tmp_db)
apply_migrations(conn, MIGRATION_DIR)

# Write a skill with secrets via write_canonical
result = write_canonical(
    conn,
    workspace_id="test-ws",
    kind="skill",
    payload={
        "name": "test_skill",
        "summary": "Use this API key: sk-ant-abc123def456xyz789",
        "subtype": "skill",
        "status": "active"
    },
    agent_id="test-agent"
)

# Check the actual table row
row = conn.execute(
    "SELECT * FROM skills WHERE workspace_id = 'test-ws'"
).fetchone()

if row:
    row_dict = dict(row)
    print(f"Skill row summary: {row_dict.get('summary')}")
    
    # Check for secrets
    full_str = json.dumps(row_dict)
    secrets_found = []
    
    if "sk-ant-abc123def456xyz789" in full_str:
        secrets_found.append("Anthropic API key (sk-ant-)")
    elif "<<REDACTED:" in full_str:
        print("[OK] Secret appears to be redacted (contains <<REDACTED: marker)")
    
    if secrets_found:
        print("\n[CRITICAL] Secrets found in cleartext for skill:")
        for secret in secrets_found:
            print(f"  - {secret}")
    else:
        print("\n[OK] Secrets are properly redacted for skill")
else:
    print("No row found")
            
close_connection(conn)
if tmp_db.exists():
    tmp_db.unlink()
