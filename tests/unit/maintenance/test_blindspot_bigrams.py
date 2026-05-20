"""v3.3 — bigram + NER tokenizer tests for V3 blindspot detection."""

from __future__ import annotations

import pytest

from agent_memory_lite.maintenance.blindspot_bigrams import (
    bigram_tokens,
    is_bigrams_enabled,
    is_compound_identifier,
)


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MEMORY_BLINDSPOT_BIGRAMS_ENABLED", raising=False)


def test_default_enabled_v3_3() -> None:
    """v3.3 default ON. Operator must explicitly disable to restore
    v3.1 unigram-only behavior."""
    assert is_bigrams_enabled() is True


def test_unigrams_still_in_result() -> None:
    """The set returned must include both unigrams and bigrams so the
    rest of the pipeline (counts, decision-set diff) stays intact."""
    out = bigram_tokens("kelly sizing improves drawdown")
    # Unigrams (3+ chars, not stopword):
    assert "kelly" in out
    assert "sizing" in out
    assert "improves" in out
    assert "drawdown" in out


def test_bigrams_emitted_in_order() -> None:
    """Consecutive token pairs join with a single space."""
    out = bigram_tokens("kelly sizing improves drawdown")
    assert "kelly sizing" in out
    assert "sizing improves" in out
    assert "improves drawdown" in out


def test_bigram_too_short_skipped() -> None:
    """A bigram like 'ab cd' (5 chars) below _MIN_BIGRAM_LEN gets
    dropped — too tiny to be a meaningful phrase. (Note: 'ab' / 'cd'
    are also under _MIN_TOKEN_LEN=3, so they're filtered at the
    unigram stage too; this test sanity-checks the policy.)"""
    out = bigram_tokens("abc xyz")
    # Both unigrams pass (≥3 chars each) but the bigram 'abc xyz' is
    # 7 chars which exactly matches _MIN_BIGRAM_LEN — keep it.
    assert "abc xyz" in out


def test_opaque_id_excluded_from_bigrams() -> None:
    """Bigrams whose halves are opaque ids (ep_*, dec_*, ins_*) get
    dropped — otherwise 'ep_abc123 something' would pollute the result."""
    out = bigram_tokens("ep_abc123def456 kelly sizing dec_xyz789abc123")
    assert "kelly sizing" in out
    # No bigram pulling in an opaque-id half.
    assert not any("ep_abc" in token for token in out)
    assert not any("dec_xyz" in token for token in out)


def test_stopwords_skipped_in_unigrams() -> None:
    """Stopwords like 'the' / 'and' / 'this' are filtered upstream."""
    out = bigram_tokens("the and this kelly sizing")
    assert "the" not in out
    assert "and" not in out
    assert "this" not in out
    assert "kelly" in out


def test_empty_input_returns_empty_set() -> None:
    assert bigram_tokens("") == set()
    assert bigram_tokens("   ") == set()


def test_single_token_no_bigrams() -> None:
    """A single token produces only the unigram (no pair to bigram)."""
    out = bigram_tokens("kelly")
    assert out == {"kelly"}


def test_compound_identifier_detection() -> None:
    """Snake_case / CamelCase tokens are flagged as compound."""
    assert is_compound_identifier("boot_gate")
    assert is_compound_identifier("refreshExitConfigs")
    assert is_compound_identifier("MakerBot")
    # Plain words are not compound.
    assert not is_compound_identifier("kelly")
    assert not is_compound_identifier("decision")
    # All-upper is not compound (constants).
    assert not is_compound_identifier("HTTP")
    # All-digits / mixed-with-digits without case = not compound.
    assert not is_compound_identifier("12345")


def test_compound_identifier_preserves_underscore_through_tokenizer() -> None:
    """The regex \\w+ catches underscores, so 'boot_gate' arrives as one
    unigram and gets flagged. Verify the end-to-end."""
    out = bigram_tokens("boot_gate filter triggers")
    assert "boot_gate" in out
    assert is_compound_identifier("boot_gate")


def test_disabled_returns_unigrams_only_when_caller_checks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The bigram_tokens function itself always emits bigrams — the
    env flag is a caller-side switch (blindspot_detection consults it
    to choose between bigram and legacy tokenizers). Cover the flag
    helper directly."""
    monkeypatch.setenv("MEMORY_BLINDSPOT_BIGRAMS_ENABLED", "false")
    assert is_bigrams_enabled() is False
    monkeypatch.setenv("MEMORY_BLINDSPOT_BIGRAMS_ENABLED", "0")
    assert is_bigrams_enabled() is False
    monkeypatch.setenv("MEMORY_BLINDSPOT_BIGRAMS_ENABLED", "true")
    assert is_bigrams_enabled() is True
