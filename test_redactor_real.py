"""Test the redactor with proper length secrets."""
from agent_memory_lite.redaction.redactor import redact

# Real Anthropic key format (sk-ant- + 20+ chars)
test_key = "sk-ant-abcdefghijklmnopqrst"  # Exactly 20 chars after prefix
print(f"Test key length: {len(test_key)}, after prefix: {len(test_key) - 7}")

test_strings = [
    test_key,
    f"Use this API key: {test_key}",
    "ghp_123456789012345678901234567890",  # Valid GitHub PAT (30+ chars)
]

for s in test_strings:
    redacted = redact(s)
    print(f"Input: {s}")
    print(f"Output: {redacted.text}")
    print(f"Redaction kinds: {redacted.kinds_seen}")
    print()
