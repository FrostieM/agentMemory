"""Test that real secrets in audit_repo are not redacted."""
import sqlite3
import json
from pathlib import Path
from agent_memory_lite.db.connection import open_connection, close_connection
from agent_memory_lite.db.migrations import MIGRATION_DIR, apply_migrations
from agent_memory_lite.repositories.audit_repo import insert_audit

# Create a test database
tmp_db = Path("/tmp/test_audit_real.db")
if tmp_db.exists():
    tmp_db.unlink()

conn = open_connection(tmp_db)
apply_migrations(conn, MIGRATION_DIR)

# Real secret shapes
anthropic_key = "sk-ant-abcdefghijklmnopqrst"  # 20 chars after prefix
github_pat = "ghp_123456789012345678901234567890"  # 30 chars

# Insert an audit entry with real secrets
audit_entry = insert_audit(
    conn,
    workspace_id="test-ws",
    action="write",
    target_type="decision",
    target_id="dec_123",
    after={
        "decision_text": f"Using API key {anthropic_key}",
        "auth_token": github_pat,
        "metadata": {"config": anthropic_key}
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
    
    # Check for secrets
    db_str = json.dumps(after_from_db)
    secrets_found = []
    
    if anthropic_key in db_str:
        secrets_found.append("Anthropic API key (sk-ant-)")
    if github_pat in db_str:
        secrets_found.append("GitHub PAT (ghp_)")
    
    if secrets_found:
        print("[CRITICAL] Secrets found in cleartext in audit_repo after_json:")
        for secret in secrets_found:
            print(f"  - {secret}")
        print(f"\nAudit after_json:\n{json.dumps(after_from_db, indent=2)}")
    else:
        print("[OK] Secrets appear to be redacted in audit_repo")
else:
    print("No audit row found")
            
close_connection(conn)
if tmp_db.exists():
    tmp_db.unlink()
