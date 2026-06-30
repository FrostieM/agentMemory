"""Test that real secrets in candidate metadata are not redacted on write."""
import sqlite3
import json
from pathlib import Path
from agent_memory_lite.db.connection import open_connection, close_connection
from agent_memory_lite.db.migrations import MIGRATION_DIR, apply_migrations
from agent_memory_lite.models.candidates import MemoryCandidate, TemporalSpan
from agent_memory_lite.models.enums import MemoryCandidateKind, TrustLevel
from agent_memory_lite.ingestion.candidate_writer import write_memory_candidate

# Create a test database
tmp_db = Path("/tmp/test_secrets_real.db")
if tmp_db.exists():
    tmp_db.unlink()

conn = open_connection(tmp_db)
apply_migrations(conn, MIGRATION_DIR)

# Real secret shapes
anthropic_key = "sk-ant-abcdefghijklmnopqrst"  # 20 chars after prefix
github_pat = "ghp_123456789012345678901234567890"  # 30 chars
password = "my-super-secret-password"

# Create a candidate with real secrets in the metadata
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
        "api_key": anthropic_key,
        "config": {
            "password": password,
            "nested": {"token": github_pat},
        }
    }
)

# Write the candidate
try:
    stored = write_memory_candidate(conn, workspace_id="test-ws", candidate=candidate)
    print(f"Candidate ID: {stored.id}")

    # Check the database directly
    row = conn.execute("SELECT metadata_json FROM candidates WHERE id = ?", (stored.id,)).fetchone()
    if row:
        metadata_from_db = json.loads(row[0])
        
        # Check for secrets
        db_str = json.dumps(metadata_from_db)
        secrets_found = []
        
        if anthropic_key in db_str:
            secrets_found.append("Anthropic API key (sk-ant-)")
        if password in db_str:
            secrets_found.append("Password")
        if github_pat in db_str:
            secrets_found.append("GitHub PAT (ghp_)")
        
        if secrets_found:
            print("\n[CRITICAL] Secrets found in cleartext in database:")
            for secret in secrets_found:
                print(f"  - {secret}")
            print(f"\nMetadata from DB:\n{json.dumps(metadata_from_db, indent=2)}")
        else:
            print("\n[OK] Secrets appear to be redacted")
            
finally:
    close_connection(conn)
    if tmp_db.exists():
        tmp_db.unlink()
