"""1.6.0: best-effort signature extraction for symbol versioning.

The "signature" is the first non-trivial line of a symbol's body —
``def calculate(self, x, y=None):`` for Python, ``int main(int argc,
char** argv) {`` for C++, ``async fetch(id: number): Promise<User> {``
for TypeScript. We hash this line per-version; when it changes
between versions, downstream callers may break.

Best-effort: a multi-line signature (very long Python def with
type annotations split across lines) gets only its first line.
That's still useful — the function name and most parameter shapes
are typically on the first line.
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


def extract_signature(text: str) -> str:
    """Return the first signature-bearing line of ``text``, trimmed.

    For multi-line signatures this returns just the first line. We
    cap at 2000 chars to fit the SymbolVersion column constraint.
    """
    for raw in text.splitlines():
        if _is_signature_line(raw):
            return raw.strip()[:2000]
    return ""


def signature_hash(signature: str) -> str:
    """blake2b hex of the trimmed signature. ``''`` hashes to a
    sentinel so empty signatures don't collide silently."""
    return blake2b_hex(signature.strip())


def content_hash(text: str) -> str:
    """blake2b hex of the full chunk body."""
    return blake2b_hex(text)
