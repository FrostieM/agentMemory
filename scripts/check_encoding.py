"""Gate: tracked text files are clean UTF-8, free of mojibake.

Catches encoding corruption that slips into committed files -- a file that is not
valid UTF-8, contains the U+FFFD replacement character, or carries an unambiguous
DOUBLE-ENCODING marker (a real character's UTF-8 bytes mis-decoded as cp1252 and
re-saved -- the sequence a smart quote or accented letter turns into when a UTF-8
file is mangled by a latin1 tool). The markers are generated from code points
below, so this source stays pure-ASCII; the markers never occur in legitimate
UTF-8 -- including the project's intentional Cyrillic (v1.10) content -- so the
check is false-positive-free on real text.

    python scripts/check_encoding.py            # report
    python scripts/check_encoding.py --enforce  # CI gate (non-zero on findings)
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Findings can contain the very mojibake chars we report; force UTF-8 stdout so a
# Windows cp1251/cp1252 console cannot crash the gate on its own output.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]

ROOT = Path(__file__).resolve().parents[1]
SCAN_DIRS = ("src", "tests", "scripts", "docs", "migrations")
SCAN_SUFFIXES = {".py", ".md", ".txt", ".json", ".yaml", ".yml", ".toml", ".sql", ".cfg", ".ini"}
SKIP_PARTS = {"__pycache__", ".venv", ".git", "node_modules", ".claude"}
# Encoding tools legitimately contain marker chars as detection data; exempt them.
SKIP_FILES = {
    "scripts/check_encoding.py",
    "src/agent_memory_lite/maintenance/encoding_audit.py",
    "src/agent_memory_lite/maintenance/encoding_audit_models.py",
}

# Code points whose double-encoding (their UTF-8 bytes read as cp1252, re-saved)
# yields a corruption signature: smart quotes, en/em dash, nbsp, common accented
# latin. Built from code points so this file holds no literal non-ASCII.
_DOUBLE_ENCODE_CODEPOINTS = (
    0x2019,
    0x2018,
    0x201C,
    0x201D,
    0x2013,
    0x2014,
    0x00A0,
    0x00E9,
    0x00E8,
    0x00FC,
    0x00F6,
    0x00E4,
    0x00B0,
    0x00B7,
)


def _mojibake_markers() -> tuple[str, ...]:
    markers = {"�"}  # U+FFFD replacement char = definite decode loss
    for cp in _DOUBLE_ENCODE_CODEPOINTS:
        try:
            markers.add(chr(cp).encode("utf-8").decode("cp1252"))
        except UnicodeDecodeError:
            continue
    return tuple(sorted(markers))


_MOJIBAKE = _mojibake_markers()


def _iter_files() -> list[Path]:
    out: list[Path] = []
    for d in SCAN_DIRS:
        base = ROOT / d
        if not base.exists():
            continue
        for path in base.rglob("*"):
            if any(part in SKIP_PARTS for part in path.parts):
                continue
            if path.is_file() and path.suffix.lower() in SCAN_SUFFIXES:
                out.append(path)
    root_docs = ("README.md", "CLAUDE.md", "AGENTS.md", "CONTRIBUTING.md")
    out += [ROOT / name for name in root_docs if (ROOT / name).exists()]
    return sorted(p for p in set(out) if p.relative_to(ROOT).as_posix() not in SKIP_FILES)


def scan() -> list[tuple[str, str]]:
    """Return (relpath, reason) for each file with an encoding problem."""
    findings: list[tuple[str, str]] = []
    for path in _iter_files():
        rel = path.relative_to(ROOT).as_posix()
        raw = path.read_bytes()
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            findings.append((rel, f"not valid UTF-8: {exc}"))
            continue
        hits = sorted({m for m in _MOJIBAKE if m in text})
        if hits:
            shown = ", ".join(repr(h) for h in hits)
            findings.append((rel, f"mojibake marker(s): {shown}"))
    return findings


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="UTF-8 / mojibake encoding gate.")
    parser.add_argument("--enforce", action="store_true", help="Exit non-zero on findings.")
    args = parser.parse_args(argv)

    findings = scan()
    if findings:
        print(f"FAIL: {len(findings)} file(s) with encoding problems:")
        for rel, reason in findings:
            print(f"  {rel}: {reason}")
        return 1 if args.enforce else 0
    print(f"OK: all tracked text files are clean UTF-8 (scanned {len(_iter_files())} files).")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
