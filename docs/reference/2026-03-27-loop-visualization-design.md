# Loop 可视化 — 设计规约

> **状态**: 已实现
> **日期**: 2026-03-27
> **关联**: autonomous-dev-loop.md

---

## 背景

自治研发循环（Dev Loop）的状态信息分散在 `loop-state.json`、`backlog.md`、`logs/`、`reviews/` 等多个文件中，需要手动读 JSON 或询问 AI 才能了解进展。缺乏一眼可见的进展视图，影响日常跟进效率和异步协作。

## 目标

提供两种互补的可视化方式：

1. **终端看板**：本地实时查看，支持自动刷新
2. **Mermaid Markdown**：可粘贴到飞书/GitLab/任何 Markdown 渲染器，满足异步分享

## 设计决策

| 决策 | 选择 | 理由 |
|------|------|------|
| 实现方式 | 纯 Shell 脚本 | 零依赖，和 dev-loop.sh 同级，不引入额外运行时 |
| JSON 解析 | jq | macOS 自带，已在 loop 环境中可用 |
| 图表格式 | Mermaid | GitLab/飞书/GitHub 原生支持渲染，无需额外服务 |
| Session 时间线 | 按日+阶段去重汇总 | 避免 VERIFY 重试风暴导致甘特图爆炸（03-26 有 13 个 VERIFY session） |
| PROGRESS.md | .gitignore 排除 | 生成物，不应纳入版本控制 |

## 文件清单

```
loop/
├── dashboard.sh          # 终端可视化看板 (新增)
├── generate-progress.sh  # PROGRESS.md 生成器 (新增)
├── PROGRESS.md           # 生成产物 (.gitignore)
├── loop-state.json       # 数据源 (已有)
├── logs/                 # 数据源 (已有)
└── reviews/              # 数据源 (已有)
```

## 工具 1：dashboard.sh — 终端看板

### 用法

```bash
bash loop/dashboard.sh           # 单次渲染
bash loop/dashboard.sh -w        # 每 5 秒自动刷新
bash loop/dashboard.sh -w 10     # 每 10 秒自动刷新
```

### 渲染区域

```
┌──────────────────────────────────────────┐
│  Header: 标题 + 分支/阶段/功能/更新时间     │
├──────────────────────────────────────────┤
│  Progress: 统计数字 + 彩色进度条            │
│  ██████████████░░░░░░░░░░  1/3            │
├──────────────────────────────────────────┤
│  Task List: 表格形式，每行一个任务           │
│  #6  优化UI                   ✅ DONE      │
│  #7  skill辅助决策指标升级     🔄 ACTIVE   │
│  #8  skill市场UI体验重构       ⏳ PENDING  │
├──────────────────────────────────────────┤
│  Pipeline: 当前任务的阶段流水线             │
│  📐 DESIGN → 🔨 IMPLEMENT → [🔍 VERIFY]  │
├──────────────────────────────────────────┤
│  Checkpoint: chunk/task/commit 细节        │
│  Retry: verify_attempts / merge_fix       │
├──────────────────────────────────────────┤
│  Recent Sessions: 最近 5 个 session        │
│  Reviews: 最近 3 个审查结论                 │
└──────────────────────────────────────────┘
```

### 颜色方案

| 元素 | 颜色 |
|------|------|
| 已完成任务 | 绿色 |
| 进行中任务 | 黄色加粗 |
| 待开发任务 | 灰色 |
| 阻塞任务 | 红色 |
| 当前阶段 | 黄色加粗方括号 |
| 已过阶段 | 绿色 |
| 未到阶段 | 灰色 |

### 阶段图标

| Phase | Icon |
|-------|------|
| INIT | 🚀 |
| DESIGN | 📐 |
| IMPLEMENT | 🔨 |
| VERIFY | 🔍 |
| FIX | 🔧 |
| MERGE | 🔀 |
| MERGE_FIX | 🩹 |
| FINALIZE | 📦 |
| FAST_TRACK | ⚡ |

## 工具 2：generate-progress.sh — Mermaid Markdown

### 用法

```bash
bash loop/generate-progress.sh
# → loop/PROGRESS.md
```

### 输出结构

#### 2.1 概览表格

概览信息以 Markdown 表格呈现：集成分支、当前阶段（带 emoji）、当前任务、总进度统计、更新时间。

附带文本进度条：`██████░░░░░░░░░░░░░░ 33% (1/3)`

#### 2.2 Mermaid: 任务状态图

```mermaid
graph LR
  T6["#6 优化UI"]:::done
  T7["#7 skill辅助决策指标升级"]:::active
  T8["#8 skill市场UI体验重构"]:::pending
  T6 --> T7 --> T8

  classDef done fill:#d4edda,stroke:#28a745,color:#155724
  classDef active fill:#fff3cd,stroke:#ffc107,color:#856404
  classDef pending fill:#e2e3e5,stroke:#6c757d,color:#383d41
  classDef blocked fill:#f8d7da,stroke:#dc3545,color:#721c24
```

四种状态样式：done（绿）、active（黄）、pending（灰）、blocked（红）。

#### 2.3 Mermaid: 当前任务阶段流水线

```mermaid
graph LR
  D["📐 DESIGN"] --> I["🔨 IMPLEMENT"] --> V["🔍 VERIFY"] --> M["🔀 MERGE"]
```

根据 `current_phase` 自动高亮：当前阶段黄色加粗边框，已过阶段绿色，未到阶段默认灰。

#### 2.4 实施进度表

当 `implement_progress.current_chunk > 0` 时展示：当前 Chunk、Task、最后提交的 Task + commit SHA、Verify/Merge Fix 重试次数。

#### 2.5 任务清单表

全量任务列表：ID、名称、复杂度、状态（emoji）、创建时间、完成时间。按 completed → blocked → queue 顺序排列。

#### 2.6 Session 按日汇总表

| 日期 | 🚀 INIT | 📐 DESIGN | 🔨 IMPL | 🔍 VERIFY | 🔧 FIX | 🔀 MERGE | 总计 |
|------|---------|-----------|---------|-----------|--------|----------|------|

通过 `find` 按日期+阶段计数，避免 `ls` glob 在 `set -e` 下的展开问题。

#### 2.7 Mermaid: Session 甘特时间线

按 `日期-阶段` 去重聚合，每个组合只保留首次 session 的时间戳，渲染为 30 分钟时间块。用 `awk` 聚合去重，section 按阶段分组且仅在阶段变化时输出。

#### 2.8 审查记录表

从 `reviews/*.md` 中提取 `Verdict` 行，映射为 PASS/FAIL/Pending 状态。

## 数据流

```
loop-state.json ──┐
logs/*.log ───────┤──→ dashboard.sh ──→ 终端彩色输出
reviews/*.md ─────┤
                  └──→ generate-progress.sh ──→ PROGRESS.md (Mermaid)
```

两个脚本共享相同的数据源，独立运行，无状态副作用。

## 集成点

### 与 dev-loop.sh 的集成（可选，未实现）

可在 `dev-loop.sh` 每个 phase 结束后自动调用：

```bash
bash loop/generate-progress.sh
```

实现每次状态变更自动更新 PROGRESS.md。

### 与飞书的集成（可选，未实现）

可通过飞书 MCP 将 PROGRESS.md 内容同步到飞书文档，团队成员无需访问终端。

## 技术细节

- **依赖**: bash 4+、jq 1.7+（macOS 自带）
- **兼容性**: macOS (zsh/bash)、Linux
- **错误处理**: `set -euo pipefail`，STATE_FILE 不存在时报错退出
- **性能**: 单次渲染 < 1 秒（32 个 log 文件）
- **.gitignore**: `loop/PROGRESS.md` 已加入排除
