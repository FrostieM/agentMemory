#!/usr/bin/env bash
# Manual pre-deploy gate. Run this explicitly when you want a full
# verification pass before pushing a release tag, regardless of whether
# the pre-push hook is installed.
#
# Differs from the pre-push hook in two ways:
#   * Includes the UI Playwright phase (--skip-ui is NOT passed).
#   * Auto-detects Ollama; if reachable, the v1.8 reflective phase runs.
#
# Usage:
#     bash scripts/predeploy.sh
#
# Exit code propagates from the crash test: 0 = clean, 1 = problems
# (full report at tests/qa/qa-crash-test-artifacts/report.json).

set -e

REPO_ROOT="$(git rev-parse --show-toplevel)"
cd "$REPO_ROOT"

VENV_PY="$REPO_ROOT/.venv/Scripts/python.exe"
if [[ ! -x "$VENV_PY" ]]; then
    VENV_PY="$REPO_ROOT/.venv/bin/python"
fi
if [[ ! -x "$VENV_PY" ]]; then
    echo "[predeploy] no .venv python found — install the project first."
    exit 1
fi

echo "[predeploy] running full crash test (UI + Ollama autodetect)..."
"$VENV_PY" -m scripts.crash_test
status=$?
if [[ $status -eq 0 ]]; then
    echo ""
    echo "[predeploy] crash test PASS — ready to push."
else
    echo ""
    echo "[predeploy] crash test FAILED — fix problems before pushing."
    echo "[predeploy] report: tests/qa/qa-crash-test-artifacts/report.json"
fi
exit $status
