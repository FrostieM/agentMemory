"""Test that secrets in audit log after_json are not redacted on write."""
import sqlite3
import json
from pathlib import Path
from agent_memory_lite.db.connection import open_connection, close_connection
from agent_memory_lite.db.migrations import MIGRATION_DIR, apply_migrations
from agent_memory_lite.repositories.audit_repo import insert_audit

# Create a test database
tmp_db = Path("/tmp/test_audit_secrets.db")
if tmp_db.exists():
    tmp_db.unlink()

conn = open_connection(tmp_db)
apply_migrations(conn, MIGRATION_DIR)

# Insert an audit entry with secrets in the after payload
audit_entry = insert_audit(
    conn,
    workspace_id="test-ws",
    action="write",
    target_type="decision",
    target_id="dec_123",
    after={
        "decision_text": "API key is sk-ant-abc123def456xyz789",
        "auth_token": "ghp_1234567890abcdefghijklmnop1234567890",
        "metadata": {"password": "my-secret-password"}
    }
)

conn.commit()

# Check the database directly
row = conn.execute(
    "SELECT after_json FROM audit_log WHERE id = ?", 
    (audit_entry.id,)
).fetchone()

if row:
    after_from_db = json.loads(row[0])
    print(f"Audit after_json from DB:\n{json.dumps(after_from_db, indent=2)}")
    
    # Check for secrets
    db_str = json.dumps(after_from_db)
    secrets_found = []
    
    if "sk-ant-abc123def456xyz789" in db_str:
        secrets_found.append("Anthropic API key (sk-ant-)")
    if "my-secret-password" in db_str:
        secrets_found.append("Password")
    if "ghp_1234567890abcdefghijklmnop1234567890" in db_str:
        secrets_found.append("GitHub PAT (ghp_)")
    
    if secrets_found:
        print("\n[CRITICAL] Secrets found in cleartext in audit_log:")
        for secret in secrets_found:
            print(f"  - {secret}")
    else:
        print("\n[OK] Secrets appear to be redacted")
else:
    print("No audit row found")
            
close_connection(conn)
if tmp_db.exists():
    tmp_db.unlink()
