#!/bin/bash
# orchestrator.sh — 无人值守循环编排器
# 用法：./orchestrator.sh [--init]
#   --init  首次运行，执行 INIT 阶段
#   无参数  从 loop-state.json 当前状态恢复

# 不用 set -e：claude 非零退出需要优雅处理，不能直接崩
set -uo pipefail

STATE_FILE="$HOME/.poi-loop/loop-state.json"
LOG_DIR="$HOME/.poi-loop/logs"
MAX_ROUNDS=100              # 安全上限，防止无限循环
MAX_IMPLEMENT_RETRIES=10    # IMPLEMENT 同 Phase 重入上限

# ── 解析项目根目录，将相对路径转为绝对路径 ──
PROJECT_ROOT="$(git rev-parse --show-toplevel 2>/dev/null)" || {
  echo "[orchestrator] FATAL: 不在 git 仓库中，请在项目目录内执行此脚本"
  exit 1
}
cd "$PROJECT_ROOT"

DOC="$PROJECT_ROOT/loop/loop_v2/autonomous-dev-loop.md"
BACKLOG="$PROJECT_ROOT/loop/backlog.md"

mkdir -p "$LOG_DIR"

# ── 前置检查 ──
if ! command -v claude &>/dev/null; then
  echo "[orchestrator] FATAL: claude 命令未找到，请先安装 Claude Code CLI"
  exit 1
fi

if ! command -v python3 &>/dev/null; then
  echo "[orchestrator] FATAL: python3 命令未找到"
  exit 1
fi

# ── 颜色输出 ──
info()  { printf '\033[0;36m[orchestrator]\033[0m %s\n' "$*"; }
warn()  { printf '\033[0;33m[orchestrator]\033[0m %s\n' "$*"; }
error() { printf '\033[0;31m[orchestrator]\033[0m %s\n' "$*"; }
ok()    { printf '\033[0;32m[orchestrator]\033[0m %s\n' "$*"; }

# ── 信号处理：优雅退出 ──
INTERRUPTED=false
cleanup() {
  INTERRUPTED=true
  warn "收到中断信号，等待当前 claude 会话结束后退出..."
  # 不直接 kill 子进程，让 claude 自行清理
}
trap cleanup SIGINT SIGTERM

# ── 从状态文件读取字段（带错误处理 + boolean 标准化） ──
read_state() {
  local field="$1"
  local result
  result=$(python3 -c "
import json, sys
try:
    d = json.load(open('$STATE_FILE'))
    v = d.get('$field', '')
    # boolean 统一输出为小写 true/false
    if isinstance(v, bool):
        print('true' if v else 'false')
    else:
        print(v)
except Exception as e:
    print('', file=sys.stderr)
    sys.exit(1)
" 2>/dev/null) || {
    warn "读取状态字段 '$field' 失败（文件损坏或不存在）"
    echo ""
    return 1
  }
  echo "$result"
}

# ── 运行 claude 会话（带退出码捕获） ──
run_claude() {
  local prompt="$1"
  local log_file="$2"

  # 不用 set -e，手动捕获管道退出码
  echo "$prompt" | claude --print 2>&1 | tee "$log_file"
  local exit_code=${PIPESTATUS[1]:-0}

  if [ "$exit_code" -ne 0 ]; then
    warn "Claude Code 退出码: $exit_code"
  fi

  return "$exit_code"
}

# ── 根据 Phase 生成 Prompt ──
get_prompt() {
  local phase="$1"
  case "$phase" in
    FAST_TRACK)
      cat <<PROMPT
阅读 $DOC 的 FAST_TRACK 部分。
阅读 $STATE_FILE 获取当前任务。
阅读 $BACKLOG 获取需求描述。

你同时担任架构师和开发者。对当前低复杂度任务执行快速通道：
探索代码 → 创建功能分支 → TDD 实现 → push。
不需要写独立的 spec/plan 文件，在 commit message 中说明设计决策即可。
如果发现任务比预期复杂，按文档的降级机制切换到完整流程。
全程自主工作，不要问我问题。
PROMPT
      ;;
    DESIGN)
      cat <<PROMPT
阅读 $DOC 的 Phase 1: DESIGN 部分。
阅读 $STATE_FILE 获取当前任务。
阅读 $BACKLOG 获取需求描述。

你是架构师。你的任务是为当前 backlog 条目输出 spec 和 plan。
确保在 dev 分支上工作（分支名见状态文件的 dev_branch 字段）。
如果任务复杂度为"中"或"高"，使用 /brainstorming 技能辅助设计（自主推进模式，不等待确认）。
全程自主工作，不要问我问题。
PROMPT
      ;;
    IMPLEMENT)
      cat <<PROMPT
阅读 $DOC 的 Phase 2: IMPLEMENT 部分。
阅读 $STATE_FILE 获取当前任务、dev 分支名和 plan 路径。
阅读对应的 plan 文件。

你是开发者。严格按照 plan 逐步实现，使用 TDD。
功能分支从 dev 分支拉出（不是从 master）。
全程自主工作，不要问我问题。
PROMPT
      ;;
    VERIFY)
      cat <<PROMPT
阅读 $DOC 的 Phase 3: VERIFY 部分。
阅读 $STATE_FILE 获取当前任务。
阅读 $BACKLOG 获取原始需求描述。
如果状态中 spec_path 非空，阅读对应的 spec 文件。

你是代码审查者。你的职责是验证：
1. spec 是否完整覆盖了 backlog 中的原始需求（完整流程）
2. 实现是否正确且符合 spec（或 backlog 需求，若为快速通道）和项目规范
你必须严格执行检查，发现问题必须指出，不可放水。
全程自主工作，不要问我问题。
PROMPT
      ;;
    FIX)
      cat <<PROMPT
阅读 $DOC 的 Phase 3.5: FIX 部分。
阅读 $STATE_FILE 获取修复清单路径。
阅读修复清单文件（路径在状态的 fix_list_path 字段）。

你是修复开发者。只修复清单中的 Critical 和 Major 问题，不做额外改动。
如果清单包含"spec 层面问题"，需要同时修正 spec 和对应的实现代码。
修完后 push，全程自主工作，不要问我问题。
PROMPT
      ;;
    MERGE)
      cat <<PROMPT
阅读 $DOC 的 Phase 4: MERGE 部分。
阅读 $STATE_FILE 获取当前任务和分支信息。

你是集成者。将功能分支合入 dev，跑 CI，更新 backlog，推进到下一个任务。
全程自主工作，不要问我问题。
PROMPT
      ;;
    MERGE_FIX)
      cat <<PROMPT
阅读 $DOC 的 Phase 4.5: MERGE_FIX 部分。
阅读 $STATE_FILE 获取当前状态和 CI 失败日志路径。
阅读 CI 失败日志文件。

你是集成修复开发者。CI 在 dev 合入后失败，你需要在功能分支上修复兼容性问题。
全程自主工作，不要问我问题。
PROMPT
      ;;
    FINALIZE)
      cat <<PROMPT
阅读 $DOC 的 Phase 5: FINALIZE 部分。
阅读 $STATE_FILE 获取完成情况。

你是发布经理。清理功能分支，创建从 dev 到 master 的汇总 PR（仅创建，不合并）。
⚠️ 禁止执行 gh pr merge 或任何将代码合入 master 的操作。
全程自主工作，不要问我问题。
PROMPT
      ;;
    *)
      echo ""
      ;;
  esac
}

# ── 主流程 ──

# 处理 --init
if [[ "${1:-}" == "--init" ]]; then
  if [ -f "$STATE_FILE" ]; then
    error "状态文件已存在，如需重新初始化请先删除: rm $STATE_FILE"
    exit 1
  fi
  info "执行 INIT..."
  INIT_PROMPT="阅读 $DOC 的 Phase 0: INIT 部分。
执行初始化：创建状态目录、dev 集成分支，写入初始 loop-state.json。
然后检查第一个任务的复杂度，进入对应 Phase。
全程自主工作，不要问我问题。"

  LOG_FILE="$LOG_DIR/init-$(date +%Y%m%d-%H%M%S).log"
  info "日志: $LOG_FILE"
  run_claude "$INIT_PROMPT" "$LOG_FILE" || true

  # 检查 INIT 是否成功创建了状态文件
  if [ ! -f "$STATE_FILE" ]; then
    error "INIT 已执行但状态文件未创建（AI 可能未正确完成初始化）"
    error "请检查日志: $LOG_FILE"
    exit 1
  fi
  ok "INIT 完成，状态文件已创建"
fi

# 检查状态文件
if [ ! -f "$STATE_FILE" ]; then
  error "状态文件不存在，请先执行: $0 --init"
  exit 1
fi

ROUND=0
IMPLEMENT_RETRIES=0

while true; do
  ROUND=$((ROUND + 1))

  # 安全上限
  if [ "$ROUND" -gt "$MAX_ROUNDS" ]; then
    error "已达到最大轮次 $MAX_ROUNDS，停止。可能存在循环问题。"
    break
  fi

  # 中断信号检查
  if [ "$INTERRUPTED" = true ]; then
    warn "中断信号已收到，优雅退出。"
    break
  fi

  # 读取当前状态
  PHASE=$(read_state current_phase) || { error "无法读取状态文件，停止。"; break; }
  ALERT=$(read_state system_alert) || ALERT="false"
  ITEM_ID=$(read_state current_item_id) || ITEM_ID="?"

  info "── Round $ROUND | Phase: $PHASE | Item: $ITEM_ID ──"

  # 检查终止条件
  if [ "$PHASE" = "AWAITING_HUMAN_REVIEW" ]; then
    ok "全部完成！汇总 PR 已创建，等待人工审核。"
    break
  fi

  if [ "$ALERT" = "true" ]; then
    error "system_alert 触发，3+ 功能连续 BLOCKED，需要人工排查。"
    break
  fi

  # 生成 Prompt
  PROMPT=$(get_prompt "$PHASE")
  if [ -z "$PROMPT" ]; then
    error "未知 Phase: $PHASE，停止。"
    break
  fi

  # 启动 Claude Code 会话
  LOG_FILE="$LOG_DIR/round${ROUND}-${PHASE}-item${ITEM_ID}-$(date +%Y%m%d-%H%M%S).log"
  info "启动 Claude Code | Phase: $PHASE | 日志: $LOG_FILE"

  run_claude "$PROMPT" "$LOG_FILE" || true

  # 中断信号检查（claude 结束后）
  if [ "$INTERRUPTED" = true ]; then
    warn "中断信号已收到，优雅退出。"
    break
  fi

  # 会话结束后短暂停顿，让文件系统同步
  sleep 2

  # 检查状态文件是否仍然有效
  if [ ! -f "$STATE_FILE" ]; then
    error "状态文件丢失！尝试从备份恢复..."
    if [ -f "$HOME/.poi-loop/loop-state.prev.json" ]; then
      cp "$HOME/.poi-loop/loop-state.prev.json" "$STATE_FILE"
      warn "已从 prev.json 恢复，继续..."
    else
      error "无备份可恢复，停止。"
      break
    fi
  fi

  # 检查 Phase 是否变化
  NEW_PHASE=$(read_state current_phase) || { error "无法读取状态文件，停止。"; break; }

  if [ "$NEW_PHASE" = "$PHASE" ]; then
    if [ "$PHASE" = "IMPLEMENT" ]; then
      # IMPLEMENT 允许多轮重入（上下文耗尽），但有上限
      IMPLEMENT_RETRIES=$((IMPLEMENT_RETRIES + 1))
      if [ "$IMPLEMENT_RETRIES" -ge "$MAX_IMPLEMENT_RETRIES" ]; then
        error "IMPLEMENT 已重试 $MAX_IMPLEMENT_RETRIES 次仍未完成，停止。请检查日志。"
        break
      fi
      info "IMPLEMENT 重入 ($IMPLEMENT_RETRIES/$MAX_IMPLEMENT_RETRIES)，断点续传..."
    else
      # 非 IMPLEMENT Phase 未变化，重试一次
      warn "Phase 未变化（仍为 $PHASE），再试一次..."
      run_claude "$PROMPT" "$LOG_FILE" || true
      sleep 2
      NEW_PHASE=$(read_state current_phase) || { error "无法读取状态文件，停止。"; break; }
      if [ "$NEW_PHASE" = "$PHASE" ]; then
        error "两次执行后 Phase 仍未推进，停止。请检查日志: $LOG_FILE"
        break
      fi
    fi
  else
    # Phase 变化了，重置 IMPLEMENT 计数器
    IMPLEMENT_RETRIES=0
  fi

  info "Phase: $PHASE → $NEW_PHASE"
done

info "编排器结束。最终状态:"
if [ -f "$STATE_FILE" ]; then
  python3 -m json.tool "$STATE_FILE" 2>/dev/null || cat "$STATE_FILE"
else
  warn "状态文件不存在"
fi
