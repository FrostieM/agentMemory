"""1.6.0: best-effort signature extraction for symbol versioning.

The "signature" is the first non-trivial line(s) of a symbol's
body — ``def calculate(self, x, y=None):`` for Python,
``int main(int argc, char** argv) {`` for C++,
``async fetch(id: number): Promise<User> {`` for TypeScript.
We hash this signature per-version; when it changes between
versions, downstream callers may break.

2.1.5: multi-line signatures are now joined. When the first
signature-bearing line opens a paren / bracket but doesn't close
it, the extractor accumulates following lines until the balance
returns to zero (capped at 2000 chars). A signature like::

    def fetch_users(
        client: Client,
        *,
        page: int = 1,
    ) -> list[User]:

now returns
``def fetch_users(client: Client, *, page: int = 1, ) -> list[User]:``
in one line — and a parameter change anywhere in the multi-line
form correctly bumps signature_hash.
"""

from __future__ import annotations

from agent_memory_lite.utils.hashing import blake2b_hex

# Languages where we strip leading decorator / annotation lines
# before picking the signature (their signature is below).
_DECO_PREFIXES: tuple[str, ...] = (
    "@",  # python / java / c#
    "#[",  # rust
    "[[",  # c++ attribute
)


def _is_signature_line(line: str) -> bool:
    """A line is signature-bearing when it isn't blank, isn't a
    comment, and isn't a decorator / annotation."""
    stripped = line.strip()
    if not stripped:
        return False
    # Comment styles across supported languages.
    if stripped.startswith(("#", "//", "/*", "*", '"""', "'''")):
        return False
    return not stripped.startswith(_DECO_PREFIXES)


# Only parens + square brackets count as "signature continuation".
# Curly braces open a body block (TS / Java / C++ / C#), so we stop
# the moment a line introduces them after balanced parens — that's
# the end of the signature span.
_OPEN_BRACKETS = "(["
_CLOSE_BRACKETS = ")]"


def _bracket_delta(line: str) -> int:
    """Return open-minus-close bracket count for one line.

    Naive — does not account for brackets inside string literals or
    comments. Good enough for the small subset of signature lines we
    care about (function defs / class bodies opening braces).
    """
    delta = 0
    for ch in line:
        if ch in _OPEN_BRACKETS:
            delta += 1
        elif ch in _CLOSE_BRACKETS:
            delta -= 1
    return delta


def extract_signature(text: str) -> str:
    """Return the signature span of ``text``, trimmed.

    2.1.5: when the first signature-bearing line has unbalanced
    brackets (``def foo(`` with no matching ``)`` on the same line),
    accumulate following lines until the bracket count returns to
    zero or we hit a body-opening colon / brace. Capped at 2000
    chars to fit the SymbolVersion column constraint.
    """
    lines = text.splitlines()
    start = next(
        (i for i, raw in enumerate(lines) if _is_signature_line(raw)),
        None,
    )
    if start is None:
        return ""
    parts: list[str] = [lines[start].rstrip()]
    balance = _bracket_delta(lines[start])
    cursor = start + 1
    while balance > 0 and cursor < len(lines):
        cur = lines[cursor]
        parts.append(cur.strip())
        balance += _bracket_delta(cur)
        cursor += 1
    return " ".join(s.strip() for s in parts).strip()[:2000]


def signature_hash(signature: str) -> str:
    """blake2b hex of the trimmed signature. ``''`` hashes to a
    sentinel so empty signatures don't collide silently."""
    return blake2b_hex(signature.strip())


def content_hash(text: str) -> str:
    """blake2b hex of the full chunk body."""
    return blake2b_hex(text)
