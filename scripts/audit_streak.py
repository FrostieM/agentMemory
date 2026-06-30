"""Certification gate: require N consecutive zero-finding whole-system audit rounds.

This is what turns "passes its tests" into a DEFENSIBLE certification. Each
adversarial whole-system audit round (separate agents, attacker/fuzzer framing)
records its outcome in a committed ledger (``docs/certification_ledger.jsonl``);
a release is certified only when the most recent ``--require N`` rounds all found
ZERO real defects AND were run at one consistent source commit.

    python scripts/audit_streak.py --require 3
    python scripts/audit_streak.py --require 3 --strict   # also == current git HEAD
    python scripts/audit_streak.py --record --head <sha> --findings 0 \
        --scope whole-system --notes "round 1: bar + 3x5 lenses + verify"

The "same head" rule (not "== current HEAD") is deliberate: recording a round
appends to the ledger and would itself move HEAD, so the gate asks "were the last
N rounds all clean at the SAME audited commit" -- the release then tags that
commit. ``--strict`` additionally pins that commit to the current HEAD for the
exact release moment.
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
    if require < 1:
        return False, "require must be >= 1"
    if len(rounds) < require:
        return False, f"only {len(rounds)} round(s) recorded; need {require}."
    last = rounds[-require:]
    dirty = [r for r in last if int(r.get("findings", 1)) != 0]
    if dirty:
        return False, f"{len(dirty)} of the last {require} round(s) found defects (not clean)."
    heads = {str(r.get("head")) for r in last}
    if len(heads) != 1:
        return (
            False,
            f"the last {require} rounds span {len(heads)} commits, not one consistent head.",
        )
    streak_head = next(iter(heads))
    if head is not None and streak_head != head:
        return False, (
            f"clean streak is at {streak_head[:12]} but current HEAD is {head[:12]} "
            "(source changed since certification)."
        )
    return True, f"certified: {require} consecutive zero-finding rounds at {streak_head[:12]}."


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
