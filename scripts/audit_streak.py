"""Certification gate: require N consecutive zero-finding whole-system audit rounds.

This is what turns "passes its tests" into a DEFENSIBLE certification. Each
adversarial whole-system audit round (separate agents, attacker/fuzzer framing)
records its outcome in a committed ledger (``docs/certification_ledger.jsonl``);
a commit is certified only when it has at least ``--require N`` rounds AND EVERY
round recorded at that commit found ZERO real defects.

    python scripts/audit_streak.py --require 3
    python scripts/audit_streak.py --require 3 --strict   # also == current git HEAD
    python scripts/audit_streak.py --record --head <sha> --findings 0 \
        --scope whole-system --notes "round 1: bar + 3x5 lenses + verify"

"Every round at the commit must be clean" (not merely "the last N are clean")
closes a laundering hole -- otherwise a known-DIRTY round at commit X could be
certified by re-running N clean rounds at the same X without fixing anything. The
only way past a dirty round is to fix the code, which moves HEAD and begins a
fresh streak. Recording a round appends to the ledger and itself moves HEAD, so
the gate keys on the latest round's commit (the release tags it); ``--strict``
additionally pins that commit == current git HEAD for the exact release moment.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

LEDGER = Path(__file__).resolve().parents[1] / "docs" / "certification_ledger.jsonl"


def _git_head() -> str | None:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True
        )
        return out.stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def load_rounds() -> list[dict[str, object]]:
    if not LEDGER.exists():
        return []
    rounds: list[dict[str, object]] = []
    for raw in LEDGER.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line:
            rounds.append(json.loads(line))
    return rounds


def _record(args: argparse.Namespace) -> int:
    head = args.head or _git_head()
    if not head:
        print("FAIL: no --head given and git HEAD is unavailable.")
        return 1
    existing = load_rounds()
    record = {
        "round": len(existing) + 1,
        "head": head,
        "timestamp": args.timestamp or "",
        "scope": args.scope,
        "findings": int(args.findings),
        "notes": args.notes,
    }
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with LEDGER.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record) + "\n")
    print(f"recorded round {record['round']} @ {head[:12]} findings={record['findings']}.")
    return 0


def evaluate(rounds: list[dict[str, object]], require: int, head: str | None) -> tuple[bool, str]:
    """Certified iff the LATEST audited commit has >= ``require`` rounds and EVERY
    round at that commit found zero defects.

    Anti-laundering: it is NOT "the last N rounds are clean" -- that let a known
    DIRTY round at commit X be certified by simply re-running N more clean rounds
    at the same X without fixing anything (global-audit 2026-06-30). A certifiable
    commit must have ZERO dirty rounds; the only way past a dirty round is to fix
    the code, which moves HEAD and starts a fresh streak."""
    if require < 1 or not rounds:
        return False, (
            "require must be >= 1" if require < 1 else f"no rounds recorded; need {require}."
        )
    streak_head = str(rounds[-1].get("head") or "")
    if not streak_head:
        return False, "the latest round has no recorded head (cannot certify)."
    at_head = [r for r in rounds if str(r.get("head") or "") == streak_head]

    def _is_dirty(round_row: dict[str, object]) -> bool:
        # round-B: a null / missing / unparseable ``findings`` must be treated as
        # DIRTY (cannot certify on ambiguous data), never crash the gate. ``.get(k, 1)``
        # alone returned None for a JSON null and ``int(None)`` raised TypeError.
        raw = round_row.get("findings")
        try:
            return raw is None or int(raw) != 0  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return True

    dirty = [r for r in at_head if _is_dirty(r)]
    if dirty:
        return False, (
            f"{len(dirty)} round(s) at the current commit {streak_head[:12]} found defects -- "
            "a certifiable commit must have ZERO dirty rounds. Fix the code (which moves "
            "HEAD) and accumulate a fresh clean streak."
        )
    if len(at_head) < require:
        return False, f"only {len(at_head)} clean round(s) at {streak_head[:12]}; need {require}."
    if head is not None and streak_head != head:
        return False, (
            f"clean streak is at {streak_head[:12]} but current HEAD is {head[:12]} "
            "(source changed since certification)."
        )
    return True, f"certified: {len(at_head)} zero-finding round(s) at {streak_head[:12]}."


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Certification audit-streak gate.")
    parser.add_argument("--require", type=int, default=3, help="Consecutive clean rounds needed.")
    parser.add_argument(
        "--strict", action="store_true", help="Also require the streak == git HEAD."
    )
    parser.add_argument("--record", action="store_true", help="Append a round to the ledger.")
    parser.add_argument("--head", default=None)
    parser.add_argument("--findings", default=0)
    parser.add_argument("--scope", default="whole-system")
    parser.add_argument("--notes", default="")
    parser.add_argument("--timestamp", default=None)
    args = parser.parse_args(argv)

    if args.record:
        return _record(args)

    rounds = load_rounds()
    head = _git_head() if args.strict else None
    ok, message = evaluate(rounds, args.require, head)
    print(("OK: " if ok else "FAIL: ") + message)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
