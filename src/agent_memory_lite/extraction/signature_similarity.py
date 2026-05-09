"""2.1.4: token-based MinHash for ``similar_signature`` soft edges.

The hard graph (``symbol_edges``) records explicit AST-level
relationships. The soft graph adds heuristic ones; v1.7.0 already
emits ``co_changed`` from the file-ingest co-change pass. This
module fills in the third edge kind: ``similar_signature``.

Two functions a method ``fetch_users(client) -> list[User]`` and
``fetch_orders(client) -> list[Order]`` share most signature
tokens — they are likely parallel implementations of the same
idea. When an agent rewrites one, soft-graph neighbors surface
the other as a candidate for the same change.

Implementation: bag-of-tokens MinHash with N hash permutations
(default 64). Estimated Jaccard = matches / N. Token set is
``set(re.split(r"[\\s(),:;{}\\[\\]<>]+", signature.lower())) - {""}``.

Deterministic: hash function is blake2b with a fixed permutation
seed, so the same signature always produces the same MinHash.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

_TOKEN_SPLIT = re.compile(r"[\s(),:;{}\[\]<>]+")
_DEFAULT_PERMUTATIONS = 64
_SEED_PREFIX = b"sigmh-v1:"


def tokenize(signature: str) -> set[str]:
    """Lower-case + split a signature into a bag of tokens."""
    raw = _TOKEN_SPLIT.split(signature.lower())
    return {tok for tok in raw if tok}


def _hash_for(token: str, perm: int) -> int:
    """Permuted hash via blake2b digest seeded by perm index. Returns
    the first 8 bytes of the digest as an unsigned int.
    """
    h = hashlib.blake2b(digest_size=8)
    h.update(_SEED_PREFIX)
    h.update(perm.to_bytes(4, "big"))
    h.update(token.encode("utf-8"))
    return int.from_bytes(h.digest(), "big")


@dataclass(frozen=True, slots=True)
class MinHashSignature:
    permutations: int
    values: tuple[int, ...]


_EMPTY = MinHashSignature(permutations=_DEFAULT_PERMUTATIONS, values=())


def minhash(signature: str, *, permutations: int = _DEFAULT_PERMUTATIONS) -> MinHashSignature:
    """Compute the MinHash of a signature's token bag. Empty
    signatures get an empty signature object (Jaccard with anything
    is 0).
    """
    tokens = tokenize(signature)
    if not tokens:
        return MinHashSignature(permutations=permutations, values=())
    out: list[int] = []
    for p in range(permutations):
        out.append(min(_hash_for(t, p) for t in tokens))
    return MinHashSignature(permutations=permutations, values=tuple(out))


def jaccard(a: MinHashSignature, b: MinHashSignature) -> float:
    """Estimated Jaccard similarity in [0.0, 1.0]. Returns 0.0 when
    either signature is empty or the permutation counts differ.
    """
    if not a.values or not b.values or a.permutations != b.permutations:
        return 0.0
    matches = sum(1 for x, y in zip(a.values, b.values, strict=True) if x == y)
    return matches / a.permutations


def is_empty(sig: MinHashSignature) -> bool:
    return not sig.values


# Re-export the canonical empty signature for callers that want to
# represent "no tokens" without recomputing.
EMPTY_SIGNATURE = _EMPTY
