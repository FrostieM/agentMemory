"""Test the anthropic_key pattern directly."""
import re

pattern = re.compile(r"sk-ant-[A-Za-z0-9_-]{20,}")

test_key = "sk-ant-abc123def456xyz789"
print(f"Pattern: {pattern.pattern}")
print(f"Test key: {test_key}")
print(f"Length after prefix: {len(test_key) - 7}")  # sk-ant- is 7 chars

match = pattern.search(test_key)
if match:
    print(f"Match found: {match.group()}")
else:
    print("No match found")

# Try in a sentence
sentence = "Use this API key: sk-ant-abc123def456xyz789"
match = pattern.search(sentence)
if match:
    print(f"Match in sentence: {match.group()}")
else:
    print("No match in sentence")
