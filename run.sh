#!/usr/bin/env bash
# Convenience launcher on Unix shells.
set -euo pipefail
PY="${PY:-python}"
exec "$PY" -m agent_memory_lite "$@"
