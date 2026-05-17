"""Single-pass digest queue drainer — invoked by OS scheduler every 5 min.

Walks ``~/.agent_memory/digest_queue.jsonl``, dedupes by latest mtime
per (workspace, file_path), and UPSERTs a digest per entry. Pure
``drain_queue`` wrapper.

Exit code 0 always (failure-soft — individual file errors logged but
never abort the runner; the scheduled task should never show a red
indicator from transient parse errors).
"""

from __future__ import annotations

import argparse
import contextlib
import json
import sys
from pathlib import Path

# Force UTF-8 stdout so summary glyphs survive Windows cp1251.
with contextlib.suppress(AttributeError, ValueError):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from agent_memory_lite.v3.cognition.digest_worker import (  # noqa: E402
    DEFAULT_QUEUE_PATH,
    drain_queue,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Drain the v3 digest queue once. Used by scheduled task."
    )
    parser.add_argument(
        "--queue-path",
        type=Path,
        default=DEFAULT_QUEUE_PATH,
        help=f"Path to the JSONL queue file (default: {DEFAULT_QUEUE_PATH}).",
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON summary.")
    args = parser.parse_args(argv)

    processed = drain_queue(args.queue_path)
    summary = {"queue_path": str(args.queue_path), "processed": processed}
    if args.json:
        sys.stdout.write(json.dumps(summary) + "\n")
    else:
        sys.stdout.write(
            f"[digest-worker] drained queue {args.queue_path} -> processed={processed}\n"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
