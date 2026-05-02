"""Hypothesis-driven invariants for redaction.

The two properties we lean on:
1. Idempotence — running redact twice is the same as once.
2. Secret coverage — for any text built around a known shape, the secret literal
   does not appear in the redacted output.
"""

from __future__ import annotations

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from agent_memory_lite.redaction import redact


@given(text=st.text(max_size=2000))
@settings(max_examples=200, deadline=None, suppress_health_check=[HealthCheck.too_slow])
def test_redact_is_idempotent(text: str) -> None:
    once = redact(text).text
    twice = redact(once).text
    assert twice == once


@given(
    body=st.text(alphabet="abcdefghijklmnopqrstuvwxyzABCDEF0123456789", min_size=32, max_size=64),
    prefix=st.text(max_size=40),
    suffix=st.text(max_size=40),
)
@settings(max_examples=100, deadline=None)
def test_openai_shape_is_always_redacted(body: str, prefix: str, suffix: str) -> None:
    secret = f"sk-{body}"
    text = f"{prefix} {secret} {suffix}"
    out = redact(text)
    assert secret not in out.text


@given(
    value=st.text(
        alphabet="abcdefghijklmnopqrstuvwxyz0123456789!@#$%^&*",
        min_size=4,
        max_size=64,
    ),
)
@settings(max_examples=100, deadline=None)
def test_password_value_is_always_redacted(value: str) -> None:
    text = f"config: password={value} done"
    out = redact(text)
    assert f"password={value}" not in out.text


@given(text=st.text(max_size=500))
@settings(max_examples=100, deadline=None)
def test_kinds_seen_is_subset_of_known_kinds(text: str) -> None:
    out = redact(text)
    # Any reported kind must appear at least once in the output as a redaction marker.
    for kind in out.kinds_seen:
        assert f"<<REDACTED:{kind.upper()}>>" in out.text
