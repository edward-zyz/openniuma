# 自治研发循环 v2.1 (Autonomous Dev Loop)

> 让 AI 在无人值守下逐条完成 backlog，通过**多会话角色隔离** + **硬性自动化门禁** + **dev 集成分支** + **外置状态文件**确保质量。

---

## 硬性红线（不可违反）

> **禁止 AI 自主合并代码到 master。任何情况下，AI 只能将代码合入 dev 分支。合入 master 必须由人工确认。**

具体约束：
- **禁止** `git merge ... master`、`git push origin master`、`git checkout master && git merge ...`
- **禁止** 使用 `gh pr merge` 合并任何 PR 到 master
- **允许** 创建 dev → master 的 PR（`gh pr create --base master`），但**不允许**合并该 PR
- **允许** `git merge --no-ff feat/{slug}` 合入 dev 分支
- FINALIZE 阶段的产出是一个**待人工审核的 PR**，不是已合并的代码
- 如果任何 Phase 的指令与本红线冲突，以本红线为准

**系统级保障（Prompt 之外的硬约束）：**
- GitHub 侧 master 分支保护必须开启：require PR review、禁止 force push、require status checks
- 如未开启，INIT 阶段会通过 `gh api` 检查并提示人工配置

---

## 状态文件：外置于 Git（核心设计决策）

> **loop-state.json 不参与 Git 版本控制。** 它存放在固定的本地路径，不会被 `git reset`、`git checkout`、`git merge` 等操作影响。这从根本上消除了多分支修改同一状态文件导致的冲突。

### 路径约定

| 文件 | 路径 | 用途 |
|------|------|------|
| **运行时状态** | `~/.poi-loop/loop-state.json` | 唯一的状态真相源 |
| **写入前备份** | `~/.poi-loop/loop-state.prev.json` | 每次写入前自动备份上一版 |
| **Schema 模板** | `loop/loop_v2/loop-state.template.json`（repo 内） | 仅供参考，不直接使用 |

### 状态读写规则

- **读取：** 每个 Phase 启动时从 `~/.poi-loop/loop-state.json` 读取
- **写入：** 先将当前文件复制到 `loop-state.prev.json`，再写入新内容
- **不 commit、不 push。** 状态文件完全脱离 Git 生命周期
- **Git 操作（reset/checkout/merge）不影响状态文件**，这是选择方案 B 的核心收益

### 状态写入操作（所有 Phase 通用）

每次需要更新状态时，执行：
```bash
# 1. 备份当前状态
cp ~/.poi-loop/loop-state.json ~/.poi-loop/loop-state.prev.json 2>/dev/null || true

# 2. 写入到临时文件（由 AI 使用 Claude Code 的 Write 工具）
# 使用 Write 工具写入 ~/.poi-loop/loop-state.tmp.json

# 3. 原子替换（mv 在大多数文件系统上是原子操作，避免写入中途崩溃导致状态损坏）
mv ~/.poi-loop/loop-state.tmp.json ~/.poi-loop/loop-state.json
```

后文所有"更新 loop-state"均指此操作，不再重复。

---

## 状态文件 Schema

```jsonc
{
  "schema_version": 3,
  "dev_branch": "dev/backlog-batch-2026-03-23",

  // ── 当前任务定位（id = queue 中的 id 字段，不是数组下标）──
  "current_item_id": 1,
  "current_phase": "DESIGN",         // DESIGN | IMPLEMENT | VERIFY | FIX | MERGE | MERGE_FIX | FINALIZE | AWAITING_HUMAN_REVIEW | FAST_TRACK
  "track": "full",                   // "fast" | "full" — 当前任务走快速通道还是完整流程

  // ── 当前任务的分支和文件 ──
  "branch": null,                    // 当前功能分支名，如 "feat/remove-default-brands"
  "slug": null,                      // 当前功能的 slug，如 "remove-default-brands"
  "spec_path": null,
  "plan_path": null,
  "fix_list_path": null,

  // ── 细粒度 checkpoint（IMPLEMENT 断点续传）──
  "implement_progress": {
    "current_chunk": 0,
    "current_task": 0,
    "last_committed_task": null,
    "last_commit_sha": null
  },

  // ── 重试计数 ──
  "verify_attempts": 0,
  "merge_fix_attempts": 0,

  // ── 完成记录（结构化，便于 FINALIZE 使用）──
  "completed": [],
  // 元素格式: { "id": 1, "name": "去掉默认品牌预设", "slug": "remove-default-brands", "completed_at": "ISO8601" }

  "blocked": [],
  // 元素格式: { "id": 1, "name": "...", "slug": "...", "reason": "3x VERIFY failed", "blocked_at": "ISO8601" }

  // ── 功能队列 ──
  "queue": [
    { "id": 1, "name": "...", "status": "pending", "depends_on": [], "complexity": "低" }
    // status: "pending" | "in_progress" | "done" | "blocked" | "blocked_by_dependency"
  ],

  "system_alert": false,
  "updated_at": "ISO8601"
}
```

**关键变更（vs v2.0）：**
- `current_item_id` 替代 `current_item_index`，明确语义为 queue 中的 `id` 字段
- `track` 字段标识快速/完整流程，解决 VERIFY 无法区分的问题
- `slug` 字段独立存储，不靠从 name 反推
- `completed` / `blocked` 使用结构化对象，含 slug 用于分支清理
- `status` 增加 `"blocked_by_dependency"` 状态
- 移除 `last_phase_commit_sha`（状态不在 Git 中，无需 SHA 校验）

---

## 设计原则

1. **一个功能 = 一个会话周期。** 不在单会话里塞多个功能，避免上下文耗尽。
2. **角色隔离靠会话边界，不靠 prompt 前缀。** 设计者和审查者是不同的 Claude Code 会话，天然无法"放水"。
3. **质量靠自动化门禁，不靠 Agent 自觉。** lint/test/build/typecheck 是硬性关卡，必须 exit code 0 才能继续。
4. **状态外置于 Git。** loop-state.json 不参与版本控制，不会被 Git 操作影响，不会产生合并冲突。
5. **dev 分支做集成缓冲。** 功能分支合入 dev 而非直接进 master，所有功能在 dev 上累积验证，最终由人工 PR 回 master。
6. **master 只读。** AI 全程不触碰 master，最终产出是一个等待人工审核的 PR。
7. **环境切换必须完整。** 切分支后必须 `npm install`，确保依赖与分支一致。
8. **低复杂度走快速通道，可降级。** 简单任务不需要 4 个会话的完整流程，但发现不适合时可降级到完整流程。
9. **会话衔接靠编排器，不靠人工。** orchestrator.sh 自动读取状态、生成 Prompt、启动下一个会话，实现真正的无人值守。

---

## 分支策略

```
master (受保护，只接受 PR)
  │
  └── dev/backlog-batch-{date} (本轮循环的集成分支)
        │
        ├── feat/remove-default-brands     ← 功能 1，完成后 merge 回 dev
        ├── feat/remove-city-selector      ← 功能 2，完成后 merge 回 dev
        ├── feat/workspace-brand-config    ← 功能 3，完成后 merge 回 dev
        └── ...
```

### 规则

| 规则 | 说明 |
|------|------|
| **dev 分支从 master 拉出** | 循环启动时创建 `dev/backlog-batch-{date}`，push 到 remote |
| **功能分支从 dev 最新代码拉出** | `git pull origin dev && git checkout -b feat/{slug}` |
| **功能完成后 merge 回 dev** | 用 `git merge --no-ff feat/{slug}` 保留合并记录 |
| **merge 后必须跑 CI** | 在 dev 分支上执行全部门禁，全部 exit 0 才算合入成功 |
| **CI 通过后才能开始下一个功能** | dev CI 通过 → 从 dev 最新代码拉下一个 feat 分支 |
| **CI 失败必须修复** | 不可跳过，在 feat 分支上修复 → 重新 merge → 重新跑 CI |
| **全部功能完成后** | 从 dev 向 master 创建一个汇总 PR |
| **dev 分支上不直接写代码** | 只接受来自 feat/* 分支的 merge |
| **切分支后必须 npm install** | 确保 node_modules 与当前分支的 package.json / lock 一致 |
| **功能分支在 FINALIZE 后统一清理** | MERGE 阶段不删功能分支，保留回溯能力 |

---

## 循环总览

```
首次启动：INIT — 创建 dev 分支 + 初始化外置状态文件

每个功能项根据复杂度选择路径：

  ┌────────────────────────────────────────────────────────────┐
  │      ~/.poi-loop/loop-state.json（外置，不受 Git 影响）      │
  │  记录 dev 分支名、当前进度（含 Phase 内细粒度 checkpoint）    │
  │  每个 Phase 启动时校验状态一致性                              │
  └───────────┬────────────────────────────────────────────────┘
              │
              ├─── 低复杂度 (track: fast) → 快速通道
              │      单会话 DESIGN+IMPLEMENT → VERIFY → MERGE
              │      如果发现不适合可降级到完整流程
              │
              └─── 中/高复杂度 (track: full) → 完整流程
                     Session 1: DESIGN → Session 2: IMPLEMENT
                     → Session 3: VERIFY → Session 4: MERGE/FIX

全部功能完成后 → FINALIZE → 创建汇总 PR
```

---

## 通用步骤：状态校验（每个 Phase 启动时执行）

> 每个 Phase 的第一步不是"开始干活"，而是"确认接手状态正确"。

> 注：以下脚本为**伪代码**，展示校验逻辑。Agent 应先用 Read 工具读取 `~/.poi-loop/loop-state.json` 提取字段值，再执行对应的校验命令。`${dev_branch}` 等变量需由 Agent 从 JSON 中获取后替换。

```bash
# 1. 校验状态文件存在且可解析
[ -f ~/.poi-loop/loop-state.json ] \
  || { echo "FATAL: ~/.poi-loop/loop-state.json 不存在，需要先执行 INIT"; exit 1; }
python3 -c "import json; json.load(open('$HOME/.poi-loop/loop-state.json'))" \
  || { echo "FATAL: loop-state.json 不是合法 JSON"; exit 1; }

# 2. 校验 schema_version == 3
# 如果不等于 3，说明版本不匹配

# 3. 校验 dev 分支存在性（先尝试本地，再 fetch remote）
git rev-parse --verify "${dev_branch}" 2>/dev/null \
  || { git fetch origin "${dev_branch}" 2>/dev/null \
       && git rev-parse --verify "origin/${dev_branch}" 2>/dev/null \
       || { echo "FATAL: dev 分支 ${dev_branch} 不存在（本地和远程均无）"; exit 1; }; }

# 4. 如果 branch 字段非空，校验功能分支存在性
if [ -n "${branch}" ]; then
  git rev-parse --verify "${branch}" 2>/dev/null \
    || { git fetch origin "${branch}" 2>/dev/null \
         && git rev-parse --verify "origin/${branch}" 2>/dev/null \
         || { echo "FATAL: 功能分支 ${branch} 不存在"; exit 1; }; }
fi

# 5. 如果 spec_path 非空，校验文件存在（需要先 checkout 对应分支）
# 6. 如果 plan_path 非空，校验文件存在

# 7. 校验 current_phase 与当前会话角色匹配
# （如果状态说 VERIFY 但当前是 DESIGN 会话 → 停止，提示用户启动正确的 Phase）
```

**校验失败处理：**
- `FATAL`：停止执行，输出诊断信息，等待人工介入
- 状态文件损坏时可从 `~/.poi-loop/loop-state.prev.json` 恢复

---

## 通用步骤：环境同步（每次切分支后执行）

```bash
git checkout {target_branch}
git pull origin {target_branch}
npm install
```

本文后续所有"切到 xxx 分支"均隐含执行此环境同步。不再重复标注。

---

## Phase 0: INIT（初始化 — 仅首次）

### 启动 Prompt

```
阅读 loop/loop_v2/autonomous-dev-loop.md 的 Phase 0: INIT 部分。
执行初始化：创建状态目录、dev 集成分支，写入初始 loop-state.json。
然后检查第一个任务的复杂度，进入对应 Phase。
全程自主工作，不要问我问题。
```

### 操作

```bash
# 0. 幂等性检查：如果状态文件已存在，输出当前进度并确认
if [ -f ~/.poi-loop/loop-state.json ]; then
  echo "⚠️ 已存在 loop-state.json，当前进度："
  cat ~/.poi-loop/loop-state.json | python3 -m json.tool
  echo "如需重新初始化，请先手动删除 ~/.poi-loop/loop-state.json"
  exit 1
fi

# 1. 创建状态目录
mkdir -p ~/.poi-loop

# 2. 确保在最新 master 上
git checkout master
git pull origin master

# 3. 检查 master 分支保护状态
gh api repos/{owner}/{repo}/branches/master/protection 2>/dev/null \
  && echo "✅ master 分支保护已开启" \
  || echo "⚠️ 警告：master 分支保护未开启，请人工配置后再继续"

# 4. 创建 dev 集成分支
git checkout -b dev/backlog-batch-{date}
git push -u origin dev/backlog-batch-{date}
```

写入初始状态到 `~/.poi-loop/loop-state.json`（参考 Schema 模板）。

根据第一个任务的 complexity 决定进入 FAST_TRACK 还是 DESIGN。

---

## 快速通道：FAST_TRACK（低复杂度任务）

> 对 `complexity: "低"` 的任务，DESIGN + IMPLEMENT 合并为一个会话，跳过完整的 spec/plan 流程。

### 适用条件

- queue 中 `complexity` 为 `"低"`
- 任务不涉及数据库 migration，不涉及新增 API 路由

### 降级机制

如果在 FAST_TRACK 过程中发现任务比预期复杂（涉及 migration、需要改多于 5 个文件、或需要新增 API），**立即降级**：
1. 如果已创建功能分支但未 commit 代码 → 删除分支，更新状态 `track: "full"`, `current_phase: "DESIGN"`（回到设计阶段补 spec/plan）
2. 如果已有 commit → 保留分支，更新状态 `track: "fast"`, `current_phase: "VERIFY"`（视为 IMPLEMENT 已完成，VERIFY 按快速通道审查，对照 backlog + commit message）

> ⚠️ 情况 2 保持 `track: "fast"` 而非改为 `"full"`，因为快速通道不产生 spec 文件，若设为 `"full"` 会导致 VERIFY 尝试读取不存在的 spec 而卡死。

### 启动 Prompt

```
阅读 loop/loop_v2/autonomous-dev-loop.md 的 FAST_TRACK 部分。
阅读 ~/.poi-loop/loop-state.json 获取当前任务。
阅读 loop/backlog.md 获取需求描述。

你同时担任架构师和开发者。对当前低复杂度任务执行快速通道：
探索代码 → 创建功能分支 → TDD 实现 → push。
不需要写独立的 spec/plan 文件，在 commit message 中说明设计决策即可。
如果发现任务比预期复杂，按文档的降级机制切换到完整流程。
全程自主工作，不要问我问题。
```

### 工作流程

1. **执行状态校验**
2. 读取 backlog.md 对应条目
3. **在 dev 上探索代码**（限定范围：只看直接相关的 2-3 个文件）
4. **判断是否适合快速通道：** 如不适合 → 降级，更新状态，结束会话
5. 从 dev 创建功能分支 + 环境同步
6. TDD 实现：写测试 → 确认失败 → 实现 → 确认通过
7. 硬性门禁（与 VERIFY 一致）：
   ```bash
   npm run lint && npm test && npm run build
   npx tsc --noEmit -p frontend
   ```
8. commit + push（commit message 包含设计决策说明）
9. **更新 checkpoint：** 每完成一个逻辑单元就更新状态的 `implement_progress`，防止中途中断后无法恢复
10. 更新状态：
    ```json
    {
      "current_phase": "VERIFY",
      "track": "fast",
      "branch": "feat/{slug}",
      "slug": "{slug}",
      "spec_path": null,
      "plan_path": null
    }
    ```

---

## Phase 1: DESIGN（架构师会话）

### 启动 Prompt

```
阅读 loop/loop_v2/autonomous-dev-loop.md 的 Phase 1: DESIGN 部分。
阅读 ~/.poi-loop/loop-state.json 获取当前任务。
阅读 loop/backlog.md 获取需求描述。

你是架构师。你的任务是为当前 backlog 条目输出 spec 和 plan。
确保在 dev 分支上工作（分支名见状态文件的 dev_branch 字段）。
如果任务复杂度为"中"或"高"，使用 /brainstorming 技能辅助设计（自主推进模式，不等待确认）。
全程自主工作，不要问我问题。
```

### 工作流程

1. **执行状态校验**
2. 切到 dev 最新代码 + 环境同步
3. 读取 backlog.md 中对应条目的需求描述
4. **深度探索代码：**
   - 找到所有相关的数据模型、API 路由、前端组件、store 状态
   - 理解当前实现的完整上下文（基于 dev 分支最新代码）
   - 识别影响范围和与已合入功能的潜在冲突
   - **实用约束：** 先列出需要探索的文件清单（不超过 15 个），逐个读取，避免无目的地浏览
5. **读取当前 migration 版本号**（如果涉及数据库变更）：
   ```bash
   grep -n "MIGRATION_VERSION" backend/src/storage/migrations.ts | head -3
   ```
   在 spec 中使用 `CURRENT + 1` 占位，不硬编码数字。
6. **根据复杂度选择设计路径：**

   **路径 A — 中/高复杂度任务**（queue 中当前任务 `complexity = "中"` 或 `"高"`）：

   使用 `/brainstorming` 技能，将 backlog 需求描述 + 步骤 4 的代码探索发现作为输入。

   > ⚠️ **自主推进模式**：brainstorming 设计为交互式技能，会在多个节点等待用户确认。在自治循环中，**你就是决策者**，必须自行推进每个确认点：
   > - **被问澄清问题时** → 基于 backlog 描述和代码探索结果自行回答，不要等待
   > - **被要求在 2-3 个方案中选择时** → 自行选择最优方案并记录理由
   > - **设计分节展示并等待确认时** → 自行审阅，如有调整直接提出，然后确认通过
   > - **被要求审阅已写好的 spec 时** → 确认符合下方质量要求后直接通过
   > - **遇到任何"请确认"/"你觉得呢？"** → 立即做出决策并推进，绝不停留等待
   > - **同一确认点循环 3 次以上** → 强制通过并在 spec 中标注待复核

   brainstorming 完成后会自动产出 **spec** 文件（含内置 spec reviewer 审查），
   并过渡到 `writing-plans` 技能产出 **plan**。
   确认两个文件已生成后，**直接跳到步骤 7**。

   **路径 B — 低复杂度任务**（`complexity = "低"`）：

   手动编写 spec 和 plan：
   - 写 **spec**（`docs/superpowers/specs/{date}-{slug}-design.md`）：
     - 数据模型变更（含 migration SQL，版本号标注为 `CURRENT + 1`）
     - API 接口变更（路由、请求体、响应体）
     - 前端组件变更（PC + 移动端）
     - 对"待定"项做出决策并记录理由
     - **需求追溯：** 每个 spec 章节标注对应的 backlog 需求点
   - 写 **plan**（`docs/superpowers/plans/{date}-{slug}.md`）：
     - Chunk → Task → Step 结构
     - 每个 Task 标注要修改/新建的文件列表
     - TDD：每个功能 Task 先写测试 Step，再写实现 Step
     - **Task 依赖标注：** 如果 Task B 依赖 Task A 的产出，标注 `depends: Task A`

7. **创建功能分支，将 spec/plan commit 到功能分支**（避免在 dev 上 commit 后 merge 回来产生冲突）：
   ```bash
   git checkout -b feat/{slug}
   git add docs/superpowers/specs/{date}-{slug}-design.md docs/superpowers/plans/{date}-{slug}.md
   git commit -m "docs({slug}): add spec and plan"
   git push -u origin feat/{slug}
   ```
8. 更新状态：
   ```json
   {
     "current_phase": "IMPLEMENT",
     "branch": "feat/{slug}",
     "slug": "{slug}",
     "spec_path": "docs/superpowers/specs/{date}-{slug}-design.md",
     "plan_path": "docs/superpowers/plans/{date}-{slug}.md"
   }
   ```

### 质量要求

**无论走路径 A 还是路径 B，最终产出的 spec 和 plan 都必须满足：**

- spec 中的每个 API 必须定义请求/响应类型
- plan 中的每个 Task 必须有 `**Files:**` 列表
- 涉及 UI 的必须覆盖 PC + 移动端
- 数据库变更必须写完整 migration SQL（版本号用 `CURRENT + 1` 占位）
- spec 中每个章节必须追溯到 backlog 需求点

> 路径 A 的 spec 已经过 brainstorming 内置的 spec-reviewer 子代理审查；路径 B 的 spec 由架构师自行把关。两条路径都要确保上述五项达标后才能进入 IMPLEMENT。

---

## Phase 2: IMPLEMENT（开发者会话）

### 启动 Prompt

```
阅读 loop/loop_v2/autonomous-dev-loop.md 的 Phase 2: IMPLEMENT 部分。
阅读 ~/.poi-loop/loop-state.json 获取当前任务、dev 分支名和 plan 路径。
阅读对应的 plan 文件。

你是开发者。严格按照 plan 逐步实现，使用 TDD。
功能分支从 dev 分支拉出（不是从 master）。
全程自主工作，不要问我问题。
```

### 工作流程

1. **执行状态校验**
2. **检查是否为断点续传：**
   - 如果 `implement_progress.last_commit_sha` 非空：
     ```bash
     git checkout feat/{slug}
     git log --oneline | head -5
     git cat-file -t {last_commit_sha}
     ```
   - 读取状态中的 `current_chunk` + `current_task`，对照 plan 确认进度
   - 从断点处继续，不重做已完成的 Task
   - 如果 `last_commit_sha` 不存在 → 从头开始（但先检查分支上是否已有 commit）
3. **从 dev 最新代码创建功能分支**（如果还没有）：
   ```bash
   git checkout {dev_branch}
   git pull origin {dev_branch}
   npm install
   git checkout -b feat/{slug}
   ```
4. **动态确定 migration 版本号**（如果 plan 涉及数据库变更）：
   ```bash
   grep "MIGRATION_VERSION" backend/src/storage/migrations.ts
   ```
   将 plan/spec 中的 `CURRENT + 1` 替换为实际值。
5. **严格按 plan 的 Chunk → Task → Step 顺序执行：**
   - **plan 文件只读**，不在 plan 中勾选 `[x]` 或修改内容（避免 merge 回 dev 时产生冲突）
   - 进度通过状态文件的 `implement_progress` 追踪
   - TDD 节奏：写测试 → 确认失败 → 实现 → 确认通过
6. **每完成一个 Task，执行硬性门禁：**
   ```bash
   npm run lint 2>&1 | tail -20
   npm test 2>&1 | tail -50
   npx tsc --noEmit -p frontend 2>&1 | tail -20
   ```
   > 注：`npm run build` 的后端部分已包含 `tsc` 编译，无需单独执行 `npx tsc --noEmit -p backend`。前端的 `vite build` 不做类型检查，因此需要单独执行前端 typecheck。

   **全部 exit 0 才能 commit。**
7. **commit + push + 更新 checkpoint：**
   ```bash
   git add <具体文件>
   git commit -m "feat({slug}): {task 描述}"
   git push -u origin feat/{slug}
   ```
   立即更新状态的 `implement_progress`（每个 Task 都更新，不等全部完成）。
8. 全部 Task 完成后，执行最终门禁（与 VERIFY 一致）：
   ```bash
   npm run lint && npm test && npm run build
   npx tsc --noEmit -p frontend
   ```
9. 更新状态：
   ```json
   {
     "current_phase": "VERIFY",
     "branch": "feat/{slug}"
   }
   ```

### 卡住时的处理

- **测试写不出来：** 检查 spec 是否有歧义，如有则在 commit message 中记录偏差并继续
- **现有代码有 bug 挡路：** 在当前分支修复，commit message 标注 `fix:`
- **plan 步骤明显有误：** 在 commit message 中记录偏差原因并按修正后的方案继续（plan 文件保持只读）
- **同一步骤失败 5 次：** 在 commit message 中标注 `[STUCK]` 及原因。**跳过前检查依赖链：** 后续 Task 如果标注了 `depends: 当前Task`，也必须级联跳过。
- **上下文消耗过大（已读 + 已输出内容明显很多）：** 立即更新 implement_progress，commit 当前进度，结束当前会话。

---

## Phase 3: VERIFY（审查者会话）

> **核心价值：独立会话，同时审查"spec 是否覆盖原始需求"和"实现是否符合 spec"。**

### 启动 Prompt

```
阅读 loop/loop_v2/autonomous-dev-loop.md 的 Phase 3: VERIFY 部分。
阅读 ~/.poi-loop/loop-state.json 获取当前任务。
阅读 loop/backlog.md 获取原始需求描述。
如果状态中 spec_path 非空，阅读对应的 spec 文件。

你是代码审查者。你的职责是验证：
1. spec 是否完整覆盖了 backlog 中的原始需求（完整流程）
2. 实现是否正确且符合 spec（或 backlog 需求，若为快速通道）和项目规范
你必须严格执行检查，发现问题必须指出，不可放水。
全程自主工作，不要问我问题。
```

### 工作流程

#### Step 0: 状态校验 + 流程识别

执行状态校验。读取 `track` 字段：
- `track: "fast"` → 跳过 Step 1（无独立 spec），Step 3 对照 backlog 原始需求 + commit message
- `track: "full"` → 执行完整流程

#### Step 1: 需求覆盖审查（仅 track: full）

读取 spec 文件（路径来自状态的 `spec_path`），与 backlog 原始需求对照：

| # | 检查项 | 判定方法 |
|---|--------|----------|
| 0a | **需求完整性** | backlog 中的每个需求点在 spec 中都有对应设计 |
| 0b | **决策合理性** | spec 中对"待定"项的决策是否合理，是否遗漏了关键场景 |
| 0c | **过度设计** | spec 是否包含 backlog 未要求的功能（避免范围蔓延） |

如果 spec 本身有重大缺陷 → 直接判 FAIL，修复清单标注"spec 层面问题"。

#### Step 2: 自动化门禁（硬性，不可跳过）

```bash
git checkout feat/{slug}
git pull origin feat/{slug}
npm install

npm run lint
npm test
npm run build
npx tsc --noEmit -p frontend
```

> 注：`npm run build` 的后端部分已包含 `tsc` 编译，无需再单独执行 `npx tsc --noEmit -p backend`。前端的 `vite build` 不做类型检查，因此需要单独执行前端 typecheck。

任何一个失败 = 直接 FAIL。

#### Step 3: diff 审查

```bash
git diff {dev_branch}...feat/{slug} --stat
git diff {dev_branch}...feat/{slug}
```

对照 spec（或 backlog + commit message），逐项检查：

| # | 检查项 | 判定方法 |
|---|--------|----------|
| 1 | **功能完整性** | 每个需求点在 diff 中都有对应实现 |
| 2 | **API 契约** | 路由、请求体、响应体与 spec 定义一致 |
| 3 | **数据库 migration** | 版本递增、SQL 正确、有 IF NOT EXISTS 防护 |
| 4 | **测试覆盖** | 新增路由有测试、核心逻辑有单测 |
| 5 | **移动端实现** | UI 变更同时覆盖 PC 和移动端 |
| 6 | **类型安全** | 新代码无 `any`、时间字段为 `string` 类型 |
| 7 | **UI 规范** | Tailwind + tokens 颜色、`cn()` 合并 class |
| 8 | **安全** | SQL 参数化、权限中间件覆盖、无敏感信息硬编码 |
| 9 | **副作用** | 是否意外修改了不相关的文件或功能 |

#### Step 4: 出具判定

写入 `loop/reviews/{date}-{slug}-review.md`：

```markdown
# Review: {功能名}
**Branch:** feat/{slug}
**Track:** fast | full
**Verdict:** PASS | FAIL
**Date:** {date}

## 需求覆盖审查（仅 full track）
- [x] backlog 需求完整覆盖
- [x] 决策合理性
- [x] 无过度设计

## 自动化门禁
- [x] lint / test / build / typecheck

## 审查发现
### Critical（必须修复）
### Major（应该修复）
### Minor（建议改进，不阻塞）
```

commit + push review 文件到 feat 分支。

#### Step 5: 更新状态

- **PASS →** `{ "current_phase": "MERGE" }`
- **FAIL →**
  - `verify_attempts += 1`
  - 如果 `verify_attempts >= 3`：**执行 BLOCKED 清理**（见下方）
  - 否则：`{ "current_phase": "FIX", "fix_list_path": "reviews/{date}-{slug}-review.md" }`

> ℹ️ `verify_attempts` 是**总次数上限**，FIX 后不重置。VERIFY→FIX→VERIFY→FIX→VERIFY 循环中第 3 次 VERIFY 失败即 BLOCKED，避免无限循环。`merge_fix_attempts` 同理。

---

## BLOCKED 清理流程（通用）

当任何阶段触发 BLOCKED 时，执行统一的清理：

1. 将当前项加入 `blocked` 数组：
   ```json
   { "id": 1, "name": "...", "slug": "...", "reason": "...", "blocked_at": "ISO8601" }
   ```
2. 更新 queue 中对应项 `status = "blocked"`
3. **级联标记依赖项：** 扫描 queue，将所有 `depends_on` 包含当前项 id 的任务标记为 `status = "blocked_by_dependency"`（递归，直到没有更多受影响的项）
4. **清理 backup tag**（如果存在）：
   ```bash
   git tag -d "backup/pre-merge-{slug}" 2>/dev/null
   git push origin --delete "backup/pre-merge-{slug}" 2>/dev/null || true
   ```
5. 清空当前任务字段：`branch`, `slug`, `spec_path`, `plan_path`, `fix_list_path`, `verify_attempts`, `merge_fix_attempts`，重置 `implement_progress`
6. `current_item_id` 推进到下一个 `status = "pending"` 且依赖已满足的项
7. 如果没有更多可执行项 → `current_phase = "FINALIZE"`
8. 否则根据下一项的 complexity 设置 `current_phase` 和 `track`
9. 如果 blocked 数组长度 >= 3 → 设置 `system_alert = true`

> ℹ️ `blocked_by_dependency` 在当前 batch 中不可自动恢复。如果人工后续修复了被 BLOCKED 的依赖项，需要在下一个 batch 中重新处理受影响的任务。

---

## Phase 3.5: FIX（修复会话）

### 启动 Prompt

```
阅读 loop/loop_v2/autonomous-dev-loop.md 的 Phase 3.5: FIX 部分。
阅读 ~/.poi-loop/loop-state.json 获取修复清单路径。
阅读修复清单文件（路径在状态的 fix_list_path 字段）。

你是修复开发者。只修复清单中的 Critical 和 Major 问题，不做额外改动。
如果清单包含"spec 层面问题"，需要同时修正 spec 和对应的实现代码。
修完后 push，全程自主工作，不要问我问题。
```

### 工作流程

1. **执行状态校验**
2. 切到功能分支 + 环境同步
3. 读取 review 文件中的 Critical + Major 问题
4. **区分问题类型：**
   - **spec 层面问题：** 先修正 spec 文件，再修正实现代码。**如果 spec 路径变更，更新状态的 `spec_path`。**
   - **实现层面问题：** 直接修正实现代码
5. 逐个修复，每个修复后跑 `npm run lint && npm test`
6. commit + push
7. 更新状态：`{ "current_phase": "VERIFY" }`

---

## Phase 4: MERGE（合入 dev + CI 验证）

### 启动 Prompt

```
阅读 loop/loop_v2/autonomous-dev-loop.md 的 Phase 4: MERGE 部分。
阅读 ~/.poi-loop/loop-state.json 获取当前任务和分支信息。

你是集成者。将功能分支合入 dev，跑 CI，更新 backlog，推进到下一个任务。
全程自主工作，不要问我问题。
```

### 工作流程

#### Step 1: 合入前备份

```bash
git checkout {dev_branch}
git pull origin {dev_branch}
npm install

# 打 tag 并推到 remote（跨会话可用）
git tag "backup/pre-merge-{slug}" HEAD
git push origin "backup/pre-merge-{slug}"
```

#### Step 2: 合入 dev

```bash
git merge --no-ff feat/{slug} -m "merge: feat/{slug} into {dev_branch}"
```

如果有合并冲突：
1. 解决冲突（优先保留 dev 上已有功能的逻辑，在此基础上融入新功能）
2. `git add` 冲突文件
3. `git commit`（写清楚冲突解决方式）

#### Step 3: 在 dev 上跑 CI（硬性门禁）

```bash
npm install
npm run lint
npm test
npm run build
```

全部 exit 0 = CI 通过。

#### Step 4: 根据 CI 结果决定下一步

**CI 通过：**

```bash
# push dev
git push origin {dev_branch}

# 清理备份 tag（本地 + 远程）
git tag -d "backup/pre-merge-{slug}"
git push origin --delete "backup/pre-merge-{slug}"
```

更新 backlog.md — 已完成项标注 `✅`，commit + push 到 dev。

更新状态：
- 当前项加入 `completed`：`{ "id": N, "name": "...", "slug": "...", "completed_at": "ISO8601" }`
- queue 中对应项 `status = "done"`
- 清空 `branch`, `slug`, `spec_path`, `plan_path`, `fix_list_path`，重置 `verify_attempts`, `merge_fix_attempts`, `implement_progress`
- `current_item_id` 推进到下一个 `status = "pending"` 且依赖已满足的项
- 如果没有更多可执行项 → `current_phase = "FINALIZE"`
- 否则根据下一项 complexity 设置 `current_phase` 和 `track`

**CI 失败：**

```bash
# 使用备份 tag 安全回退
git reset --hard "backup/pre-merge-{slug}"
# 不立即删 tag，MERGE_FIX 后重新 MERGE 时会复用
```

> ⚠️ 因为状态文件在 Git 之外，`git reset --hard` 不会影响 loop-state.json。这是方案 B 的关键优势。

切到 feat 分支记录 CI 失败日志（dev 已 reset，日志存在 feat 分支上）：
```bash
git checkout feat/{slug}
```
记录 CI 失败日志到 `loop/reviews/{date}-{slug}-ci-fail.md`，commit + push 到 feat 分支。

更新状态：
- `merge_fix_attempts += 1`
- `fix_list_path` = CI fail log 路径
- 如果 `merge_fix_attempts >= 3`：**执行 BLOCKED 清理**
- 否则：`current_phase = "MERGE_FIX"`

---

## Phase 4.5: MERGE_FIX（合入冲突修复会话）

### 启动 Prompt

```
阅读 loop/loop_v2/autonomous-dev-loop.md 的 Phase 4.5: MERGE_FIX 部分。
阅读 ~/.poi-loop/loop-state.json 获取当前状态和 CI 失败日志路径。
阅读 CI 失败日志文件。

你是集成修复开发者。CI 在 dev 合入后失败，你需要在功能分支上修复兼容性问题。
全程自主工作，不要问我问题。
```

### 工作流程

1. **执行状态校验**
2. 读取 CI 失败日志
3. 切到功能分支 + 环境同步
4. **分析 CI 失败原因，决定修复策略：**
   - **集成/兼容性问题**（与 dev 上已有功能冲突）→ 先将 dev 最新代码 merge 到功能分支：
     ```bash
     git merge {dev_branch}
     npm install
     ```
   - **功能本身的 bug** → 直接在 feat 分支上修复，不 merge dev（避免引入不相关变更干扰修复）
5. 修复问题
6. 确认 CI 通过：`npm run lint && npm test && npm run build`
7. commit + push
8. 更新状态：`{ "current_phase": "MERGE" }`
9. 清理旧 backup tag（如果存在）：
   ```bash
   git tag -d "backup/pre-merge-{slug}" 2>/dev/null
   git push origin --delete "backup/pre-merge-{slug}" 2>/dev/null || true
   ```
   > 下次 MERGE 会重新打 tag。

---

## Phase 5: FINALIZE（全部完成后 — 汇总 PR）

当 queue 中所有项都已 `done`、`blocked` 或 `blocked_by_dependency` 时，进入最终阶段。

### 启动 Prompt

```
阅读 loop/loop_v2/autonomous-dev-loop.md 的 Phase 5: FINALIZE 部分。
阅读 ~/.poi-loop/loop-state.json 获取完成情况。

你是发布经理。清理功能分支，创建从 dev 到 master 的汇总 PR（仅创建，不合并）。
⚠️ 禁止执行 gh pr merge 或任何将代码合入 master 的操作。
全程自主工作，不要问我问题。
```

### 工作流程

1. **执行状态校验**
2. 确保 dev 与 master 同步：
   ```bash
   git checkout {dev_branch}
   git pull origin {dev_branch}
   npm install
   git fetch origin master
   git merge origin/master -m "merge: sync master into {dev_branch} before finalize"
   ```
   > ⚠️ 使用 `git merge` 而非 `git rebase`，因为 dev 分支包含 `--no-ff` merge commits，rebase 会打散合并历史并产生重复提交。

   如果 merge 有冲突，解决后继续。

3. 最终 CI 验证：
   ```bash
   npm install
   npm run lint && npm test && npm run build
   ```

   **如果 CI 失败：**
   - 分析失败原因（通常是 master 上的新变更与 dev 上的功能不兼容）
   - 在 dev 分支上直接修复（这是唯一允许在 dev 上直接写代码的场景，因为所有功能分支已合入）
   - 修复后重新跑 CI，全部通过后继续
   - 如果 2 次修复仍失败，回退 merge（`git reset --hard ORIG_HEAD`），设置 `system_alert = true`，等待人工介入

4. **清理已合入的功能分支（本地 + 远程）：**
   ```bash
   # 遍历 completed 列表（slug 字段）
   for slug in {completed_slugs}; do
     git branch -d "feat/${slug}" 2>/dev/null || true
     git push origin --delete "feat/${slug}" 2>/dev/null || true
   done

   # 也清理残留的 backup tags
   git tag -l "backup/pre-merge-*" | xargs -I {} sh -c 'git tag -d {} && git push origin --delete {} 2>/dev/null || true'
   ```

5. 创建汇总 PR：
   ```bash
   gh pr create \
     --base master \
     --head {dev_branch} \
     --title "feat: backlog batch {date}" \
     --body "$(cat <<'EOF'
   ## Summary

   本 PR 包含以下 backlog 功能的实现：

   ### Completed
   {逐条列出 completed 中的功能}

   ### Blocked (需人工处理)
   {逐条列出 blocked 中的功能及原因}

   ### Skipped (依赖阻塞)
   {逐条列出 blocked_by_dependency 的功能}

   ## CI Verification
   - [x] `npm run lint` 通过
   - [x] `npm test` 全部通过
   - [x] `npm run build` 构建成功
   - [ ] 人工验收

   🤖 Generated with [Claude Code](https://claude.com/claude-code)
   EOF
   )"
   ```

6. **到此为止。** 不要合并 PR，不要操作 master 分支。PR 创建后等待人工审核。

   > ℹ️ 根据项目规范（CLAUDE.md），master 只接受 Squash Merge。GitHub 会将 dev 上的全部 `--no-ff` 合并历史压缩为一个 commit。dev 分支内部的 merge 记录仅用于循环过程中的追踪和调试。

7. 更新状态：`{ "current_phase": "AWAITING_HUMAN_REVIEW" }`

---

## 任务队列与依赖

| 序号 | 任务 | 依赖 | 复杂度 | 路径 |
|------|------|------|--------|------|
| 1 | 去掉默认品牌预设 | 无 | 低 | FAST_TRACK |
| 2 | 去掉城市选项 | 无 | 低 | FAST_TRACK |
| 3 | 空间品牌配置扩展 | 无 | 中 | 完整流程 |
| 4 | 品牌关键词管理 | #3 | 中 | 完整流程 |
| 5 | POI 展示模式切换 | 无 | 中 | 完整流程 |
| 6 | 空间邀请能力优化 | 无 | 低 | FAST_TRACK |
| 7 | 空间算法优化 | #3 | 高 | 完整流程 |
| 8 | POI 缓存管理后台 | 无 | 中 | 完整流程 |
| 9 | 非餐饮类 POI 接入 | 无 | 中 | 完整流程 |
| 10 | Onboarding 流程优化 | #1-#6 | 中 | 完整流程 |

**依赖规则：**
- 推进时检查 `depends_on` 中的项是否全部 `done`
- 如果依赖项是 `blocked` → 当前项自动标记 `blocked_by_dependency`（BLOCKED 清理流程会级联处理）
- `blocked_by_dependency` 的项不视为可执行，不阻塞 FINALIZE

---

## 异常处理

| 场景 | 处理 |
|------|------|
| **状态文件不存在** | FATAL，需要执行 INIT |
| **状态文件损坏** | 从 `~/.poi-loop/loop-state.prev.json` 恢复 |
| **分支不存在（本地）** | 尝试 `git fetch origin {branch}` |
| **分支不存在（远程）** | FATAL，等待人工 |
| **Phase 与会话角色不匹配** | 停止，提示用户启动正确的 Phase |
| **git reset --hard** | 不影响状态文件（外置于 Git），安全 |
| **自动化门禁 3 次修不好** | BLOCKED 清理 → 级联标记依赖项 → 跳下一条 |
| **dev 合入后 CI 3 次修不好** | BLOCKED 清理 → reset dev 到 backup tag |
| **plan 与代码冲突** | IMPLEMENT 在 commit message 记录偏差（plan 只读） |
| **依赖项被 BLOCKED** | 级联标记 `blocked_by_dependency` |
| **migration 版本冲突** | 动态读取 MIGRATION_VERSION + 1 |
| **merge dev 时有冲突** | 优先保留 dev 已有逻辑 |
| **上下文消耗过大** | 保存 implement_progress，commit 当前进度，结束会话 |
| **npm install 失败** | 删 node_modules 重试 → 仍失败则 FATAL |
| **[STUCK] 有依赖链** | 级联标注所有依赖 Task |
| **3+ 功能连续 BLOCKED** | `system_alert = true`，等待人工 |
| **所有可执行项已处理** | 进入 FINALIZE（含 done + blocked + blocked_by_dependency） |
| **FAST_TRACK 发现复杂度超预期** | 降级到完整流程（见 FAST_TRACK 降级机制） |
| **INIT 误执行两次** | 检测到已有状态文件则输出进度并拒绝覆盖 |
| **FINALIZE CI 失败** | 在 dev 上修复（限 2 次），仍失败则回退 merge + system_alert |
| **MERGE CI 失败后日志 commit** | 显式 checkout feat 分支后再 commit，避免误 commit 到 dev |

---

## 运维：无人值守启动

### 编排脚本（Orchestrator）

一键启动，自动完成所有 Phase 的会话衔接，直到所有任务完成或触发人工干预。

将以下脚本保存为 `loop/loop_v2/orchestrator.sh`：

```bash
#!/bin/bash
# orchestrator.sh — 无人值守循环编排器
# 用法：./orchestrator.sh [--init]
#   --init  首次运行，执行 INIT 阶段
#   无参数  从 loop-state.json 当前状态恢复

set -euo pipefail

STATE_FILE="$HOME/.poi-loop/loop-state.json"
DOC="loop/loop_v2/autonomous-dev-loop.md"
BACKLOG="loop/backlog.md"
LOG_DIR="$HOME/.poi-loop/logs"
mkdir -p "$LOG_DIR"

# ── 颜色输出 ──
info()  { echo -e "\033[0;36m[orchestrator]\033[0m $*"; }
warn()  { echo -e "\033[0;33m[orchestrator]\033[0m $*"; }
error() { echo -e "\033[0;31m[orchestrator]\033[0m $*"; }
ok()    { echo -e "\033[0;32m[orchestrator]\033[0m $*"; }

# ── 从状态文件读取字段 ──
read_state() {
  python3 -c "import json,sys; d=json.load(open('$STATE_FILE')); print(d.get('$1',''))"
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

# ── 主循环 ──

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
  echo "$INIT_PROMPT" | claude --print 2>&1 | tee "$LOG_FILE"
fi

# 检查状态文件
if [ ! -f "$STATE_FILE" ]; then
  error "状态文件不存在，请先执行: $0 --init"
  exit 1
fi

ROUND=0
while true; do
  ROUND=$((ROUND + 1))

  # 读取当前状态
  PHASE=$(read_state current_phase)
  ALERT=$(read_state system_alert)
  ITEM_ID=$(read_state current_item_id)

  info "── Round $ROUND | Phase: $PHASE | Item: $ITEM_ID ──"

  # 检查终止条件
  if [ "$PHASE" = "AWAITING_HUMAN_REVIEW" ]; then
    ok "全部完成！汇总 PR 已创建，等待人工审核。"
    break
  fi

  if [ "$ALERT" = "True" ] || [ "$ALERT" = "true" ]; then
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

  # claude --print 非交互模式运行，完成后自动退出
  echo "$PROMPT" | claude --print 2>&1 | tee "$LOG_FILE"
  EXIT_CODE=${PIPESTATUS[1]:-0}

  if [ "$EXIT_CODE" -ne 0 ]; then
    warn "Claude Code 退出码: $EXIT_CODE，检查状态后继续..."
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

  # 检查 Phase 是否变化（防止同一 Phase 无限循环）
  NEW_PHASE=$(read_state current_phase)
  if [ "$NEW_PHASE" = "$PHASE" ] && [ "$NEW_PHASE" != "IMPLEMENT" ]; then
    # IMPLEMENT 可能因上下文耗尽需要多轮，允许同 Phase 重入
    warn "Phase 未变化（仍为 $PHASE），可能会话未正常完成。再试一次..."
    # 给一次重试机会
    echo "$PROMPT" | claude --print 2>&1 | tee -a "$LOG_FILE"
    sleep 2
    NEW_PHASE=$(read_state current_phase)
    if [ "$NEW_PHASE" = "$PHASE" ]; then
      error "两次执行后 Phase 仍未推进，停止。请检查日志: $LOG_FILE"
      break
    fi
  fi

  info "Phase 从 $PHASE → $NEW_PHASE"
done

info "编排器结束。最终状态:"
cat "$STATE_FILE" | python3 -m json.tool
```

### 使用方式

```bash
# 首次启动（一键，然后走开）
chmod +x loop/loop_v2/orchestrator.sh
./loop/loop_v2/orchestrator.sh --init

# 如果编排器因意外中断（机器重启等），从当前状态恢复
./loop/loop_v2/orchestrator.sh

# 后台运行（完全无人值守）
nohup ./loop/loop_v2/orchestrator.sh --init > ~/.poi-loop/orchestrator.log 2>&1 &

# 查看进度
cat ~/.poi-loop/loop-state.json | python3 -m json.tool

# 查看编排器日志
tail -f ~/.poi-loop/orchestrator.log

# 查看某轮会话的详细日志
ls ~/.poi-loop/logs/
```

### 编排器如何保证无人值守

| 场景 | 编排器行为 |
|------|-----------|
| Phase 正常完成 | 读取新 Phase → 自动启动下一个 Claude Code 会话 |
| 会话异常退出 | 检查状态文件 → 如果 Phase 已推进则继续，否则重试一次 |
| IMPLEMENT 上下文耗尽 | 状态文件有断点，Phase 不变，编排器允许 IMPLEMENT 重入 |
| 状态文件丢失 | 自动从 prev.json 恢复 |
| `AWAITING_HUMAN_REVIEW` | 编排器停止，输出完成信息 |
| `system_alert: true` | 编排器停止，提示人工排查 |
| 同一 Phase 重试 2 次仍未推进 | 编排器停止，输出日志路径供排查 |

### 人工干预点

编排器会自动停止并提示的场景：
- **AWAITING_HUMAN_REVIEW** — 汇总 PR 已创建，人工决定是否 merge
- **system_alert: true** — 3+ 功能连续 BLOCKED，可能有系统性问题
- **Phase 卡死** — 两次执行后 Phase 仍未推进

> ⚠️ **AI 全程不会将任何代码合入 master。master 的变更只能由人工操作。**

### 状态恢复

```bash
# 如果状态文件损坏，从备份恢复
cp ~/.poi-loop/loop-state.prev.json ~/.poi-loop/loop-state.json
# 然后重新启动编排器（无 --init）
./loop/loop_v2/orchestrator.sh
```

---

## v2.0 → v2.1 Bug 修复清单

| Bug | 问题 | 修复 |
|-----|------|------|
| **P0-1** | loop-state.json 多分支修改 → 每次 MERGE 必冲突 | 状态文件移出 Git，存放于 `~/.poi-loop/` |
| **P0-2** | reset --hard 回退状态文件 | 状态文件不在 Git 中，不受 reset 影响 |
| **P0-3** | current_item_index 语义不明 | 改为 `current_item_id`，明确为 queue 中的 id 字段 |
| **P1-4** | FAST_TRACK 进 VERIFY 无法识别 | 新增 `track` 字段（"fast"/"full"），VERIFY 按 track 分流 |
| **P1-5** | 依赖未满足的任务阻止 FINALIZE | 新增 `blocked_by_dependency` 状态，BLOCKED 时级联标记 |
| **P1-6** | backup tag 只在本地 | 创建后 push 到 remote |
| **P1-7** | VERIFY Prompt 未引导读 spec | Prompt 增加 spec_path 读取指引 |
| **P2-8** | MERGE_FIX 中 merge dev 导致 loop-state 冲突 | 状态文件不在 Git 中，无冲突 |
| **P2-9** | 环境同步 typecheck 不设门禁 | 移除虚假的冒烟测试，依赖各 Phase 的正式门禁 |
| **P2-10** | 40% context 不可执行 | 改为实用约束："列出不超过 15 个文件" |
| **P2-11** | FAST_TRACK 无断点续传 | 增加 checkpoint 更新 + 降级机制 |
| **P2-12** | FIX 修 spec 后 spec_path 未更新 | FIX 工作流增加 spec_path 更新步骤 |
| **P3-13** | completed 元素类型未定义 | 定义为 `{ id, name, slug, completed_at }` |
| **P3-14** | BLOCKED 后字段未清空 | 统一 BLOCKED 清理流程 |
| **P3-15** | CI 失败后 fix_list_path 未更新 | MERGE CI 失败时更新 fix_list_path |

## v2.1 → v2.2 Bug 修复清单（QA1）

| Bug | 问题 | 修复 |
|-----|------|------|
| **P0-1** | FAST_TRACK 降级到 full 后 VERIFY 读不到 spec | 已有 commit 时保持 `track: "fast"`，VERIFY 按快速通道审查 |
| **P0-2** | BLOCKED 清理不清理 backup tag | BLOCKED 清理流程增加 tag 清理步骤 |
| **P0-3** | DESIGN 步骤编号跳跃（7→9） | 修正为连续编号 7→8 |
| **P0-4** | spec/plan commit 到 dev 导致 merge 冲突 | DESIGN 创建 feat 分支，spec/plan commit 到 feat；IMPLEMENT plan 只读 |
| **P1-5** | verify_attempts 语义不清 | 补充说明：3 次是总次数上限，FIX 后不重置 |
| **P1-6** | blocked_by_dependency 无恢复机制 | 补充说明：当前 batch 不可恢复，需下一 batch 重新处理 |
| **P1-7** | MERGE CI 失败后日志 commit 去向不明确 | 增加显式 `git checkout feat/{slug}` 步骤 |
| **P1-8** | FINALIZE CI 失败无处理流程 | 增加修复流程（限 2 次，仍失败则回退 + system_alert） |
| **P1-9** | 模板 current_phase 初始值与任务复杂度不匹配 | 模板改为 `"FAST_TRACK"`（任务 #1 为低复杂度） |
| **P2-10** | 状态写入非原子操作 | 改为 tmp + mv 原子写入 |
| **P2-11** | INIT 无幂等性检查 | 启动时检测已有状态文件，拒绝覆盖 |
| **P2-12** | 状态校验 shell 变量来源不明 | 增加伪代码标注说明 |
| **P2-13** | IMPLEMENT/VERIFY/FAST_TRACK 门禁命令不一致 | 统一所有门禁为 `lint + test + build + tsc --noEmit -p frontend` |
| **P2-14** | MERGE_FIX 无条件 merge dev | 改为先分析原因，仅集成问题才 merge dev |
