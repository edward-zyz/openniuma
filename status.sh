#!/usr/bin/env bash
set -euo pipefail
NIUMA_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(dirname "$NIUMA_DIR")"
RUNTIME_DIR="${REPO_DIR}/.openniuma-runtime"
python3 "$NIUMA_DIR/lib/status.py" --state "$RUNTIME_DIR/state.json" --stats "$RUNTIME_DIR/stats.json" --workers "$RUNTIME_DIR/workers" "$@"
