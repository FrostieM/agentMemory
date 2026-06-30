"""Enforce the project's modular-file SLOC ceiling -- as a RATCHET.

Project rule (CLAUDE.md / behaviors): source files under
``src/agent_memory_lite`` stay at or below 150 SLOC, one concern per module.

The ceiling could not land as a one-shot mega-refactor, so a set of pre-existing
oversized files was grandfathered. The old policy only WARNed on them -- which
let grandfathered files keep GROWING (brain_pass.py ballooned from ~210 to 1000+
SLOC while merely "warning"). This is now a RATCHET instead:

* **Hard fail** when a file NEW to ``src/agent_memory_lite`` exceeds the limit
  (150). Stops further drift. (unchanged)
* **Hard fail** when a grandfathered file exceeds its FROZEN CAP -- the SLOC it
  had when the ratchet landed (``scripts/sloc_baseline.json``). A grandfathered
  file may shrink (good) or stay put, but never grow. The only way to add code
  to one is to decompose it first.
* **Hard fail** when a baselined file has dropped to <= 150 SLOC but is still in
  the baseline -- decomposing a file must REMOVE it from the baseline, so the set
  can only shrink, never silently retain stale entries.

``--write-baseline`` regenerates ``sloc_baseline.json`` with RATCHET-DOWN
semantics: it lowers a cap to the current size, drops files now <= 150, and
REFUSES to raise any cap or admit a brand-new oversized file (those must be fixed,
not baselined). Run it after an intentional decomposition.

CI runs ``--enforce``. ``--list`` prints every file's SLOC.

SLOC = lines that are not blank and not pure comments. Multi-line strings count.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

DEFAULT_LIMIT = 150
ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = ROOT / "src" / "agent_memory_lite"
BASELINE_PATH = Path(__file__).resolve().parent / "sloc_baseline.json"


def _sloc(path: Path) -> int:
    count = 0
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        count += 1
    return count


def collect(root: Path | None = None) -> list[tuple[str, int]]:
    # Read SRC_ROOT at call time (not as a default arg bound at import) so tests
    # can monkeypatch the module global.
    root = root if root is not None else SRC_ROOT
    rows: list[tuple[str, int]] = []
    for path in sorted(root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        rows.append((path.relative_to(root).as_posix(), _sloc(path)))
    return rows


def load_baseline() -> dict[str, int]:
    if not BASELINE_PATH.exists():
        return {}
    return {
        str(k): int(v) for k, v in json.loads(BASELINE_PATH.read_text(encoding="utf-8")).items()
    }


def _write_baseline(rows: list[tuple[str, int]], limit: int) -> int:
    """Regenerate the baseline with ratchet-DOWN semantics: never raise a cap,
    never admit a NEW oversized file. Returns a non-zero exit if it would have to
    (so the caller fixes the file instead of baselining it)."""
    old = load_baseline()
    current = {rel: sloc for rel, sloc in rows if sloc > limit}
    if not old:
        # First-time bootstrap: freeze every currently-oversized file at its size.
        BASELINE_PATH.write_text(
            json.dumps(dict(sorted(current.items())), indent=2) + "\n", encoding="utf-8"
        )
        print(f"bootstrapped {BASELINE_PATH.name}: {len(current)} capped file(s).")
        return 0
    new_offenders = sorted(rel for rel in current if rel not in old)
    if new_offenders:
        print(f"FAIL: refusing to baseline {len(new_offenders)} NEW oversized file(s):")
        for rel in new_offenders:
            print(f"  {current[rel]:5d}  {rel}")
        print("Split them below the ceiling instead of baselining new debt.")
        return 1
    # Ratchet down: cap = min(old cap, current); drop files now <= limit.
    new_baseline = {rel: min(old[rel], current[rel]) for rel in current}
    BASELINE_PATH.write_text(
        json.dumps(dict(sorted(new_baseline.items())), indent=2) + "\n", encoding="utf-8"
    )
    dropped = sorted(set(old) - set(new_baseline))
    print(
        f"wrote {BASELINE_PATH.name}: {len(new_baseline)} capped file(s); dropped {len(dropped)}."
    )
    return 0


def _print_failures(
    new_violations: list[tuple[str, int]],
    grew: list[tuple[str, int]],
    stale: list[str],
    baseline: dict[str, int],
    sloc_by_file: dict[str, int],
    limit: int,
) -> bool:
    """Print each hard-fail class; return True if any fired."""
    failed = False
    if new_violations:
        print(f"FAIL: {len(new_violations)} NEW file(s) over {limit} SLOC:")
        for rel, sloc in sorted(new_violations, key=lambda r: -r[1]):
            print(f"  {sloc:5d}  {rel}")
        print("Split the file into a subpackage (one concern per module) before adding more.\n")
        failed = True
    if grew:
        print(f"FAIL: {len(grew)} grandfathered file(s) GREW past their frozen cap (ratchet):")
        for rel, sloc in sorted(grew, key=lambda r: -r[1]):
            print(f"  {sloc:5d} > cap {baseline[rel]:<5d}  {rel}")
        print("A grandfathered file may only shrink. Decompose it instead of adding code.\n")
        failed = True
    if stale:
        print(
            f"FAIL: {len(stale)} baselined file(s) dropped to <= {limit} SLOC -- "
            "remove from baseline:"
        )
        for rel in stale:
            print(f"  {sloc_by_file.get(rel, 0):5d}  {rel}")
        print("Run `python scripts/check_sloc.py --write-baseline` to shrink the ratchet set.\n")
        failed = True
    return failed


def _run_check(rows: list[tuple[str, int]], limit: int, enforce: bool) -> int:
    baseline = load_baseline()
    sloc_by_file = dict(rows)
    over = [(rel, sloc) for rel, sloc in rows if sloc > limit]
    new_violations = [(rel, sloc) for rel, sloc in over if rel not in baseline]
    grew = [(rel, sloc) for rel, sloc in over if rel in baseline and sloc > baseline[rel]]
    within_cap = [(rel, sloc) for rel, sloc in over if rel in baseline and sloc <= baseline[rel]]
    # A baselined file now at/below the ceiling: decomposed -> must be removed.
    stale = sorted(rel for rel in baseline if sloc_by_file.get(rel, 0) <= limit)

    if within_cap:
        print(f"WARN: {len(within_cap)} grandfathered file(s) over {limit} SLOC (within cap):")
        for rel, sloc in sorted(within_cap, key=lambda r: -r[1]):
            print(f"  {sloc:5d} / cap {baseline[rel]:<5d}  {rel}")
        print("Decompose them in dedicated commits; the cap means they can only shrink.\n")

    failed = _print_failures(new_violations, grew, stale, baseline, sloc_by_file, limit)
    if not over and not stale:
        print(f"OK: every src/agent_memory_lite/*.py is at or below {limit} SLOC.")
    elif not failed:
        print("OK: no new oversized files; all grandfathered files within their frozen caps.")
    return 1 if (failed and enforce) else 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--enforce", action="store_true", help="Exit non-zero on hard violations.")
    parser.add_argument("--list", action="store_true", help="Print every file's SLOC, high to low.")
    parser.add_argument(
        "--write-baseline",
        action="store_true",
        help="Regenerate sloc_baseline.json (ratchet-down).",
    )
    parser.add_argument(
        "--limit", type=int, default=DEFAULT_LIMIT, help=f"Ceiling ({DEFAULT_LIMIT})."
    )
    args = parser.parse_args(argv)

    rows = collect()
    if args.list:
        for rel, sloc in sorted(rows, key=lambda r: -r[1]):
            print(f"{sloc:5d}  {rel}")
        return 0
    if args.write_baseline:
        return _write_baseline(rows, args.limit)
    return _run_check(rows, args.limit, args.enforce)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
