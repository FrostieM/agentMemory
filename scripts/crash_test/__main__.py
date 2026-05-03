"""CLI entry point for the crash test.

Usage:
    python -m scripts.crash_test                  # default run
    python -m scripts.crash_test --json out.json  # also write JSON report
    python -m scripts.crash_test --skip-ui        # skip Playwright phase
    python -m scripts.crash_test --skip-llm       # skip Ollama-dependent phase
    python -m scripts.crash_test --port 8766      # custom port

Always returns exit code 0 if every non-skip phase passed, otherwise non-zero.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Force UTF-8 stdout so Unicode chars in the report don't blow up on
# Windows cp1251 consoles.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]

from scripts.crash_test.runner import run_crash_test
from scripts.crash_test.workspace import QA_ROOT


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="End-to-end crash test for agent-memory-lite.")
    parser.add_argument("--port", type=int, default=8766, help="Port for the test HTTP service")
    parser.add_argument("--workspace", default="qa-crash-test", help="Test workspace_id")
    parser.add_argument("--json", default=None, help="Write JSON report to this path")
    parser.add_argument(
        "--artifacts",
        default=str(QA_ROOT.parent / "qa-crash-test-artifacts"),
        help="Directory for service logs / screenshots / JSON report",
    )
    parser.add_argument("--skip-ui", action="store_true", help="Skip the Playwright UI phase")
    parser.add_argument(
        "--skip-llm", action="store_true", help="Skip the Ollama-dependent reflective phase"
    )
    args = parser.parse_args(argv)

    artifacts_dir = Path(args.artifacts)
    reporter = run_crash_test(
        workspace_id=args.workspace,
        port=args.port,
        artifacts_dir=artifacts_dir,
        skip_ui=args.skip_ui,
        skip_llm=args.skip_llm,
    )

    text = reporter.render_text()
    print(text)

    json_path = Path(args.json) if args.json else (artifacts_dir / "report.json")
    reporter.write_json(json_path)
    print(f"\nJSON report: {json_path}")

    overall = reporter.overall_status()
    return 0 if overall in {"pass", "skip"} else 1


if __name__ == "__main__":
    sys.exit(main())
