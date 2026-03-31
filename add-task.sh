#!/usr/bin/env bash
set -euo pipefail
NIUMA_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(dirname "$NIUMA_DIR")"
python3 "$NIUMA_DIR/lib/inbox.py" add-task \
  --inbox "$NIUMA_DIR/inbox" \
  --tasks "$NIUMA_DIR/tasks" \
  --state "$NIUMA_DIR/state.json" \
  "$@"
