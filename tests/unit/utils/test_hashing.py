from __future__ import annotations

from agent_memory_lite.utils.hashing import blake2b_hex, sha256_hex


def test_blake2b_hex_is_deterministic() -> None:
    assert blake2b_hex("hello") == blake2b_hex("hello")


def test_blake2b_hex_default_size() -> None:
    h = blake2b_hex("hello")
    assert len(h) == 32  # 16 bytes -> 32 hex chars


def test_blake2b_accepts_bytes_and_str() -> None:
    assert blake2b_hex(b"hello") == blake2b_hex("hello")


def test_sha256_hex_length() -> None:
    assert len(sha256_hex("hello")) == 64
