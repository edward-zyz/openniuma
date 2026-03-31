#!/usr/bin/env bash
set -euo pipefail
NIUMA_DIR="$(cd "$(dirname "$0")" && pwd)"
python3 "$NIUMA_DIR/lib/stats.py" summary "$NIUMA_DIR/stats.json" "$@"
