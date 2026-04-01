#!/usr/bin/env bash
set -euo pipefail
NIUMA_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(dirname "$NIUMA_DIR")"
RUNTIME_DIR="${REPO_DIR}/.openniuma-runtime"
python3 "$NIUMA_DIR/lib/inbox.py" add-task \
  --inbox "$RUNTIME_DIR/inbox" \
  --tasks "$RUNTIME_DIR/tasks" \
  --state "$RUNTIME_DIR/state.json" \
  "$@"
