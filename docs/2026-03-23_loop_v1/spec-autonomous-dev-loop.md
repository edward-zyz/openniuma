# Autonomous Dev Loop — 设计规约

> 一套让 AI Agent 在无人值守下逐条消化 backlog、自主完成设计-实现-审查-合并全流程的编排系统。

---

## 1. 要解决的问题

Claude Code 单会话有上下文窗口限制，且缺乏"跨会话记忆"。如果把整个 backlog 交给一个会话，要么上下文爆掉，要么后半段质量崩塌。同时，AI 自己写完自己审不一定靠谱——同一个会话里"实现者"和"审查者"共享上下文，天然容易放水。

核心矛盾：**如何让 AI 在无人值守下持续交付高质量代码？**

---

## 2. 核心理念（10 条设计原则）

### 2.1 一个功能 = 一个会话周期

不在单会话里塞多个功能。每个功能走完整个 Phase 链后，下一个功能在新会话中开始。避免上下文耗尽导致后期质量退化。

### 2.2 角色隔离靠会话边界，不靠 prompt 前缀

设计者（DESIGN）、实现者（IMPLEMENT）、审查者（VERIFY）是不同的 Claude Code 会话。审查者看不到实现过程中的上下文，天然无法"放水"——这比在同一个会话里用 system prompt 说"你现在是审查者"可靠得多。

### 2.3 质量靠自动化门禁，不靠 Agent 自觉

`npm run lint && npm test && npm run build && npx tsc --noEmit` 是硬性关卡。exit code 非 0 就不能往下走，不存在"差不多就行"的灰色地带。AI 可以偷懒不写测试，但门禁不可能骗过去。

### 2.4 状态持久化到文件 + 启动时校验

`loop-state.json` 是全局真相源（source of truth）。每个会话启动时先读取并校验状态一致性，结束时更新并保存。新会话不需要知道上一个会话做了什么——看文件就够了。

### 2.5 dev 分支做集成缓冲

功能分支合入 dev 而非直接进 master。多个功能在 dev 上累积验证，尽早暴露隐性冲突。最终每个功能独立创建 feat→master PR，squash merge 后 master 历史干净可回滚。

### 2.6 master 只读（AI 的硬性红线）

AI 全程不触碰 master。禁止 `git merge ... master`、`git push origin master`、`gh pr merge`。最终产出是等待人工审核的 PR，不是已合并的代码。这是 prompt 层面和 GitHub 分支保护双重保障。

### 2.7 环境切换必须完整

切分支后必须 `npm install`，确保 `node_modules` 与当前分支的 `package.json` / lock 一致。这条看似琐碎，但历史上因此踩坑多次。

### 2.8 按复杂度分流

不是所有任务都需要走完整的 4 会话流程。低复杂度任务走快速通道（3 会话），中复杂度合并设计+实现（3 会话），高复杂度才走完整流程（4 会话）。复杂度由 AI 在接手时根据客观标准动态评估。

### 2.9 会话衔接靠外部编排，不靠人工粘贴

`dev-loop.sh` 是一个 bash 编排脚本，负责读取状态、选择 prompt、调用 `claude -p`、检测异常。所有实际工作由 claude 会话内部完成，脚本本身不参与任何 Git 操作或代码修改。

### 2.10 状态更新优先于收尾操作

每个 Phase 完成核心决策后，先更新 `loop-state.json` 的 phase 字段，再执行 git commit/push 等收尾操作。即使会话在收尾时中断，编排脚本也能检测到 phase 已推进，下一个会话可以补做未完成的收尾。

---

## 3. 系统架构

### 3.1 两层架构

```
┌─────────────────────────────────────────┐
│         编排层 (dev-loop.sh)             │
│  bash 脚本，纯控制流，不修改代码         │
│  - 读 loop-state.json → 选 prompt       │
│  - 调 claude -p → 等完成                 │
│  - 检测 phase 变化 → 成功/失败判定       │
│  - inbox 轮询 → 热插入新任务            │
│  - 连续失败安全阀 → 停止循环            │
└──────────────┬──────────────────────────┘
               │  claude -p "$prompt"
               ▼
┌─────────────────────────────────────────┐
│         执行层 (Claude Code 会话)        │
│  每个会话 = 一个 Phase 的全部工作        │
│  - 读文件、写代码、跑测试、git 操作      │
│  - 更新 loop-state.json                 │
│  - 完全自主，不请求人工输入             │
└─────────────────────────────────────────┘
```

### 3.2 分支策略

```
master (受保护，只接受 PR squash merge)
  │
  └── dev/backlog-batch-{date} (集成分支)
        │
        ├── feat/task-a   ── merge --no-ff → dev → CI 通过 → 下一个
        ├── feat/task-b   ── merge --no-ff → dev → CI 通过 → 下一个
        └── ...

FINALIZE 阶段：feat rebase master → 独立 PR → 人工 squash merge
```

- 功能分支从 dev 最新代码拉出（包含前序功能）
- 合入 dev 后必须跑 CI，全部通过才能开始下一个功能
- dev 分支上不直接写代码，只接受 feat/* 的 merge

### 3.3 Worktree 隔离

编排脚本为需要代码修改的 Phase 创建独立的 git worktree：

- 隔离目录：`.trees/loop-{slug}`
- 使用 `--detach` 创建（detached HEAD 基于 dev 分支），避免多 worktree 检出同一分支的冲突
- 独立数据库：`poi_dev_loop_{slug}`
- 独立 `.env`：DATABASE_URL 指向独立数据库
- 依赖初始化：比较 `package-lock.json`，一致时用 APFS clone（`cp -Rc`，~6s），不一致时才 `npm install`
- 功能完成后自动清理 worktree + 数据库

### 3.4 并行执行（多 Worker 模式）

支持多个任务同时执行，通过 `--workers N` 参数控制并发数（默认 5）：

```
┌──────────────────────────────────────────┐
│          调度器 (parallel_main)            │
│  - 扫描 queue，找出所有可并行任务          │
│  - 依赖检查：pending + depends_on 已满足   │
│  - 原子认领：mkdir 锁防止重复分配          │
│  - 监控 worker PID，回收完成/失败的        │
│  - 同步结果回主 state + BLOCKED 传播       │
└───┬──────────┬──────────┬────────────────┘
    │          │          │
    ▼          ▼          ▼
 Worker#1   Worker#2   Worker#3
 (task A)   (task B)   (task C)
  独立        独立        独立
  worktree   worktree   worktree
  state      state      state
  数据库      数据库      数据库
```

**关键设计：**

- **Worker 隔离**：每个 worker 通过 `--single-task N` 参数以子进程方式运行，拥有独立的 state 文件（`loop/workers/{id}/state.json`）、worktree 和数据库
- **依赖感知调度**：只启动 `depends_on` 全部已完成的任务，有依赖的任务等待前序完成后自动解锁
- **原子认领**：通过 `mkdir` 锁（跨平台兼容 bash 3.x）保护 state 文件的并发读写
- **结果同步**：worker 完成后，调度器将结果写回主 `loop-state.json`，并执行 BLOCKED 状态级联传播
- **容错**：worker 异常退出时任务重置为 pending，允许后续重试；残留 PID 文件在启动时自动清理

---

## 4. Phase 流转（状态机）

### 4.1 三条路径

| 复杂度 | 路径 | 会话数 | 评估标准 |
|--------|------|--------|----------|
| 低 | FAST_TRACK → VERIFY → MERGE | 3 | ≤5 文件、无 DB migration、无新 API、需求 ≤3 行 |
| 中 | DESIGN_IMPLEMENT → VERIFY → MERGE | 3 | 不满足"低"也不满足"高"的其余情况 |
| 高 | DESIGN → IMPLEMENT → VERIFY → MERGE | 4 | DB migration + 新 API + 新页面，或需求 >20 行 |

复杂度由首个接手会话在读取需求并初步探索代码后动态评估，如评估结果与当前 phase 不匹配，立即修正路径并结束会话。

### 4.2 完整状态机

```
INIT ──→ [按复杂度分流]
           │
           ├── FAST_TRACK ─────────────┐
           ├── DESIGN_IMPLEMENT ───────┤
           └── DESIGN → IMPLEMENT ─────┤
                                       ▼
                                    VERIFY
                                     │   │
                                  PASS   FAIL (≤3次)
                                     │   │
                                     │   └── FIX → VERIFY (循环)
                                     ▼         (≥3次 → BLOCKED → 下一条)
                                   MERGE
                                     │   │
                                  CI通过  CI失败 (≤3次)
                                     │   │
                                     │   └── MERGE_FIX → MERGE (循环)
                                     ▼         (≥3次 → BLOCKED → 下一条)
                               [下一条 pending]
                                     │
                                  有 → 回到分流
                                  无 → FINALIZE → CI_FIX → AWAITING_HUMAN_REVIEW
                                                              │
                                                        [inbox 有新任务]
                                                              │
                                                        回到分流继续
```

### 4.3 各 Phase 职责

| Phase | 角色 | 核心职责 | 产出物 |
|-------|------|----------|--------|
| INIT | 初始化器 | 创建 dev 分支，校验分支保护 | loop-state.json |
| FAST_TRACK | 架构师+开发者 | 低复杂度任务一站式完成 | 代码 + commit message |
| DESIGN_IMPLEMENT | 架构师+开发者 | 中复杂度任务：精简 spec → TDD 实现 | spec + 代码 |
| DESIGN | 架构师 | 高复杂度任务深度设计 | spec + plan |
| IMPLEMENT | 开发者 | 严格按 plan 逐步 TDD 实现 | 代码 + 勾选 plan |
| VERIFY | 审查者 | 独立视角审查：门禁 + 规范扫描 + diff 审查 | review 文件 |
| FIX | 修复者 | 只修 Critical/Major，不做额外改动 | 修复 commit |
| MERGE | 集成者 | feat merge 到 dev + 跑 CI + 推进队列 | dev 分支更新 |
| MERGE_FIX | 集成修复者 | 修复 dev 上的 CI 失败 | 修复 commit |
| FINALIZE | 发布经理 | 逐功能 rebase master + 创建 PR | PR(不合并) |
| CI_FIX | CI 修复者 | 修复 PR 的 GitHub CI 失败 | 修复 commit |

---

## 5. 关键机制

### 5.1 状态文件 (loop-state.json)

不纳入 Git 跟踪（`.gitignore`），避免多分支合并冲突。核心字段：

- `dev_branch` — 本轮集成分支名
- `current_item_id` + `current_phase` — 当前任务和阶段
- `branch` / `spec_path` / `plan_path` — 当前工作产出物引用
- `implement_progress` — 细粒度断点（Chunk/Task/commit SHA）
- `verify_attempts` / `merge_fix_attempts` — 重试计数器
- `queue` — 任务队列（含 status, depends_on, complexity）
- `completed` / `blocked` — 完成/阻塞记录
- `lock` — 并发保护（session_id + acquired_at，30 分钟过期）

### 5.2 Inbox 热插入

循环运行中可随时向 `loop/inbox/` 投入 `.md` 任务文件：

```markdown
---
name: 功能名称
complexity: 中
depends_on: []
---
需求描述...
```

编排脚本每轮会话前自动扫描、分配 ID、移动到 `tasks/`、写入 queue、生成 backlog.md。创建 `inbox/STOP` 文件可优雅停止循环。

### 5.3 断点续传

**会话级**：Phase 未推进时，编排脚本在下一轮 prompt 前追加断点续传提示，要求新会话检查已有产出物再决定从哪里继续。

**Task 级**：`implement_progress` 记录最后一个已 commit 的 Task 和 SHA。新会话通过 `git log` 验证 SHA 存在，从下一个 Task 继续，不重做已完成的工作。

**上下文熔断**：当会话上下文消耗接近阈值时，立即保存进度 + commit + push，结束会话交给下一轮接续。

### 5.4 自动化门禁

每个 Task 完成后、每次 Phase 结束前，必须通过四项门禁（全部 exit 0）：

```bash
npm run lint           # ESLint
npm test               # 全量测试
npm run build          # 前后端构建
npx tsc --noEmit -p frontend  # 前端类型检查
```

VERIFY 阶段额外执行增量代码规范扫描：硬编码颜色、any 类型、emoji 图标、原生 HTML 组件、新建 CSS 文件。

### 5.5 安全阀与异常处理

| 机制 | 触发条件 | 行为 |
|------|----------|------|
| 连续失败停止 | 3 次 Phase 未推进 | 停止循环，等待人工排查 |
| VERIFY 熔断 | 3 次审查不通过 | 标记 BLOCKED，跳下一条 |
| MERGE_FIX 熔断 | 3 次 CI 修不好 | 标记 BLOCKED，dev reset 到备份 tag |
| STUCK 标记 | 同一步骤失败 5 次 | 跳过并级联标记依赖链 |
| API 限流处理 | 检测到 rate limit | 计算重置时间，长等待后继续（不消耗重试次数）|
| 权限阻塞检测 | 检测到 permission denied | 立即停止循环（不可重试）|
| 残留 worktree 清理 | 启动时发现非当前任务的 worktree | 自动清理 |
| 状态文件自检 | `loop-state.json` 不存在 | 从 `tasks/` 目录自动扫描重建 |
| 残留 worker 清理 | 并行模式启动时发现上次的 PID 文件 | 清理 workers/ 目录 |

### 5.6 依赖管理

- 任务间通过 `depends_on` 声明依赖（整数 ID 数组）
- 推进到下一条时，检查依赖项是否全部在 `completed` 中
- 如果依赖项被 BLOCKED，级联传播 BLOCKED 状态到所有下游任务
- FINALIZE 阶段，有依赖的功能标记为 deferred，等前序 PR 合并后再处理

---

## 6. 为什么这样设计

### 6.1 为什么用多会话而不是单会话

**上下文窗口是硬限制。** 一个复杂功能的 DESIGN + IMPLEMENT 可能消耗大量上下文。拆开后每个会话从干净状态开始，质量更稳定。

**角色隔离是质量保障。** VERIFY 会话没有实现过程的上下文偏见，更容易发现问题。这比在同一会话里切换角色 prompt 可靠得多。

### 6.2 为什么用 dev 分支而不是直接 PR 到 master

**暴露隐性冲突。** 两个功能都改了同一个组件，各自通过 CI 但合在一起挂了——只有在 dev 上累积合并才能尽早发现。

**串行递进。** 每个功能基于前序功能已合入的 dev 代码来设计和实现，避免在真空中开发。

### 6.3 为什么用外部 bash 编排而不是 AI 自己管理循环

**可靠性。** AI 会话可能因上下文耗尽、API 限流、网络中断等原因异常退出。外部脚本可以检测异常并自动恢复。

**关注点分离。** 脚本只做控制流（读状态→选 prompt→调 claude→判结果），AI 只做实际工作（写代码、跑测试、提交）。两者通过 loop-state.json 解耦。

### 6.4 为什么状态文件不纳入 Git

loop-state.json 记录的是"循环进度"而非"代码状态"。如果纳入 Git，在多分支环境下会产生大量无意义的合并冲突。它只在本地存在，由编排脚本和 claude 会话共同读写。

---

## 7. 文件结构

```
loop/
├── dev-loop.sh              # 编排脚本（bash）
├── autonomous-dev-loop.md   # 完整协议文档（AI 的行为规范）
├── spec-autonomous-dev-loop.md  # 本文件（设计规约）
├── loop-state.json          # 主状态文件（git-ignored，缺失时自动重建）
├── backlog.md               # 人类可读的进度看板（自动生成）
├── inbox/                   # 热插入任务目录（git-ignored）
│   └── *.md                 # 新任务文件
├── tasks/                   # 已入队任务描述（git-tracked）
│   └── {id}-{slug}_{date}.md
├── workers/                 # 并行模式 worker 状态（运行时生成，git-ignored）
│   └── {task_id}/
│       ├── state.json       # Worker 独立状态文件
│       ├── pid              # Worker 进程 PID
│       └── name             # 任务名称
├── .locks/                  # 并发锁目录（运行时生成）
├── logs/                    # 会话日志
│   ├── session-{date}-{phase}.log   # 串行模式会话日志
│   └── worker-{task_id}.log         # 并行模式 worker 日志
└── reviews/                 # 审查记录
    └── {date}-{slug}-review.md
```

---

## 8. 使用方式

### 串行模式（向后兼容）

```bash
# 1. 准备任务文件到 inbox/
echo '---
name: 修复登录按钮
complexity: 低
---
登录按钮在移动端点击区域太小' > loop/inbox/fix-login-button.md

# 2. 启动循环（串行，一次一个任务）
./loop/dev-loop.sh

# 3. 后台运行
nohup ./loop/dev-loop.sh > dev-loop-output.log 2>&1 &

# 4. 查看进度
cat loop/loop-state.json | python3 -m json.tool
tail -f dev-loop-output.log

# 5. 优雅停止
touch loop/inbox/STOP
```

### 并行模式（多任务同时执行）

```bash
# 最多 5 个任务并行（默认）
./loop/dev-loop.sh --workers 5

# 最多 3 个任务并行
./loop/dev-loop.sh --workers 3

# 并行 + verbose 调试
./loop/dev-loop.sh --workers 3 -v

# 查看各 worker 实时日志
tail -f loop/logs/worker-*.log

# 查看特定任务的日志
tail -f loop/logs/worker-56.log
```

调度器自动根据 `depends_on` 判断哪些任务可以并行，有依赖的任务等前序完成后自动启动。

### 手动模式（调试单个 Phase）

打开 Claude Code，粘贴对应 Phase 的启动 Prompt（见 `autonomous-dev-loop.md` 各章节），AI 读取 loop-state.json 后自主执行。

### Verbose 模式（-v）

```bash
./loop/dev-loop.sh -v   # 实时显示 AI 对话过程（stream-json + jq 解析）
```

输出格式：
- `🔧 工具名: 参数` — 每次工具调用
- `💬 文本` — Claude 的文字输出
- `✅ 完成 (耗时, 费用)` — 会话结束

---

## 9. 人工干预点

| 场景 | 人工操作 |
|------|----------|
| AWAITING_HUMAN_REVIEW | 按顺序逐个 squash merge PR 到 master |
| 功能被 BLOCKED | 检查原因，可能需要产品/技术决策 |
| 连续失败停止 | 查看日志排查系统性问题 |
| FATAL 错误 | 状态文件损坏或 Git 状态异常，手动修复 |
| master 分支保护未开启 | 在 GitHub 配置分支保护规则 |

---

## 10. 一句话总结

**外部脚本做编排，Claude 做执行；状态文件做记忆，会话边界做隔离；自动门禁做质量，dev 分支做缓冲；依赖感知并行加速，master 只读，人工兜底。**
