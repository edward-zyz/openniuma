#!/usr/bin/env bash
# openniuma.sh — openNiuMa 统一 CLI 入口
set -euo pipefail
NIUMA_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(dirname "$NIUMA_DIR")"
RUNTIME_DIR="${REPO_DIR}/.openniuma-runtime"

cmd="${1:-help}"; shift 2>/dev/null || true
case "$cmd" in
  start)     exec bash "$NIUMA_DIR/dev-loop.sh" "$@" ;;
  init)      exec bash "$NIUMA_DIR/init.sh" "$@" ;;
  status)    exec python3 "$NIUMA_DIR/lib/status.py" --state "$RUNTIME_DIR/state.json" --stats "$RUNTIME_DIR/stats.json" --workers "$RUNTIME_DIR/workers" "$@" ;;
  dashboard) exec python3 "$NIUMA_DIR/lib/status.py" --state "$RUNTIME_DIR/state.json" --stats "$RUNTIME_DIR/stats.json" --workers "$RUNTIME_DIR/workers" --format dashboard "$@" ;;
  stats)     exec python3 "$NIUMA_DIR/lib/stats.py" summary "$RUNTIME_DIR/stats.json" "$@" ;;
  add)       exec python3 "$NIUMA_DIR/lib/inbox.py" add-task --inbox "$RUNTIME_DIR/inbox" --tasks "$RUNTIME_DIR/tasks" --state "$RUNTIME_DIR/state.json" "$@" ;;
  stop)      mkdir -p "$RUNTIME_DIR/inbox" && touch "$RUNTIME_DIR/inbox/STOP" && echo "STOP 信号已发送" ;;
  cancel)    mkdir -p "$RUNTIME_DIR/inbox" && touch "$RUNTIME_DIR/inbox/CANCEL-${1:?需要 task_id}" && echo "取消信号已发送: $1" ;;
  help|*)
    cat <<HELP
用法: openniuma.sh <command> [args]

Commands:
  start [--workers=N] [--model=X]  启动编排器（--model 覆盖所有 phase 模型）
  init [--no-ai] [--dry-run]   初始化新项目
  status [--format json]       查看状态
  dashboard [-w [interval]]    终端实时看板
  stats [--task N]             运行统计
  add <description> [--complexity 低|中|高]  快捷入队
  stop                         停止编排器
  cancel <task_id>             取消任务
HELP
    ;;
esac
