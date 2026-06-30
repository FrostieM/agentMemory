"""Test the redact_freetext_fields function directly."""
from agent_memory_lite.redaction.payload import redact_freetext_fields

payload = {
    "summary": "Use this API key: sk-ant-abc123def456xyz789",
    "detail": "Token is ghp_1234567890abcdefghijklmnop1234567890",
    "metadata": {"password": "my-secret-password"}
}

redacted = redact_freetext_fields(payload)
print("Original:")
print(payload)
print("\nRedacted:")
print(redacted)

# Check if secrets are gone
import json
redacted_str = json.dumps(redacted)
if "sk-ant-abc123def456xyz789" in redacted_str:
    print("\n[FAIL] Secret NOT redacted!")
elif "<<REDACTED:" in redacted_str:
    print("\n[OK] Secret IS redacted")
else:
    print("\n[OK] Secret NOT found")
