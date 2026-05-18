"""Single-pass digest queue drainer — invoked by OS scheduler every 5 min.

Walks ``~/.agent_memory/digest_queue.jsonl``, dedupes by latest mtime
per (workspace, file_path), and UPSERTs a digest per entry. Pure
``drain_queue`` wrapper.

Exit code 0 always (failure-soft — individual file errors logged but
never abort the runner; the scheduled task should never show a red
indicator from transient parse errors).

Invoked from the scheduled task as
``pythonw.exe <this script> --json --log-file <path>``. Using
``pythonw.exe`` (no-console variant) instead of ``powershell.exe``
prevents the brief console-window flash every 5 min that the
operator was seeing. ``--log-file`` is required under pythonw because
its stdout/stderr are not attached to any console.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import sys
from pathlib import Path

# Force UTF-8 stdout so summary glyphs survive Windows cp1251.
# Under pythonw.exe stdout may be None; the suppress catches that.
with contextlib.suppress(AttributeError, ValueError):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from agent_memory_lite.cognition.digest_worker import (  # noqa: E402
    DEFAULT_QUEUE_PATH,
    drain_queue,
)


def _redirect_streams_to_log(log_file_path: str) -> None:
    """Reopen stdout/stderr to ``log_file_path`` in append mode.

    Required when this runner is invoked via ``pythonw.exe`` — pythonw
    does not allocate a console, so without this redirect every
    ``sys.stdout.write`` would either crash (stdout=None) or be
    silently dropped. Switching the scheduled task from ``powershell.exe``
    to ``pythonw.exe`` is what eliminates the briefly-visible console
    flash on every 5-min tick.
    """
    p = Path(log_file_path).expanduser()
    p.parent.mkdir(parents=True, exist_ok=True)
    fh = p.open("a", encoding="utf-8", errors="replace", buffering=1)
    sys.stdout = fh
    sys.stderr = fh


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
    parser.add_argument(
        "--log-file",
        type=str,
        default=None,
        help=(
            "If set, redirect stdout/stderr to this path (append, UTF-8). "
            "Required when the scheduled task invokes pythonw.exe so the "
            "console flash never appears."
        ),
    )
    args = parser.parse_args(argv)
    if args.log_file:
        _redirect_streams_to_log(args.log_file)

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
