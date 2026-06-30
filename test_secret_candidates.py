"""Test that secrets in candidate metadata are not redacted on write."""
import sqlite3
import json
from pathlib import Path
from agent_memory_lite.db.connection import open_connection, close_connection
from agent_memory_lite.db.migrations import MIGRATION_DIR, apply_migrations
from agent_memory_lite.models.candidates import MemoryCandidate, TemporalSpan
from agent_memory_lite.models.enums import MemoryCandidateKind, TrustLevel
from agent_memory_lite.ingestion.candidate_writer import write_memory_candidate

# Create a test database
tmp_db = Path("/tmp/test_secrets.db")
if tmp_db.exists():
    tmp_db.unlink()

conn = open_connection(tmp_db)
apply_migrations(conn, MIGRATION_DIR)

# Create a candidate with a secret in the metadata
candidate = MemoryCandidate(
    kind=MemoryCandidateKind.PROJECT_FACT,
    subject="test subject",
    predicate="test predicate",
    object="test object",
    evidence="test evidence",
    confidence=0.8,
    importance=0.7,
    trust_level=TrustLevel.AGENT_INFERRED,
    temporal=TemporalSpan(
        observed_at="2026-06-30T00:00:00Z",
        valid_from="2026-06-30T00:00:00Z",
        valid_to=None,
    ),
    metadata={
        "api_key": "sk-ant-abc123def456xyz789",
        "config": {
            "password": "my-secret-password",
            "nested": {"token": "ghp_1234567890abcdefghijklmnop1234567890"},
        }
    }
)

# Write the candidate
try:
    stored = write_memory_candidate(conn, workspace_id="test-ws", candidate=candidate)
    print(f"Candidate ID: {stored.id}")
    print(f"Stored metadata: {json.dumps(stored.metadata, indent=2)}")

    # Check the database directly
    row = conn.execute("SELECT metadata_json FROM candidates WHERE id = ?", (stored.id,)).fetchone()
    if row:
        metadata_from_db = json.loads(row[0])
        print(f"\nMetadata from DB:\n{json.dumps(metadata_from_db, indent=2)}")
        
        # Check for secrets
        db_str = json.dumps(metadata_from_db)
        secrets_found = []
        
        if "sk-ant-abc123def456xyz789" in db_str:
            secrets_found.append("Anthropic API key (sk-ant-)")
        if "my-secret-password" in db_str:
            secrets_found.append("Password")
        if "ghp_1234567890abcdefghijklmnop1234567890" in db_str:
            secrets_found.append("GitHub PAT (ghp_)")
        
        if secrets_found:
            print("\n[CRITICAL] Secrets found in cleartext in database:")
            for secret in secrets_found:
                print(f"  - {secret}")
        else:
            print("\n[OK] Secrets appear to be redacted")
            
finally:
    close_connection(conn)
    if tmp_db.exists():
        tmp_db.unlink()
