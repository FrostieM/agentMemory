"""Content hashing helpers.

`blake2b_hex` is the default for chunks and files (fast, 16-byte digest is plenty
for dedupe). `sha256_hex` exists for cases where a more conventional digest is
needed (e.g. interop with external tooling).
"""

from __future__ import annotations

import hashlib

DEFAULT_BLAKE2B_DIGEST_SIZE = 16


def blake2b_hex(data: bytes | str, *, digest_size: int = DEFAULT_BLAKE2B_DIGEST_SIZE) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.blake2b(data, digest_size=digest_size).hexdigest()


def sha256_hex(data: bytes | str) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()
