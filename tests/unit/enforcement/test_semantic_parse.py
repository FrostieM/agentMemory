"""Unit tests for the Ollama verdict parser."""

from __future__ import annotations

from agent_memory_lite.enforcement.semantic_parse import parse_verdict


def test_clean_json_violates_true() -> None:
    violates, why = parse_verdict('{"violates": true, "why": "broke rule X"}')
    assert violates is True
    assert why == "broke rule X"


def test_clean_json_violates_false() -> None:
    violates, _ = parse_verdict('{"violates": false, "why": "ok"}')
    assert violates is False


def test_fences_stripped() -> None:
    violates, why = parse_verdict('```json\n{"violates": true, "why": "fenced"}\n```')
    assert violates is True
    assert why == "fenced"


def test_invalid_json_defaults_to_false() -> None:
    assert parse_verdict("not json at all") == (False, "")


def test_empty_string_defaults_to_false() -> None:
    assert parse_verdict("") == (False, "")


def test_non_object_defaults_to_false() -> None:
    assert parse_verdict("[true]") == (False, "")


def test_prose_around_object_extracted() -> None:
    raw = 'Here is the verdict: {"violates": true, "why": "found it"} thanks!'
    violates, why = parse_verdict(raw)
    assert violates is True
    assert why == "found it"


def test_missing_why_returns_empty_string() -> None:
    violates, why = parse_verdict('{"violates": true}')
    assert violates is True
    assert why == ""
