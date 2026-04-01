#!/usr/bin/env bash
set -euo pipefail
export PATH="/opt/homebrew/bin:/opt/homebrew/sbin:${PATH}"
NIUMA_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(dirname "$NIUMA_DIR")"
RUNTIME_DIR="${REPO_DIR}/.openniuma-runtime"
if command -v python3.13 >/dev/null 2>&1; then
  PYTHON_BIN="python3.13"
else
  PYTHON_BIN="python3"
fi
"$PYTHON_BIN" "$NIUMA_DIR/lib/status.py" --state "$RUNTIME_DIR/state.json" --stats "$RUNTIME_DIR/stats.json" --workers "$RUNTIME_DIR/workers" "$@"
