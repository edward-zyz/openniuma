# QA-3 评审：可移植性与兼容性

> 评审对象：openNiuMa 合并升级设计 — 第 8 章可移植性设计 + init.sh + detect.py
> 评审角色：资深 AI 编排系统测试工程师，专精跨平台/跨项目可移植性
> 评审日期：2026-03-27

---

## 总览

设计方案在可移植性上迈出了关键一步：将 30+ 处硬编码抽象为 workflow.yaml + _common-rules.md 的引擎/配置分离架构，并通过 init.sh 三层探测策略实现"一条命令初始化"。整体方向正确，但在跨平台兼容性、探测逻辑鲁棒性、边界场景处理上存在若干需要修复的问题。

---

## 🔴 严重（必须修复，否则在部分环境下无法工作）

### S-01: `sed -i ''` 是 macOS 专属语法，Linux 下直接报错

**问题描述：**
设计中多处使用 `sed -i '' "s|...|...|g"`，这是 BSD sed (macOS) 的语法。GNU sed (Linux/WSL) 的 `-i` 不接受空字符串参数，会将 `''` 视为备份后缀，导致命令行为完全不同——不会原地修改目标文件，而是创建一个名为 `''` 后缀的备份文件。

**影响场景：**
- Linux 服务器上运行 init.sh 或 worktree hooks
- WSL (Windows Subsystem for Linux) 环境
- Docker 容器中运行（绝大多数基于 Linux）
- CI 环境（GitHub Actions runner 默认 Ubuntu）

**涉及位置：**
- workflow.yaml hooks.after_create 中 `.env` 修改
- detect.py 生成的 after_create hook 中的 sed 命令

**建议修改：**
使用跨平台兼容的 sed 封装，或改用 Python 替代 sed：
```bash
# 方案 A：跨平台 sed 封装
portable_sed_i() {
  if sed --version 2>/dev/null | grep -q GNU; then
    sed -i "$@"
  else
    sed -i '' "$@"
  fi
}

# 方案 B（推荐）：直接用 Python 替代，反正已有 Python 依赖
python3 -c "
import re, sys
with open(sys.argv[1], 'r') as f: content = f.read()
content = content.replace(sys.argv[2], sys.argv[3])
with open(sys.argv[1], 'w') as f: f.write(content)
" "$env_file" "/$db_prefix" "/$DB_NAME"
```

### S-02: `cp -Rc`（APFS clone）是 macOS 专属，Linux 无 `-c` 选项

**问题描述：**
detect.py 中 Node.js monorepo 的 APFS clone 优化使用 `cp -Rc`，其中 `-c` 是 macOS 独有的 clone flag（利用 APFS copy-on-write）。Linux 的 coreutils `cp` 不识别 `-c` 参数，会直接报错。

**影响场景：**
- 所有 Linux 环境
- 非 APFS 文件系统的 macOS（如外接 ExFAT/HFS+ 磁盘）

**涉及位置：**
- detect.py `_detect_node()` 生成的 after_create hook

**建议修改：**
```bash
# 检测 APFS clone 能力，有则用，无则普通复制
if cp -Rc --help 2>&1 | grep -q clone 2>/dev/null || [[ "$(uname)" == "Darwin" ]]; then
  cp -Rc "$MAIN_REPO/node_modules" node_modules 2>/dev/null || cp -R "$MAIN_REPO/node_modules" node_modules 2>/dev/null || true
else
  cp -R "$MAIN_REPO/node_modules" node_modules 2>/dev/null || true
fi
```
或者在 detect.py 中根据 `sys.platform` 生成不同的 hook 脚本。

### S-03: Python 3.9+ 假设"macOS 自带"已不成立

**问题描述：**
设计文档（第 10 节）声称"Python 3.9+（macOS 自带）"，但：
- macOS 12 (Monterey) 起已移除系统自带 Python 2
- macOS 从未自带 Python 3（需要安装 Xcode Command Line Tools 或 Homebrew）
- Xcode CLT 安装的 Python 版本随 macOS 版本变化，不保证 3.9+
- 部分用户的 `python3` 可能指向 Conda/pyenv 管理的版本

**影响场景：**
- 全新 macOS 安装（未装 Homebrew/Xcode CLT）
- 使用 pyenv/conda 管理 Python 的开发者，`python3` 可能不在 PATH 或版本不对

**建议修改：**
init.sh 开头增加 Python 版本检查：
```bash
# 前置检查
if ! command -v python3 >/dev/null 2>&1; then
  echo "❌ 未找到 python3，请先安装：brew install python3"
  exit 1
fi

PY_VERSION=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
if python3 -c "import sys; exit(0 if sys.version_info >= (3, 9) else 1)" 2>/dev/null; then
  echo "  ✅ Python $PY_VERSION"
else
  echo "❌ 需要 Python 3.9+，当前版本: $PY_VERSION"
  exit 1
fi
```

### S-04: PyYAML 不是标准库，`pip3 install` 在系统 Python 上可能被拒绝

**问题描述：**
设计中 init.sh 会自动 `pip3 install pyyaml`，但：
- macOS Sonoma (14+) 和许多 Linux 发行版使用 PEP 668 (`EXTERNALLY-MANAGED`)，系统 Python 禁止 `pip install`
- 用户可能没有 pip3（只装了 python3）
- Homebrew 安装的 Python 也可能触发 PEP 668 限制

**影响场景：**
- macOS Sonoma+ 用户
- Ubuntu 23.04+、Fedora 38+、Arch Linux 等现代 Linux 发行版
- 任何使用 `EXTERNALLY-MANAGED` 标记的 Python 环境

**建议修改：**
```bash
# 方案 A（推荐）：使用 pipx 或 --break-system-packages
python3 -c "import yaml" 2>/dev/null || {
  echo "  📦 安装 PyYAML..."
  pip3 install pyyaml --user -q 2>/dev/null || \
  pip3 install pyyaml --break-system-packages -q 2>/dev/null || \
  pipx install pyyaml -q 2>/dev/null || {
    echo "  ❌ 自动安装 PyYAML 失败"
    echo "  请手动安装: brew install pyyaml 或 pip3 install --user pyyaml"
    exit 1
  }
}

# 方案 B（更好）：考虑用 Python 内置 JSON 替代 YAML
# 如果 workflow.yaml 改为 workflow.json，则完全不需要 PyYAML
# 或者使用一个纯 Python 的轻量 YAML 解析器内嵌到 lib/ 中
```

---

## 🟡 中等（不会阻塞运行，但会在特定场景下产生问题）

### M-01: CI 配置探测只覆盖了 GitHub Actions 的三个文件名

**问题描述：**
`_detect_from_ci()` 只检查 `ci.yml`, `test.yml`, `check.yml` 三个固定文件名。实际项目中 GitHub Actions 文件名高度自定义，常见的还有 `main.yml`, `build.yml`, `pr.yml`, `lint.yml`, `validate.yml` 等。而且完全忽略了 GitLab CI（`.gitlab-ci.yml` 在注释中提到但未实现）。

**影响场景：**
- 使用非标准命名 CI 文件的项目（非常普遍）
- GitLab 托管的项目
- 使用 CircleCI、Jenkins 等其他 CI 的项目

**建议修改：**
```python
def _detect_from_ci(repo_dir: str) -> str:
    # GitHub Actions: 扫描所有 workflow 文件
    gh_dir = os.path.join(repo_dir, ".github", "workflows")
    if os.path.isdir(gh_dir):
        for f in sorted(os.listdir(gh_dir)):
            if f.endswith((".yml", ".yaml")):
                result = _parse_github_actions(os.path.join(gh_dir, f))
                if result:
                    return result

    # GitLab CI
    gitlab_ci = os.path.join(repo_dir, ".gitlab-ci.yml")
    if os.path.exists(gitlab_ci):
        return _parse_gitlab_ci(gitlab_ci)

    # Makefile (make test)
    makefile = os.path.join(repo_dir, "Makefile")
    if os.path.exists(makefile):
        return _parse_makefile(makefile)

    return ""
```

### M-02: GitHub Actions 解析逻辑会错误收集无关命令

**问题描述：**
`_parse_github_actions()` 遍历所有 job 的所有 step，只要 `run` 字段包含 `test/lint/build/check/tsc` 关键词就收集。这会导致：
- 部署相关的 `npm run build` 被收集（本意是构建镜像/部署包，非门禁）
- `echo "Running tests..."` 这种注释性命令被收集
- 多个 job 的命令被无差别拼接（如 `deploy` job 中的 build）
- 环境设置命令如 `actions/setup-node` 后的 `npm ci` 被遗漏但 `npm test` 被收集（依赖未安装）

**影响场景：**
- 有 deploy workflow 同时包含 build 步骤的项目
- CI 中有条件执行（`if:` 条件的 step）的项目
- 多 job 并行的 CI 配置

**建议修改：**
- 优先提取名为 `test`/`lint`/`check`/`ci` 的 job（通过 job name 过滤）
- 忽略 `deploy`/`release`/`publish` 相关 job
- 对提取结果去重
- 考虑只提取第一个匹配 job 的命令，而非全部拼接

### M-03: `git symbolic-ref refs/remotes/origin/HEAD` 在 clone 后未 fetch 的仓库上可能失败

**问题描述：**
`refs/remotes/origin/HEAD` 只在 `git clone` 时自动设置。如果用户是通过其他方式获取仓库（如 `git init` + `git remote add` + `git fetch`），该引用不存在。

**影响场景：**
- 手动初始化的仓库
- 部分 CI 环境的 shallow clone
- `git worktree add` 创建的目录内运行 init.sh

**建议修改：**
```bash
MAIN_BRANCH=$(git symbolic-ref refs/remotes/origin/HEAD 2>/dev/null | sed 's|refs/remotes/origin/||') || \
MAIN_BRANCH=$(git remote show origin 2>/dev/null | grep 'HEAD branch' | awk '{print $NF}') || \
MAIN_BRANCH=$(git branch -r 2>/dev/null | grep -E 'origin/(main|master)$' | head -1 | sed 's|.*origin/||') || \
MAIN_BRANCH="main"
```

### M-04: `timeout` 命令在 macOS 上需要 GNU coreutils

**问题描述：**
`ensure_worktree()` 中使用 `timeout "${timeout:-120}" bash -c "$hook"` 来限制 hook 执行时间。但 macOS 没有自带 `timeout` 命令，需要 `brew install coreutils` 后通过 `gtimeout` 使用。

**影响场景：**
- 未安装 GNU coreutils 的 macOS 用户（非常普遍）

**建议修改：**
```bash
# 跨平台 timeout 封装
run_with_timeout() {
  local secs="$1"; shift
  if command -v timeout >/dev/null 2>&1; then
    timeout "$secs" "$@"
  elif command -v gtimeout >/dev/null 2>&1; then
    gtimeout "$secs" "$@"
  else
    # 无 timeout 命令时用 background + wait 模拟
    "$@" &
    local pid=$!
    ( sleep "$secs" && kill "$pid" 2>/dev/null ) &
    local watcher=$!
    wait "$pid" 2>/dev/null
    local ret=$?
    kill "$watcher" 2>/dev/null 2>&1; wait "$watcher" 2>/dev/null
    return "$ret"
  fi
}
```

### M-05: detect.py 数据库检测的正则过于简单，会误匹配

**问题描述：**
`re.search(r"postgresql://[^/]+/(\w+)", env_content)` 只捕获第一个匹配的数据库名。如果 .env.example 有多个 DATABASE_URL（如测试库和开发库），或者 URL 包含查询参数（`?sslmode=require`），可能获取到错误的库名。另外 `(\w+)` 不匹配含连字符的库名（如 `my-app-dev`）。

**影响场景：**
- 库名包含连字符的项目
- .env.example 中有多个 DATABASE_URL 的项目
- URL 格式为 `postgres://`（非 `postgresql://`）的项目

**建议修改：**
```python
# 支持 postgres:// 和 postgresql://，库名支持连字符
m = re.search(r"DATABASE_URL=.*?postgre(?:sql)?://[^/]+/([\w-]+)", env_content)
```

### M-06: workflow.yaml hooks 中的 shell 脚本作为 YAML 多行字符串，缩进敏感

**问题描述：**
hooks.after_create 是 YAML 的 `|` 块标量（block scalar），对缩进极度敏感。detect.py 用 `"\n".join(clone_lines)` 拼接后直接输出为 JSON 字符串，由 config.py 写入 YAML。如果缩进处理不当，YAML 解析会截断脚本内容，或将其解析为非字符串类型。

**影响场景：**
- detect.py 生成的多行 hook 脚本在序列化为 YAML 时缩进错误
- 用户手动编辑 workflow.yaml 时不小心改了缩进

**建议修改：**
- config.py 的 `generate-workflow` 命令在写入 YAML 时使用 `yaml.dump()` 的 `default_flow_style=False` 和明确的 `width` 参数
- 对 hook 字段强制使用 literal block scalar（`|`）
- 增加 workflow.yaml 格式校验：init.sh 生成后立即 `python3 -c "import yaml; yaml.safe_load(open('loop/workflow.yaml'))"` 验证

### M-07: `createdb` / `dropdb` 假设 PostgreSQL 客户端已安装且当前用户有权限

**问题描述：**
hooks 中直接调用 `createdb`/`dropdb`，假设：
1. PostgreSQL 客户端工具已安装
2. 当前 OS 用户有创建数据库的权限
3. PostgreSQL 服务正在运行

这三个假设在非 POI 项目（如纯前端项目、使用 SQLite/MongoDB 的项目）上都不成立。

**影响场景：**
- detect.py 为不需要 PostgreSQL 的项目错误生成了数据库 hook（仅因 .env.example 中有 DATABASE_URL）
- 用户的 PostgreSQL 使用了密码认证（非 peer/trust）

**建议修改：**
- createdb 前检查 `command -v createdb` 和 `pg_isready`
- 为 hook 脚本添加优雅降级：
```bash
if command -v createdb >/dev/null 2>&1 && pg_isready -q 2>/dev/null; then
  createdb "$DB_NAME" 2>/dev/null || true
else
  echo "⚠️ PostgreSQL 不可用，跳过数据库创建"
fi
```

### M-08: Layer 2 AI 生成的 _common-rules.md 内容不可控

**问题描述：**
通过 `claude -p` 生成 _common-rules.md，但：
- 输出格式不稳定（AI 可能添加解释文字、Markdown 代码块包裹等）
- 可能输出与模板格式不一致的内容（缺少 `{{gate_command}}` 变量引用）
- 无验证步骤确认生成的文件格式正确
- `--output-format text` 可能包含前后空行或额外信息

**影响场景：**
- AI 生成的 _common-rules.md 缺少 `{{gate_command}}` → prompt 渲染时 gate 命令丢失
- AI 输出被 Markdown 代码块包裹 → 模板变量 `{{var}}` 不会被替换

**建议修改：**
- AI 生成后增加基本格式验证：
```bash
# 验证关键变量引用存在
if ! grep -q '{{gate_command}}' loop/prompts/_common-rules.md; then
  echo "  ⚠️ AI 生成的 _common-rules.md 缺少 {{gate_command}}，回退到模板"
  cp "${LOOP_DIR}/prompts/_common-rules.md.template" loop/prompts/_common-rules.md
fi
```
- 考虑给 AI 一个更严格的输出模式（如 JSON schema 约束）

---

## 🟢 建议（可选改进，提升体验和健壮性）

### L-01: 技术栈探测应支持多技术栈项目

**问题描述：**
detect.py 使用 `if/elif` 链，只识别第一个匹配的技术栈。但现实中很多项目是混合栈：Node.js 前端 + Python/Go 后端，或者 Rust + TypeScript WASM 项目。

**建议修改：**
- 使用优先级列表而非互斥 if/elif
- gate_command 合并多栈的门禁命令
- 或者至少在日志中提示"检测到多个技术栈文件，使用 [X] 作为主栈"

### L-02: init.sh 应该是幂等的

**问题描述：**
设计中 `workflow.yaml` 和 `_common-rules.md` 都有"已存在则跳过"的逻辑，这很好。但 `.gitignore` 更新和目录创建每次都执行。建议明确声明幂等性保证，并确保重复运行不会产生副作用（如 `.gitignore` 中不会出现重复条目——当前 `grep -qF` 检查已保证，这里确认设计是正确的）。

### L-03: detect.py 应有 `--dry-run` 模式

**建议修改：**
添加 `--dry-run` 参数，只输出探测结果不写入文件，方便用户在 init 前预览：
```bash
python3 loop/lib/detect.py --dry-run .
# 输出：检测到 Node.js monorepo，gate: npm run lint && npm test && ...
```

### L-04: Prompt 模板的 `{{var}}` 语法与 Mustache/Jinja 冲突

**问题描述：**
如果项目代码中涉及 Go template（`{{.}}`）、Ansible/Jinja（`{{ var }}`）或 Mustache，`_common-rules.md` 中引用这些语法的示例代码会被 config.py 的模板引擎误替换或报"未知变量"错误。

**建议修改：**
- 使用更独特的分隔符，如 `<<var>>` 或 `${var}`
- 或者实现转义语法（如 `\{\{literal\}\}`）
- 在文档中明确说明此限制

### L-05: 考虑支持 `.tool-versions` / `.nvmrc` / `.python-version` 作为探测信号

**问题描述：**
许多项目使用 asdf/mise/nvm/pyenv 的版本文件来声明运行时版本。这些文件可以补充技术栈探测的置信度，同时可以在 after_create hook 中自动切换到正确的运行时版本。

**建议修改：**
```python
# 在 after_create hook 中注入版本管理器支持
if os.path.exists(os.path.join(repo_dir, ".tool-versions")):
    after_create = "command -v mise >/dev/null && mise install || true\n" + after_create
elif os.path.exists(os.path.join(repo_dir, ".nvmrc")):
    after_create = "command -v nvm >/dev/null && nvm use || true\n" + after_create
```

### L-06: `jq 1.7+（macOS 自带）` 描述不准确

**问题描述：**
第 10 节依赖中写"jq 1.7+（macOS 自带）"，但 macOS 不自带 jq。需要通过 Homebrew 安装。

**建议修改：**
修正为"jq 1.7+（需手动安装：`brew install jq`）"，并将 jq 依赖降级为可选（dashboard.sh 辅助渲染）。

### L-07: detect.py 中 Python 项目的 pyproject.toml 解析过于粗糙

**问题描述：**
当前用 `"pytest" in content` 做字符串匹配，无法区分 `pytest` 是作为依赖还是仅在注释/文档中提到。如果 pyproject.toml 中有 `# We used to use pytest but switched to unittest`，就会误判。

**建议修改：**
使用 `tomllib`（Python 3.11+ 内置）或 `tomli` 库正确解析 TOML 结构，从 `[project.dependencies]`、`[project.optional-dependencies]` 或 `[tool.pytest]` 等具体字段判断。考虑到最低 Python 3.9 的要求，可以 try/except 降级到字符串匹配：
```python
try:
    import tomllib  # Python 3.11+
except ImportError:
    import tomli as tomllib  # fallback
```

### L-08: 移植流程缺少"验证"步骤

**建议修改：**
init.sh 末尾增加一个可选的 smoke test：
```bash
echo "🔍 验证配置..."
python3 loop/lib/config.py validate loop/workflow.yaml && echo "  ✅ workflow.yaml 格式正确" || echo "  ❌ workflow.yaml 格式错误"
python3 loop/lib/config.py render-prompt fast-track >/dev/null 2>&1 && echo "  ✅ Prompt 渲染正常" || echo "  ⚠️ Prompt 渲染失败（可能缺少变量）"
```

---

## 总体评价

**评分：7.5 / 10**

**亮点：**
1. **引擎/配置分离的架构决策是正确的**——workflow.yaml + _common-rules.md 的分层设计清晰，既满足零配置的易用性需求，又保留了深度定制的灵活性
2. **三层探测策略（确定性 → AI → 兜底）设计合理**——渐进增强，每层都有降级路径
3. **init.sh 的幂等设计**——已存在的文件跳过，重复运行安全
4. **detect.py 的探测矩阵覆盖了主流技术栈**——Node.js/Go/Rust/Python/Ruby/Java

**核心风险：**
1. **跨平台兼容性是最大短板**——`sed -i ''`、`cp -Rc`、`timeout` 三个 macOS 专属命令使得方案在 Linux 上直接不可用，与"可移植"目标矛盾（🔴 S-01, S-02, 🟡 M-04）
2. **Python 环境假设过于乐观**——系统 Python 可用性、PyYAML 安装限制在现代 OS 上已是常见问题（🔴 S-03, S-04）
3. **CI 探测逻辑偏脆弱**——固定文件名、无差别收集命令的策略在真实项目中容易产生错误的 gate_command（🟡 M-01, M-02）

**建议优先级：**
1. 先解决 4 个 🔴 严重问题——这些会导致非 macOS 环境或现代 macOS 下运行失败
2. 再处理 M-01 ~ M-04——提升真实项目中的探测准确度
3. 其余 🟡 和 🟢 可以在后续迭代中逐步改进

**一句话总结：** 架构设计方向正确，但当前实现存在"macOS-only"倾向，需在跨平台兼容性上补课后才能真正称为"拷贝即用"。
