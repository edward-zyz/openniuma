#!/usr/bin/env bash
set -euo pipefail
NIUMA_DIR="$(cd "$(dirname "$0")" && pwd)"

# 检测是否请求旧的文本/JSON格式
FORMAT=""
WATCH=false
INTERVAL=5
ARGS=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --format) FORMAT="$2"; shift 2 ;;
    -w) WATCH=true; shift
        if [[ $# -gt 0 && "$1" =~ ^[0-9]+$ ]]; then
          INTERVAL="$1"; shift
        fi ;;
    *) ARGS+=("$1"); shift ;;
  esac
done

# 非 TUI 格式走旧的 status.py
if [ -n "$FORMAT" ] && [ "$FORMAT" != "tui" ]; then
  if [ "$WATCH" = true ]; then
    while true; do
      clear
      python3 "$NIUMA_DIR/lib/status.py" --state "$NIUMA_DIR/state.json" --format "$FORMAT" "${ARGS[@]+"${ARGS[@]}"}"
      sleep "$INTERVAL"
    done
  else
    python3 "$NIUMA_DIR/lib/status.py" --state "$NIUMA_DIR/state.json" --format "$FORMAT" "${ARGS[@]+"${ARGS[@]}"}"
  fi
  exit 0
fi

# 默认启动 TUI
exec python3 "$NIUMA_DIR/tui/app.py" --dir "$NIUMA_DIR" "${ARGS[@]+"${ARGS[@]}"}"
