# openNiuMa 开源拆分方案

> 讨论时间: 2026-03-31
> 状态: 方案讨论稿，待执行

---

## 1. 背景

openNiuMa 当前作为 POI（Location Scout）项目的子目录存在，目标是拆分为独立开源项目，让其他开发者 fork 或 clone 后可以直接在自己的项目中使用。

---

## 2. 开放形态分析

### 2.1 Claude Code 扩展体系

Claude Code 生态中有五种扩展分发机制:

| 形态 | 位置 | 触发方式 | 本质 | 适合场景 |
|------|------|---------|------|---------|
| **Skill** | `.claude/skills/xxx/SKILL.md` | 关键词自动匹配 | prompt 指令 | 指导 AI 怎么做事 |
| **Command** | `.claude/commands/xxx.md` | `/xxx` 手动触发 | prompt 指令 | 用户主动调用的操作 |
| **Plugin** | 插件注册表安装 | 自动 | skill + command 打包 | 分发给其他人 |
| **MCP Server** | 独立进程 | AI 调用 tool | 可执行的工具函数 | 需要跑代码的能力 |
| **Hook** | `settings.json` | 事件触发 | shell 命令 | 自动化守护 |

参考案例: OpenSpec 采用 Plugin 模式（`openspec@claude-plugins-official`），由 4 个 skill 文件 + 1 个 CLI 工具组成。

### 2.2 三种候选方案对比

#### 方案 A: 纯 Skill（最轻量）

利用 Claude Code 原生 `Agent` tool 的 `isolation: "worktree"` 替代 git worktree 管理，多 Agent 并行调用替代 worker 池，一个 skill 文件搞定。

- 优势: 零依赖，装一个 `.md` 文件就能用
- 劣势: 丢失后台运行、stall 检测、进程级隔离

#### 方案 B: Plugin + CLI（贴合生态）

Plugin 层提供交互入口（skill + command），底层引擎通过 pip/npx 独立安装。

```
openniuma@your-org
├── skills/
│   ├── niuma-add/SKILL.md
│   ├── niuma-status/SKILL.md
│   └── niuma-init/SKILL.md
├── commands/
│   ├── niuma-start.md
│   └── niuma-stop.md
└── 引擎通过 pip/npx 独立安装
```

#### 方案 C: MCP Server（最强大）

把引擎包装成 MCP server，暴露结构化 tools，AI 能直接调用。

```json
{
  "mcpServers": {
    "openniuma": {
      "command": "uvx",
      "args": ["openniuma-mcp"]
    }
  }
}
```

### 2.3 核心能力承载力对比

| 核心能力 | 纯 Skill | Plugin + CLI | MCP Server |
|---------|----------|-------------|------------|
| 多 worker 并行（独立进程） | ❌ 受限于单次对话 | ✅ | ✅ |
| git worktree 隔离 | ⚠️ 用完即弃 | ✅ 完整生命周期 | ✅ |
| stall 检测 / 孤儿回收 | ❌ 无守护进程 | ✅ | ✅ |
| 原子状态管理（文件锁） | ❌ | ✅ | ✅ |
| 失败分类 + 差异化重试 | ⚠️ 不可靠 | ✅ | ✅ |
| 后台持续运行（人可以离开） | ❌ 对话关了就没了 | ✅ | ✅ |
| TUI 实时看板 | ❌ | ✅ | ✅ |
| 跨数据库隔离（per-task DB） | ❌ | ✅ | ✅ |

### 2.4 结论

**openNiuMa 的正确开放形态是独立 CLI 工具。**

"去喝咖啡回来看结果"是核心体验，skill 无法承载——这不是 prompt 写得好不好的问题，是 Claude Code session 生命周期的限制。

类比:
- Skill / Plugin ≈ VS Code 扩展（在编辑器里跑）
- openNiuMa ≈ Jenkins / GitHub Actions（独立运行的编排系统）

---

## 3. 最终方案: 独立 CLI + 可选 Skill 集成

```
┌─────────────────────────────────────────┐
│  openniuma (pip install / brew / npx)   │  ← 独立开源项目
│  ┌───────────────────────────────────┐  │
│  │ 引擎: dev-loop.sh + lib/*.py     │  │  ← 完整功能，后台运行
│  │ CLI:  openniuma add/start/stop    │  │
│  │ TUI:  openniuma dashboard         │  │
│  └───────────────────────────────────┘  │
└─────────────────────────────────────────┘
           │ 可选集成
           ▼
┌─────────────────────────────────────────┐
│  niuma.md (Claude Code Skill)           │  ← 一个文件，增强交互
│  - 在对话中 /niuma add "xxx"            │
│  - 调用底层 CLI，展示格式化结果          │
│  - 不替代引擎，只是友好入口              │
└─────────────────────────────────────────┘
```

---

## 4. 开源拆分工作清单

### P0 — 不做就没法用

#### 4.1 清理 POI 项目专属内容

以下文件/目录需要从开源仓库中移除或加入 `.gitignore`:

| 需清理 | 说明 |
|--------|------|
| `workflow.yaml` | POI 专属配置（`poi_dev_loop_*` 数据库、monorepo hooks） |
| `prompts/_common-rules.md` | POI 的 ESM/Tailwind/移动端规范 |
| `tasks/*.md` | POI 具体任务 |
| `reviews/` | POI review 记录 |
| `logs/`、`workers/` | 运行日志和 worker 状态 |
| `state.json`、`stats.json` 及 `.lock` 文件 | 运行时状态 |
| `backlog.md` | POI 任务看板 |
| `备忘.md` | 个人备忘 |
| `.cache/` | 缓存的 workflow.json |

应提供:
- `workflow.yaml.example` — 通用示例配置
- `_common-rules.md.template` — 已有，确认内容通用

#### 4.2 添加 LICENSE 文件

README 底部标注 MIT，但缺少实际 `LICENSE` 文件。

#### 4.3 完善 `.gitignore`

```gitignore
# 运行时状态
state.json*
stats.json*
logs/
workers/
inbox/*.md
.cache/
drafts/

# Python
__pycache__/
.pytest_cache/
*.pyc

# 锁文件
*.lock

# 项目专属（用户生成）
workflow.yaml
prompts/_common-rules.md
tasks/
reviews/
backlog.md
```

#### 4.4 安装方式改进

当前是 `cp -r` 拷贝，需要支持:
- `git clone` 到子目录
- `git submodule add`
- 一行安装脚本: `curl -sSL ... | bash`（未来）
- 验证 `init.sh` 在全新项目中能独立运行

### P1 — 做了才像样

#### 4.5 CONTRIBUTING.md

- 如何贡献、提 issue、提 PR
- 开发环境搭建（跑测试、代码风格）
- 架构简述（给贡献者看的）

#### 4.6 GitHub 仓库模板

- `/.github/ISSUE_TEMPLATE/` — bug report、feature request
- `/.github/PULL_REQUEST_TEMPLATE.md`
- `/.github/workflows/ci.yml` — `python3 -m unittest discover` + lint

#### 4.7 CHANGELOG.md

从 v0.1.0 开始记录。

#### 4.8 pyproject.toml

正式管理 Python 依赖:
- 必选: 无（纯 stdlib）
- 可选: `pyyaml`、`textual`（TUI dashboard）

#### 4.9 测试可移植性验证

- 确认 `lib/test_*.py` 不依赖 POI 的 `workflow.yaml`
- 在干净环境下 `python3 -m unittest discover` 全部通过

#### 4.10 跨平台兼容性说明

已知平台差异:
- `cp -Rc`（APFS clone）在 Linux 行为不同
- `fcntl.flock` 在部分系统有差异
- macOS 通知（AppleScript）需要 Linux fallback
- README 明确标注支持的平台

### P2 — 锦上添花

#### 4.11 示例项目 / Demo

提供最小 demo 项目（简单 Express 或 Flask app），附带几个示例任务，用户 clone 后 5 分钟跑通。

#### 4.12 架构文档

`docs/architecture.md` — Worker 调度、状态机、失败分类的详细说明（配图）。

#### 4.13 配置参考文档

`workflow.yaml` 的完整字段说明（每个字段用途、默认值、可选值）。

#### 4.14 Logo

简单的 logo 和 banner 图，放在 README 顶部。

#### 4.15 Claude Code Skill 集成层

发布 `niuma.md` skill 文件，作为 Claude Code 内的友好交互入口。

---

## 5. 建议执行顺序

```
Phase 1: 拆分清理（P0: 4.1 ~ 4.4）
    → 新建独立 GitHub 仓库
    → 清理 POI 专属内容
    → 添加 LICENSE、.gitignore
    → 验证 init.sh 独立可用

Phase 2: 社区基建（P1: 4.5 ~ 4.10）
    → CONTRIBUTING.md + issue/PR 模板
    → CI pipeline
    → pyproject.toml + CHANGELOG
    → 跨平台测试

Phase 3: 体验打磨（P2: 4.11 ~ 4.15）
    → Demo 项目
    → 架构文档 + 配置参考
    → Skill 集成层
    → Logo
```
