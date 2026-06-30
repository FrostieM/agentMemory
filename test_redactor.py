"""Test the redactor directly."""
from agent_memory_lite.redaction.redactor import redact

test_strings = [
    "sk-ant-abc123def456xyz789",
    "Use this API key: sk-ant-abc123def456xyz789",
    "my password is my-secret-password",
    "ghp_1234567890abcdefghijklmnop1234567890"
]

for s in test_strings:
    redacted = redact(s)
    print(f"Input: {s}")
    print(f"Output: {redacted.text}")
    print(f"Redaction kinds: {redacted.kinds_seen}")
    print()
