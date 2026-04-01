#!/usr/bin/env bash
set -euo pipefail
export PATH="/opt/homebrew/bin:/opt/homebrew/sbin:${PATH}"
NIUMA_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(dirname "$NIUMA_DIR")"
RUNTIME_DIR="${REPO_DIR}/.openniuma-runtime"

USE_AI=true
DRY_RUN=false
for arg in "$@"; do
  case "$arg" in
    --no-ai) USE_AI=false ;;
    --dry-run) DRY_RUN=true ;;
  esac
done

echo "🚀 初始化 openNiuMa..."

# 0. Python 版本检查
python3 "$NIUMA_DIR/lib/compat.py" check-python

# 1. 创建目录
# 代码目录
mkdir -p openniuma/prompts openniuma/.cache
# 运行时目录
mkdir -p "$RUNTIME_DIR"/{inbox,tasks,logs,reviews,workers,drafts}

# 2. 探测
echo "🔍 探测项目配置..."
MAIN_BRANCH=$(git symbolic-ref refs/remotes/origin/HEAD 2>/dev/null | sed 's|refs/remotes/origin/||' || echo "main")
PROJECT_NAME=$(basename "$(git remote get-url origin 2>/dev/null | sed 's/\.git$//')" 2>/dev/null || basename "$REPO_DIR")
eval "$(python3 "$NIUMA_DIR/lib/detect.py" "$REPO_DIR" --shell-vars)"
echo "  主分支: $MAIN_BRANCH"
echo "  项目名: $PROJECT_NAME"
echo "  技术栈: $DETECT_STACK"
echo "  Gate: $(echo "$DETECT_GATE" | head -1)"

# 3. dry-run
if [ "$DRY_RUN" = true ]; then
  echo ""
  echo "🧪 Dry-run 模式 — 预览配置，不写入文件"
  echo "  workflow.yaml: project.name=$PROJECT_NAME, main_branch=$MAIN_BRANCH"
  echo "  gate_command: $DETECT_GATE"
  echo "  after_create hook: $(echo "$DETECT_AFTER_CREATE" | wc -l | tr -d ' ') 行"
  exit 0
fi

# 4. 生成 workflow.yaml
if [ ! -f openniuma/workflow.yaml ]; then
  python3 "$NIUMA_DIR/lib/config.py" generate-workflow \
    --name "$PROJECT_NAME" \
    --main-branch "$MAIN_BRANCH" \
    --gate-command "$DETECT_GATE" \
    --after-create "$DETECT_AFTER_CREATE" \
    --before-remove "$DETECT_BEFORE_REMOVE" \
    --spec-dir "$DETECT_SPEC_DIR" \
    --plan-dir "$DETECT_PLAN_DIR" \
    > openniuma/workflow.yaml
  echo "  ✅ workflow.yaml 已生成"
else
  echo "  ⏭ workflow.yaml 已存在，跳过"
fi

# 5. AI 生成 _common-rules.md
if [ ! -f openniuma/prompts/_common-rules.md ]; then
  if [ "$USE_AI" = true ] && command -v claude >/dev/null 2>&1; then
    echo "🤖 调用 Claude 分析项目规范..."
    claude -p "分析当前项目的 CLAUDE.md 和 README，生成 openniuma/prompts/_common-rules.md。这个文件注入到 AI 编码 agent 的 prompt 中。gate_command 用 {{gate_command}} 变量。只输出文件内容。" \
      --output-format text > openniuma/prompts/_common-rules.md 2>/dev/null && {
      echo "  ✅ _common-rules.md 已由 AI 生成"
    } || {
      echo "  ⚠️ AI 生成失败，使用模板"
      [ -f "$NIUMA_DIR/prompts/_common-rules.md.template" ] && \
        cp "$NIUMA_DIR/prompts/_common-rules.md.template" openniuma/prompts/_common-rules.md
    }
  else
    echo "  ⏭ 使用默认模板"
    [ -f "$NIUMA_DIR/prompts/_common-rules.md.template" ] && \
      cp "$NIUMA_DIR/prompts/_common-rules.md.template" openniuma/prompts/_common-rules.md
  fi
else
  echo "  ⏭ _common-rules.md 已存在，跳过"
fi

# 6. .gitignore
for pattern in ".openniuma-runtime/" "openniuma/.cache/" "openniuma/.env" ".trees/"; do
  grep -qF "$pattern" .gitignore 2>/dev/null || echo "$pattern" >> .gitignore
done
echo "  ✅ .gitignore 已更新"

# 7. 依赖检查
echo ""
echo "📋 依赖检查："
python3 -c "import yaml" 2>/dev/null && echo "  ✅ PyYAML" || echo "  ⚠️ PyYAML 未安装 (pip3 install pyyaml)"
python3 -c "import textual" 2>/dev/null && echo "  ✅ textual (dashboard TUI)" || echo "  ⚠️ textual 未安装 (pip3 install textual watchfiles)"
command -v jq >/dev/null && echo "  ✅ jq" || echo "  ⚠️ jq（可选）"

echo ""
echo "✅ 初始化完成！"
echo "  bash openniuma/openniuma.sh start"
