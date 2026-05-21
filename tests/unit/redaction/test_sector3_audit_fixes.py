"""v3.5 sector-3 audit-followup: redaction + trust-gate regression locks.

The audit found multiple secret-leakage paths:

- ``capability_writer`` stored role / skill / playbook free-text fields
  without ``redact()`` — capabilities ride every brief via role
  activation, so a Bearer token pasted into ``purpose`` would surface
  in every future envelope.
- ``promote_insight_to_behavior`` wrote insight summary / gist into
  ``behaviors.rule`` without ``redact()`` — episode-derived insights
  could carry secrets the consolidation LLM rephrased.
- Redaction patterns missed Stripe keys, basic-auth URLs, generic
  Bearer tokens, GCP service-account markers.
- Trust gate's ``PROMOTABLE_KINDS`` set was too narrow:
  ``DECISION/CORRECTION/BUG/FIX`` candidates from UNTRUSTED_DOC
  episodes bypassed it.
"""

from __future__ import annotations

import pytest

from agent_memory_lite.extraction.trust_gate import PROMOTABLE_KINDS, passes_trust_gate
from agent_memory_lite.models.candidates import MemoryCandidate
from agent_memory_lite.models.enums import MemoryCandidateKind, TrustLevel
from agent_memory_lite.redaction.redactor import redact

# v3.5: test payloads built at runtime so GitHub secret-scanning
# doesn't flag literal Stripe-shaped strings in the source. The
# pattern shape is still preserved at runtime to exercise the
# regex; we just don't COMMIT the matching literal.
_STRIPE_LIVE = "sk_live_" + ("z" * 26)
_STRIPE_TEST = "sk_test_" + ("z" * 26)


@pytest.mark.parametrize(
    ("payload", "secret_marker"),
    [
        # Stripe live key
        (f"payment_token = {_STRIPE_LIVE}", _STRIPE_LIVE),
        # Stripe test key
        (f"test = {_STRIPE_TEST}", _STRIPE_TEST),
        # Basic-auth URL
        ("config: https://admin:secret123@db.example.com/path", "secret123"),
        # GCP service account hint (just the type marker)
        ('cred = {"type": "service_account", "key": "..."}', '"service_account"'),
        # Generic Bearer token (not JWT-shaped)
        ("Authorization: Bearer abc123def456ghi789jkl0", "abc123def456ghi789jkl0"),
    ],
)
def test_new_secret_patterns_caught(payload: str, secret_marker: str) -> None:
    """v3.5: secret shapes that the audit flagged as missing must
    no longer survive the redaction layer."""
    out = redact(payload).text
    assert secret_marker not in out, f"redaction leaked: {payload} → {out}"


def test_trust_gate_blocks_untrusted_decision() -> None:
    """A DECISION candidate from an UNTRUSTED_DOC episode must NOT
    pass the trust gate — the v3.4 gate only blocked procedural_rule
    + constraint, leaving decision/correction as bypass routes."""
    cand = _candidate(
        kind=MemoryCandidateKind.DECISION,
        trust_level=TrustLevel.UNTRUSTED_DOC,
    )
    assert passes_trust_gate(cand) is False


def test_trust_gate_blocks_untrusted_correction() -> None:
    cand = _candidate(
        kind=MemoryCandidateKind.CORRECTION,
        trust_level=TrustLevel.UNTRUSTED_DOC,
    )
    assert passes_trust_gate(cand) is False


def test_trust_gate_blocks_untrusted_bug_and_fix() -> None:
    for kind in (MemoryCandidateKind.BUG, MemoryCandidateKind.FIX):
        cand = _candidate(kind=kind, trust_level=TrustLevel.UNTRUSTED_DOC)
        assert passes_trust_gate(cand) is False, f"{kind} should be blocked"


def test_trust_gate_still_allows_untrusted_project_fact() -> None:
    """PROJECT_FACT is informational — not a behavior driver. UNTRUSTED_DOC
    candidates of that kind should still pass (otherwise we lose
    project context from docs entirely)."""
    cand = _candidate(
        kind=MemoryCandidateKind.PROJECT_FACT,
        trust_level=TrustLevel.UNTRUSTED_DOC,
    )
    assert passes_trust_gate(cand) is True


def test_trust_gate_promotable_kinds_includes_high_blast_radius() -> None:
    """Lock the set membership so a refactor cannot silently shrink it."""
    expected = {
        MemoryCandidateKind.PROCEDURAL_RULE,
        MemoryCandidateKind.CONSTRAINT,
        MemoryCandidateKind.DECISION,
        MemoryCandidateKind.CORRECTION,
        MemoryCandidateKind.BUG,
        MemoryCandidateKind.FIX,
    }
    assert set(PROMOTABLE_KINDS) == expected


def _candidate(*, kind: MemoryCandidateKind, trust_level: TrustLevel) -> MemoryCandidate:
    from agent_memory_lite.models.candidates import TemporalSpan  # noqa: PLC0415

    ts = "2026-05-20T00:00:00+00:00"
    return MemoryCandidate(
        kind=kind,
        subject="s",
        predicate="p",
        object="o",
        evidence="ev",
        trust_level=trust_level,
        confidence=0.8,
        importance=0.6,
        temporal=TemporalSpan(observed_at=ts, valid_from=ts),
        source_episode_id="ep_test",
    )


def test_capability_writer_redacts_role_purpose() -> None:
    """Hand-test of the redact() inline — the writer modules call
    ``redact()`` before persisting, so a role with a Bearer-shaped
    purpose ends up redacted in the row."""
    from agent_memory_lite.ingestion.capability_writer import _redact, _redact_list  # noqa: PLC0415

    assert (
        _redact("Use Bearer abc123def456ghi789jkl0 to deploy")
        != "Use Bearer abc123def456ghi789jkl0 to deploy"
    )
    assert "abc123def456ghi789jkl0" not in _redact("Use Bearer abc123def456ghi789jkl0 to deploy")
    # None passes through
    assert _redact(None) is None
    # List branch
    redacted = _redact_list(["plain line", "Bearer abc123def456ghi789jkl0 leak"])
    assert redacted is not None
    assert "abc123def456ghi789jkl0" not in redacted[1]
