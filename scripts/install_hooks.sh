#!/usr/bin/env bash
# Idempotent installer for the project's git hooks.
#
# Usage:
#     bash scripts/install_hooks.sh           # install (or refresh)
#     bash scripts/install_hooks.sh --uninstall
#
# Installs:
#     .git/hooks/pre-push -> copy of scripts/git_hooks/pre-push
#
# We copy rather than symlink so the hook still works on Windows where
# git-bash does not always honor symlinks. Re-running the installer is
# safe and refreshes the hook content.

set -e

REPO_ROOT="$(git rev-parse --show-toplevel)"
HOOKS_DIR="$REPO_ROOT/.git/hooks"
SOURCE_DIR="$REPO_ROOT/scripts/git_hooks"

# Files we manage (one entry per hook).
HOOKS=(pre-push pre-commit)

if [[ "${1:-}" == "--uninstall" ]]; then
    for hook in "${HOOKS[@]}"; do
        target="$HOOKS_DIR/$hook"
        if [[ -f "$target" ]]; then
            rm -f "$target"
            echo "[install_hooks] removed $target"
        fi
    done
    exit 0
fi

mkdir -p "$HOOKS_DIR"
for hook in "${HOOKS[@]}"; do
    src="$SOURCE_DIR/$hook"
    dst="$HOOKS_DIR/$hook"
    if [[ ! -f "$src" ]]; then
        echo "[install_hooks] missing source: $src" >&2
        exit 1
    fi
    cp "$src" "$dst"
    chmod +x "$dst"
    echo "[install_hooks] installed $dst"
done

echo ""
echo "[install_hooks] done."
echo "[install_hooks] crash test will run before every 'git push' to main."
echo "[install_hooks]   bypass with: MEMORY_SKIP_CRASH_TEST=1 git push"
echo "[install_hooks] auto-ingest staged files runs before every 'git commit'."
echo "[install_hooks]   requires MEMORY_WORKSPACE_ID env; service at \$MEMORY_SERVICE_URL (default 127.0.0.1:8765)."
echo "[install_hooks]   bypass with: MEMORY_SKIP_PRECOMMIT_INGEST=1 git commit"
echo "[install_hooks] uninstall:    bash scripts/install_hooks.sh --uninstall"
