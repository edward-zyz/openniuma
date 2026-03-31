# 自治研发循环 (Autonomous Dev Loop)

> 让 AI 在无人值守下逐条完成 backlog，通过**多会话角色隔离** + **硬性自动化门禁** + **dev 集成分支**确保质量。

---

## 硬性红线（不可违反）

> **🚫 禁止 AI 自主合并代码到 master。任何情况下，AI 只能将代码合入 dev 分支。合入 master 必须由人工确认。**

具体约束：
- **禁止** `git merge ... master`、`git push origin master`、`git checkout master && git merge ...`
- **禁止** 使用 `gh pr merge` 合并任何 PR 到 master
- **允许** `git merge --no-ff feat/{slug}` 合入 dev 分支（开发阶段：集成测试）
- **允许** 创建 feat → master 的 PR（`gh pr create --base master --head feat/{slug}`），但**不允许**合并该 PR（FINALIZE 阶段：发布）
- FINALIZE 阶段的产出是一个**待人工审核的 PR**，不是已合并的代码
- 如果任何 Phase 的指令与本红线冲突，以本红线为准

**系统级保障（Prompt 之外的硬约束）：**
- GitHub 侧 master 分支保护必须开启：require PR review、禁止 force push、require status checks
- 如未开启，INIT 阶段会通过 `gh api` 检查并提示人工配置

---

## 设计原则

1. **一个功能 = 一个会话周期。** 不在单会话里塞多个功能，避免上下文耗尽。中复杂度任务可合并 DESIGN+IMPLEMENT 为一个会话以减少上下文重建成本。
2. **角色隔离靠会话边界，不靠 prompt 前缀。** 设计者和审查者是不同的 Claude Code 会话，天然无法"放水"。
3. **质量靠自动化门禁，不靠 Agent 自觉。** lint/test/build/typecheck 是硬性关卡，必须 exit code 0 才能继续。
4. **状态持久化到文件 + 启动时校验。** 每个阶段结束更新 loop-state.json，新会话读取后**先校验再续传**。
5. **dev 分支做集成缓冲。** 功能分支合入 dev 而非直接进 master，所有功能在 dev 上累积验证。FINALIZE 时为每个功能单独创建 feat→master PR，使 master 上每个功能对应一个独立 commit。
6. **master 只读。** AI 全程不触碰 master，最终产出是一个等待人工审核的 PR。
7. **环境切换必须完整。** 切分支后必须 `npm install`，确保依赖与分支一致。
8. **按复杂度分流。** 低→快速通道(2 session)，中→合并模式(3 session)，高→完整流程(4 session)。复杂度由 AI 根据客观标准动态评估，而非人工标注。
9. **会话衔接靠外部编排，不靠人工粘贴。** Claude Code 单会话有上下文上限，phase 之间的续接由编排脚本自动完成，实现真正无人值守。手动模式仅作为备选。
10. **状态更新优先于收尾操作。** 每个 Phase 完成核心决策后，应**立即更新 loop-state.json 的 phase 字段**，再执行 git commit/push 等收尾操作。这样即使会话在收尾时中断，编排脚本能检测到 phase 已推进，下一个会话可以补做未完成的收尾工作。

---

## 分支策略

```
master (受保护，只接受 PR squash merge)
  │
  └── dev/backlog-batch-{date} (集成分支，用于累积验证)
        │
        ├── feat/remove-default-brands     ← 功能 1，完成后 merge --no-ff 回 dev
        ├── feat/remove-city-selector      ← 功能 2，完成后 merge --no-ff 回 dev
        ├── feat/workspace-brand-config    ← 功能 3，完成后 merge --no-ff 回 dev
        └── ...

开发阶段：feat → dev（集成测试 + CI）
发布阶段（FINALIZE）：feat rebase master → 独立 PR → 人工 squash merge → master
                      每个功能 = master 上一个独立 commit
```

### 规则

| 规则 | 说明 |
|------|------|
| **dev 分支从 master 拉出** | 循环启动时创建 `dev/backlog-batch-{date}`，push 到 remote |
| **功能分支从 dev 最新代码拉出** | `git checkout {dev_branch} && git pull && git checkout -b feat/{slug}`，确保包含前序功能 |
| **功能完成后 merge 回 dev** | 用 `git merge --no-ff feat/{slug}` 保留合并记录 |
| **merge 后必须跑 CI** | 在 dev 分支上执行 `npm run lint && npm test && npm run build`，全部 exit 0 才算合入成功 |
| **CI 通过后才能开始下一个功能** | dev CI 通过 → 从 dev 最新代码拉下一个 feat 分支，形成串行递进 |
| **CI 失败必须修复** | 不可跳过，在 feat 分支上修复 → 重新 merge → 重新跑 CI |
| **全部功能完成后** | 为每个已完成功能创建独立的 feat→master PR（按完成顺序） |
| **dev 分支上不直接写代码** | 只接受来自 feat/* 分支的 merge |
| **切分支后必须 npm install** | 确保 node_modules 与当前分支的 package.json / lock 一致 |
| **功能分支在 PR 合并后由 GitHub 自动清理** | MERGE 阶段不删功能分支，FINALIZE 创建 PR 后保留至人工合并 |

### 为什么用 dev 而不是直接 PR 到 master

- 多个功能之间可能有**隐性冲突**（比如两个功能都改了同一个组件），在 dev 上累积合并能尽早暴露
- 每次 merge 到 dev 都跑 CI，确保功能之间**组合后仍然正确**
- FINALIZE 时为每个功能创建独立 PR 到 master，squash merge 后 master 上每个功能对应一个 commit，历史清晰可回滚

---

## 循环总览

```
首次启动：从 master 创建 dev/backlog-batch-{date} 分支

每个功能项根据复杂度走不同路径：

  ┌─────────────────────────────────────────────────────┐
  │              loop-state.json                         │
  │  记录 dev 分支名、当前进度（含 Phase 内细粒度 checkpoint）  │
  │  每个 Phase 启动时校验状态一致性                            │
  └───────────┬──────────────────────────────────────────────┘
              │
              │  复杂度自动评估（见"复杂度自动评估标准"）
              │
              ├─── 低复杂度 → 快速通道（FAST_TRACK）— 3 sessions
              │      Session 1: 单会话完成 DESIGN+IMPLEMENT（无 spec/plan 文件）
              │      Session 2: VERIFY（自动门禁 + 代码规范扫描 + 对抗审查）
              │      Session 3: MERGE
              │
              ├─── 中复杂度 → 合并模式（DESIGN_IMPLEMENT）— 3 sessions
              │      Session 1: 单会话完成探索+精简 spec/plan+TDD 实现
              │      Session 2: VERIFY（自动门禁 + 代码规范扫描 + 对抗审查）
              │      Session 3: MERGE
              │
              └─── 高复杂度 → 完整流程 — 4 sessions
              │
     Session 1│  DESIGN（架构师会话）
              │  在 dev 分支上工作
              │  读 backlog → 深度探索 → /brainstorming → 输出 spec + plan
              │  更新 loop-state: phase → IMPLEMENT
              ▼
     Session 2│  IMPLEMENT（开发者会话）
              │  从 dev 拉 feat/{slug} 分支
              │  读 plan → TDD 实现 → push feat 分支
              │  更新 loop-state: phase → VERIFY
              ▼
     Session 3│  VERIFY（审查者会话 — 独立视角）
              │  在 feat/{slug} 上审查
              │  读 spec + diff → 自动化门禁 → 代码规范扫描 → 对抗审查
              │  PASS → phase → MERGE
              │  FAIL → 写修复清单 → phase → FIX
              ▼
     Session 4│  MERGE 或 FIX
              │  MERGE: feat/{slug} → merge 到 dev → 跑 CI
              │         CI 通过 → 更新 backlog → 下一条
              │         CI 失败 → phase → MERGE_FIX
              │  FIX: 在 feat 分支修复 → push → 回 VERIFY
              ▼
            下一个功能项（回到对应复杂度路径）
            ⚠️ 关键：下一条的 DESIGN/DESIGN_IMPLEMENT 基于 dev 最新代码探索
                     下一条的 IMPLEMENT 从 dev 最新代码拉新分支
                     确保每个功能都建立在前序功能已合入的基础上

全部功能完成后：
     Final   │  FINALIZE
              │  为每个功能创建独立 feat→master PR（按完成顺序）
```

---

## 状态文件：loop-state.json

位置：`loop/loop-state.json`（**不纳入 Git 跟踪**，通过 `.gitignore` 排除）

```jsonc
{
  "dev_branch": "dev/backlog-batch-2026-03-23",  // 本轮集成分支
  "current_item_id": 1,           // 当前功能的 queue id（非数组下标）
  "current_phase": "DESIGN",      // DESIGN | DESIGN_IMPLEMENT | IMPLEMENT | VERIFY | FIX | MERGE | MERGE_FIX | FINALIZE | CI_FIX | AWAITING_HUMAN_REVIEW | FAST_TRACK
  "branch": null,                 // 当前功能分支名
  "spec_path": null,              // spec 文件路径
  "plan_path": null,              // plan 文件路径
  "fix_list_path": null,          // 审查修复清单路径

  // ── 并发保护 ──
  "lock": {
    "session_id": null,           // 当前持有锁的会话 UUID
    "acquired_at": null           // ISO8601，锁获取时间（超 30 分钟视为过期，可强制接管）
  },

  // ── 工作区恢复 ──
  "stashed": false,               // 状态校验时是否 stash 了未 commit 的修改

  // ── 细粒度 checkpoint（Phase 内断点续传）──
  "implement_progress": {
    "current_chunk": 0,           // 当前 Chunk 序号
    "current_task": 0,            // 当前 Task 序号
    "last_committed_task": null,  // 最后一个已 commit 的 Task 描述
    "last_commit_sha": null,      // 最后一次 commit 的 SHA，用于校验
    "current_step_attempts": 0    // 当前 Step 失败重试次数（跨会话持久化，≥5 触发 STUCK）
  },

  // ── 重试计数 ──
  "verify_attempts": 0,           // 当前功能已审查次数（≤3）
  "merge_fix_attempts": 0,        // 合入 dev 后 CI 修复次数（≤3）

  // ── 完成记录 ──
  "completed": [],                // [{ "id": 1, "name": "...", "slug": "remove-default-brands" }]
  "blocked": [],                  // [{ "id": 1, "name": "...", "slug": "...", "reason": "..." }]

  // ── FINALIZE 产出 ──
  "pr_numbers": [],               // FINALIZE 阶段创建的 PR 编号数组，按完成顺序
  "deferred_prs": [],             // 有依赖关系需等待前序 PR 合并后处理的功能
  "blocked_issue": null,          // blocked/deferred 汇总 Issue 编号

  // ── 功能队列 ──
  "queue": [
    { "id": 1, "name": "...", "slug": "remove-default-brands", "status": "pending", "depends_on": [], "complexity": "低" }
  ],
  "updated_at": "ISO8601"
}
```

**规则：**
- 每个阶段开始前读取并**校验**、结束后更新并保存此文件（**不 commit 到 Git**）
- `implement_progress` 在每个 Task 完成后更新，确保中断可恢复
- 此文件不纳入 Git 跟踪（已加入 `.gitignore`），避免多分支间的合并冲突

---

## 热插入任务：inbox 目录

在循环运行过程中（或停止时），可以**随时**向 `loop/inbox/` 目录添加任务文件。

### 使用方式

创建一个 `.md` 文件到 `loop/inbox/`：

```markdown
---
name: 功能名称
complexity: 中
depends_on: []
---

- 需求描述第一行
- 需求描述第二行
```

编排脚本在每轮会话启动前自动：
1. 扫描 `inbox/*.md`，按创建时间排序
2. 解析 frontmatter，自动分配递增 ID
3. 移动文件到 `loop/tasks/{id}-{slug}.md`（git-tracked）
4. 写入 `loop-state.json` 的 queue（含 `desc_path` 指向 tasks/ 文件）
5. 全量生成 `backlog.md` 并 commit

| 字段 | 必填 | 默认值 | 说明 |
|------|------|--------|------|
| `name` | 是 | — | 任务名称 |
| `complexity` | 否 | `中` | `低` / `中` / `高` |
| `depends_on` | 否 | `[]` | 依赖的任务 ID 列表 |

### 数据流

```
inbox/*.md（外部写入）
    │ process_inbox()
    ├─ 解析 + 分配 ID
    ├─ 移动到 tasks/{id}-{slug}.md
    ├─ 写入 loop-state.json queue
    ├─ 全量生成 backlog.md
    └─ commit
```

### 规则
- 一个文件 = 一个任务
- 文件名用英文短横线分隔（如 `invite-bug.md`）
- 不需要关心 ID、loop 状态、当前分支
- `inbox/` 是 git-ignored，`tasks/` 是 git-tracked
- 创建 `inbox/STOP` 文件可以停止循环

---

## 通用步骤：状态校验（每个 Phase 启动时执行）

> 每个 Phase 的第一步不是"开始干活"，而是"确认接手状态正确"。

> 注：以下脚本为**伪代码**，展示校验逻辑。Agent 应先读取 loop-state.json 提取字段值，再执行对应的校验命令。`${dev_branch}` 等变量需由 Agent 从 JSON 中获取后替换。

```bash
# 1. 校验 loop-state.json 可解析
cat loop/loop-state.json | python3 -c "import sys,json; json.load(sys.stdin)" \
  || { echo "FATAL: loop-state.json 不是合法 JSON"; exit 1; }

# 2. 校验分支存在性
git rev-parse --verify "${dev_branch}" 2>/dev/null \
  || { echo "FATAL: dev 分支 ${dev_branch} 不存在"; exit 1; }

# 3. 如果 branch 字段非空，校验功能分支存在性
#    注意：IMPLEMENT 阶段启动时分支尚未创建（DESIGN 只预设了分支名），
#    因此仅在 phase ∈ {VERIFY, FIX, MERGE, MERGE_FIX} 时校验
if [ -n "${branch}" ] && echo "VERIFY FIX MERGE MERGE_FIX" | grep -qw "${current_phase}"; then
  git rev-parse --verify "${branch}" 2>/dev/null \
    || { echo "FATAL: 功能分支 ${branch} 不存在"; exit 1; }
fi

# 4. 如果 spec_path 非空，校验文件存在性
if [ -n "${spec_path}" ]; then
  [ -f "${spec_path}" ] || { echo "FATAL: spec 文件 ${spec_path} 不存在"; exit 1; }
fi

# 5. 如果 plan_path 非空，校验文件存在性
if [ -n "${plan_path}" ]; then
  [ -f "${plan_path}" ] || { echo "FATAL: plan 文件 ${plan_path} 不存在"; exit 1; }
fi

# 6. 并发保护 — 检查锁
if lock.session_id 非空 且 lock.acquired_at 距今 < 30 分钟; then
  echo "FATAL: 另一个会话 ${lock.session_id} 正在操作（${lock.acquired_at}），拒绝启动"
  exit 1
fi
# 获取锁（生成新 UUID，记录当前时间）
loop_state.lock = { "session_id": "$(uuidgen)", "acquired_at": "$(date -u +%FT%TZ)" }
# 保存 loop-state.json

# 7. 工作目录干净检查（防止上一个会话崩溃留下未 commit 的修改）
if [ -n "$(git status --porcelain)" ]; then
  echo "WARN: 工作目录不干净，自动 stash"
  git stash push -m "auto-stash before ${current_phase}"
  loop_state.stashed = true    # 记录到 loop-state.json，后续恢复用
fi
```

**stash 恢复（在 Phase 切换到目标分支并 `npm install` 后执行）：**
```bash
if [ "${stashed}" = "true" ]; then
  git stash pop || echo "WARN: stash pop 冲突，保留 stash，手动处理: git stash list"
  loop_state.stashed = false    # 无论成功与否都重置标记
fi
```
> Agent 应在切换到目标分支并 `npm install` 之后、开始实际工作之前执行 stash 恢复。如果 pop 有冲突，保留 stash 不丢弃，输出 WARN 继续工作（新会话会从 checkpoint 重做，stash 中的内容可供人工参考）。

**锁释放：** 每个 Phase 正常结束时（更新 loop-state 时），清空 `lock` 字段：`{ "session_id": null, "acquired_at": null }`。

**校验失败处理：**
- `FATAL` 级：停止执行，输出诊断信息，等待人工介入
- `WARN` 级：记录日志，尝试自愈（如从 Git 历史推断正确状态），继续执行

---

## Phase 0: INIT（初始化 — 仅首次）

循环开始时执行一次，创建 dev 集成分支。

### 操作

```bash
# 确保在最新 master 上
git checkout master
git pull origin master

# 检查 master 分支保护状态（安全红线的系统级保障）— 硬阻断，不可跳过
gh api repos/{owner}/{repo}/branches/master/protection 2>/dev/null \
  && echo "✅ master 分支保护已开启" \
  || { echo "FATAL: master 分支保护未开启。请人工配置后再启动循环。"; exit 1; }

# 校验 queue 完整性：depends_on 中的 id 必须存在于 queue 中
# Agent 应遍历 queue 中每个 item 的 depends_on，确认每个 dep_id 在 queue[*].id 中存在
# 如有不存在的 id → FATAL，停止执行（否则该任务会变成幽灵 pending，永远不执行也不 BLOCKED）

# 创建 dev 集成分支（自动避免同名冲突）
DEV_BRANCH="dev/backlog-batch-2026-03-23"
if git rev-parse --verify "${DEV_BRANCH}" 2>/dev/null; then
  # 同名分支已存在（同日多轮循环），追加序号
  N=2
  while git rev-parse --verify "${DEV_BRANCH}-${N}" 2>/dev/null; do N=$((N+1)); done
  DEV_BRANCH="${DEV_BRANCH}-${N}"
fi
git checkout -b "${DEV_BRANCH}"
git push -u origin "${DEV_BRANCH}"
```

更新 loop-state.json：
```json
{ "dev_branch": "${DEV_BRANCH}" }
```
> 注意：`DEV_BRANCH` 的值由上述脚本动态确定（可能带序号后缀），需使用实际值。

保存 loop-state.json。

> ℹ️ INIT 时确认 `loop/loop-state.json` 已在 `.gitignore` 中，如未添加则执行：
> ```bash
> grep -q "loop-state.json" .gitignore || echo "loop/loop-state.json" >> .gitignore
> git add .gitignore && git commit -m "chore: gitignore loop-state.json"
> ```

---

## 复杂度自动评估标准

> **复杂度不再依赖 inbox 手工标注。** 由首个接手会话（DESIGN / DESIGN_IMPLEMENT / FAST_TRACK）在读取需求并初步探索代码后，根据以下客观标准动态评估，结果回写 queue item 的 `complexity` 字段。

### 评估规则

**低（→ FAST_TRACK）** — 同时满足以下全部条件：
- 不涉及数据库 migration
- 不新增 API 路由
- 预计改动文件 ≤ 5 个
- 不涉及新前端页面/组件（只修改已有组件的属性/样式/文案）
- 需求描述 ≤ 3 行且无歧义

**高（→ DESIGN → IMPLEMENT 分离模式）** — 满足以下任一条件：
- 同时涉及数据库 migration + 新增 API 路由 + 新增前端页面
- 需求描述 > 20 行或包含多个独立子功能
- 影响 3 个以上独立模块（如同时涉及 auth + workspace + planning）

**中（→ DESIGN_IMPLEMENT 合并模式）** — 其余情况

### 评估时机

1. 会话读取任务需求描述后
2. 快速浏览相关文件（≤5 个文件，不做深度分析）
3. 判定复杂度并回写 `loop-state.json` queue 中该任务的 `complexity` 字段
4. 如果评估结果与当前 `current_phase` 不匹配（例如进入了 DESIGN 但评估为低），更新 `current_phase` 到正确路径并结束当前会话

### 复杂度 → 路径映射

| 复杂度 | 路径 | 总会话数 | 产出物 |
|--------|------|----------|--------|
| 低 | FAST_TRACK → VERIFY → MERGE | 3 | commit message 记录决策 |
| 中 | DESIGN_IMPLEMENT → VERIFY → MERGE | 3 | 精简 spec + plan |
| 高 | DESIGN → IMPLEMENT → VERIFY → MERGE | 4 | 完整 spec + plan（含 /brainstorming） |

---

## 快速通道：FAST_TRACK（低复杂度任务）

> 对复杂度评估为"低"的任务，DESIGN + IMPLEMENT 合并为一个会话，跳过完整的 spec/plan 流程。

### 适用条件

- 复杂度自动评估结果为"低"（见上方评估规则）

### 启动 Prompt

```
阅读 loop/autonomous-dev-loop.md 的 FAST_TRACK 部分。
阅读 loop/loop-state.json 获取当前任务。
读取 loop-state.json 中当前任务的 desc_path 字段指向的文件，获取需求描述。
如果 desc_path 为空（历史任务），读取 backlog.md 中对应条目。

你同时担任架构师和开发者。对当前低复杂度任务执行快速通道：
探索代码 → 创建功能分支 → TDD 实现 → push。
不需要写独立的 spec/plan 文件，在 commit message 中说明设计决策即可。
全程自主工作，不要问我问题。
```

### 工作流程

1. **执行状态校验**（见"通用步骤：状态校验"）
2. **检查是否为断点续传：**
   - 如果 `branch` 非空且分支已存在（上次 FAST_TRACK 中断）：
     ```bash
     git checkout feat/{slug}
     npm install
     git log --oneline | head -5    # 查看已有 commit
     ```
     直接从已有分支继续（跳到 Step 6），不重新创建分支
   - 如果 `branch` 非空但分支不存在 → 清空 `branch`，按新任务处理
3. 读取 loop-state.json 中当前任务的 desc_path 字段指向的文件，获取需求描述。如果 desc_path 为空（历史任务），读取 backlog.md 中对应条目
4. **在 dev 上探索代码**（限定范围：只看直接相关的 2-3 个文件）
5. 从 dev 创建功能分支 + 环境同步：
   ```bash
   git checkout {dev_branch}
   git pull origin {dev_branch}
   npm install
   git checkout -b feat/{slug}
   ```
6. **立即保存断点**（确保中断后可恢复）：
   更新 loop-state.json：`{ "branch": "feat/{slug}" }`，保存 loop-state.json
   > ⚠️ 必须在创建分支后立即保存 branch 到 state。否则中断后新会话会尝试 `git checkout -b` 创建同名分支而报错。
7. TDD 实现：写测试 → 确认失败 → 实现 → 确认通过
8. 硬性门禁（与完整流程标准一致）：
   ```bash
   npm run lint && npm test && npm run build
   npx tsc --noEmit -p frontend           # vite build 不做类型检查
   ```
   > 注：`npm run build` 的后端部分已包含 `tsc` 编译，无需再单独执行 `npx tsc --noEmit -p backend`。
9. 更新并保存 loop-state.json：
    ```json
    {
      "current_phase": "VERIFY",
      "spec_path": null,
      "plan_path": null
    }
    ```
10. commit + push（commit message 包含设计决策说明）

### FAST_TRACK 的 VERIFY

VERIFY 阶段仍然是独立会话，但检查范围缩小：
- 自动化门禁（必须全部通过）
- diff 审查聚焦于：功能完整性、不引入副作用、测试覆盖
- 不要求独立 spec 文件对照（对照 backlog.md 原始需求 + commit message）

---

## 合并模式：DESIGN_IMPLEMENT（中复杂度任务）

> 对复杂度评估为"中"的任务，DESIGN + IMPLEMENT 合并为一个会话，产出精简版 spec/plan 并直接实现。相比完整流程减少 1 个会话的上下文重建成本。

### 适用条件

- 复杂度自动评估结果为"中"（见"复杂度自动评估标准"）

### 启动 Prompt

```
阅读 loop/autonomous-dev-loop.md 的"合并模式：DESIGN_IMPLEMENT"部分。
阅读 loop/loop-state.json 获取当前任务。
读取 loop-state.json 中当前任务的 desc_path 字段指向的文件，获取需求描述。
如果 desc_path 为空（历史任务），读取 backlog.md 中对应条目。

你同时担任架构师和开发者。对当前中复杂度任务执行合并模式：
探索代码 → 写精简 spec/plan → 创建功能分支 → TDD 实现 → push。
全程自主工作，不要问我问题。
```

### 工作流程

1. **执行状态校验**（见"通用步骤：状态校验"）
2. 读取 loop-state.json，确定当前功能项和 dev 分支名
3. **复杂度确认：** 读取需求描述 + 快速浏览相关文件（≤5 个），按"复杂度自动评估标准"判定：
   - 如果实际为"低"：更新 `current_phase = "FAST_TRACK"`，保存 loop-state.json，结束会话
   - 如果实际为"高"：更新 `current_phase = "DESIGN"`，保存 loop-state.json，结束会话
   - 确认为"中"：继续
4. **切到 dev 最新代码**：
   ```bash
   git checkout {dev_branch}
   git pull origin {dev_branch}
   npm install
   ```
5. **探索代码**（中等深度：相关的 5-10 个文件）：
   - 找到相关的数据模型、API 路由、前端组件
   - 理解当前实现的完整上下文
6. **读取当前 migration 版本号**（如果需求涉及数据库变更）：
   ```bash
   grep -n "MIGRATION_VERSION" backend/src/storage/migrations.ts | head -3
   ```
7. **写精简版 spec**（`docs/superpowers/specs/{date}-{slug}-design.md`）：
   - 核心设计决策（为什么这样做）
   - API 接口变更（如有，含请求/响应类型）
   - 数据库 migration SQL（如有，版本号 `CURRENT + 1`）
   - 关键文件变更列表
   - **不需要** /brainstorming，不需要逐字段的完整格式
8. **写精简版 plan**（`docs/superpowers/plans/{date}-{slug}.md`）：
   - Chunk → Task 结构（**不要求 Step 级别细节**）
   - 每个 Task 标注要修改的文件列表
   - TDD：每个功能 Task 先写测试，再写实现
9. **从 dev 创建功能分支 + 环境同步：**
   ```bash
   git checkout {dev_branch}
   git pull origin {dev_branch}
   npm install
   git checkout -b feat/{slug}
   ```
10. **立即保存断点：** 更新 loop-state.json：`{ "branch": "feat/{slug}", "spec_path": "...", "plan_path": "..." }`
11. **按 plan 的 Chunk → Task 顺序 TDD 实现**（流程同 IMPLEMENT Phase）
12. **每完成一个 Task，执行硬性门禁：**
    ```bash
    npm run lint
    npm test
    npx tsc --noEmit -p frontend
    ```
    全部 exit 0 才能 commit。
13. **commit + push + 更新 checkpoint**（流程同 IMPLEMENT Phase 的 step 8）
14. 全部 Task 完成后，执行最终门禁：
    ```bash
    npm run lint && npm test && npm run build
    npx tsc --noEmit -p frontend
    ```
15. 更新并保存 loop-state.json：`{ "current_phase": "VERIFY" }`
16. commit + push spec + plan + 代码到功能分支

### 上下文熔断

如果探索+设计阶段（Step 3-8）消耗 >40% 的上下文窗口（通常体现为已处理大量文件和代码），应：
1. 保存已写好的 spec 和 plan 到文件，commit + push 到 dev 分支
2. 更新 loop-state.json：`{ "current_phase": "IMPLEMENT", "spec_path": "...", "plan_path": "..." }`
3. 结束当前会话，让编排脚本启动新的 IMPLEMENT 会话继续

### 卡住时的处理

同 IMPLEMENT Phase 的"卡住时的处理"。

---

## Phase 1: DESIGN（仅高复杂度任务 — 架构师会话）

> **中复杂度任务使用 DESIGN_IMPLEMENT 合并模式（见上方章节），不进入此阶段。** 此阶段仅用于复杂度评估为"高"的任务，需要深度探索、/brainstorming 辅助设计、完整 spec 和 plan。

### 启动 Prompt

```
阅读 loop/autonomous-dev-loop.md 的 Phase 1: DESIGN 部分。
阅读 loop/loop-state.json 获取当前任务。
读取 loop-state.json 中当前任务的 desc_path 字段指向的文件，获取需求描述。
如果 desc_path 为空（历史任务），读取 backlog.md 中对应条目。

你是架构师。你的任务是为当前高复杂度 backlog 条目输出完整 spec 和 plan。
确保在 dev 分支上工作（分支名见 loop-state.json 的 dev_branch 字段）。
使用 /brainstorming 技能辅助设计（自主推进模式，不等待确认）。
全程自主工作，不要问我问题。
```

### 工作流程

1. **执行状态校验**（见"通用步骤：状态校验"）
1.5. **复杂度确认：** 快速浏览需求和相关代码，按"复杂度自动评估标准"判定。如果实际为"低"或"中"，更新 `current_phase` 到对应路径并结束会话。确认为"高"后继续。
2. 读取 loop-state.json，确定当前功能项和 dev 分支名
3. **切到 dev 最新代码**（包含所有前序功能）：
   ```bash
   git checkout {dev_branch}
   git pull origin {dev_branch}
   npm install                    # 确保依赖与分支一致
   ```
   > ⚠️ 必须 pull 最新 dev 并 `npm install`。前一个功能已合入 dev，可能变更了依赖，当前功能的设计必须基于包含前序功能的代码和依赖。
4. 读取 loop-state.json 中当前任务的 desc_path 字段指向的文件，获取需求描述。如果 desc_path 为空（历史任务），读取 backlog.md 中对应条目
5. **深度探索代码**（这是最关键的步骤）：
   - 找到所有相关的数据模型、API 路由、前端组件、store 状态
   - 理解当前实现的完整上下文（**基于 dev 分支的最新代码**，包含已合入的前序功能）
   - 识别影响范围和与已合入功能的潜在冲突
6. **读取当前 migration 版本号**（如果需求涉及数据库变更）：
   ```bash
   grep -n "MIGRATION_VERSION" backend/src/storage/migrations.ts | head -3
   ```
   在 spec 中使用 `当前版本 + 1`，不硬编码具体数字。
7. **根据复杂度选择设计路径：**

   **路径 A — 高复杂度任务**（queue 中当前任务 `complexity = "高"`）：

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
   确认两个文件已生成后，**直接跳到步骤 8**。

   **路径 B — 备用手动设计**（如 brainstorming 不可用或 AI 主动选择）：

   手动编写 spec 和 plan：
   - 写 **spec**（`docs/superpowers/specs/{date}-{slug}-design.md`，头部日期精确到分钟如 `2026-03-23 11:54`）：
     - 数据模型变更（含 migration SQL，版本号标注为 `CURRENT + 1`）
     - API 接口变更（路由、请求体、响应体）
     - 前端组件变更（PC + 移动端）
     - 对"待定"项做出决策并记录理由
     - **需求追溯：** 每个 spec 章节标注对应的 backlog 需求点
   - 写 **plan**（`docs/superpowers/plans/{date}-{slug}.md`）：
     - Chunk → Task → Step 结构
     - 每个 Task 标注要修改/新建的文件列表
     - TDD：每个功能 Task 先写测试 Step，再写实现 Step
     - 包含可直接执行的代码片段（参考现有 plan 格式）

8. 更新 loop-state.json：
   ```json
   { "current_phase": "IMPLEMENT", "branch": "feat/{slug}", "spec_path": "...", "plan_path": "..." }
   ```
   同时将 `spec_path` 回写到 queue 中当前任务的 item（添加 `spec_path` 字段），
   并更新对应 `tasks/` 文件的 frontmatter（添加 `spec_path` 字段）。
9. commit 并 push spec + plan 到 dev 分支，保存 loop-state.json

### 质量要求

**无论走路径 A 还是路径 B，最终产出的 spec 和 plan 都必须满足：**

- spec 中的每个 API 必须定义请求/响应类型
- plan 中的每个 Task 必须有 `**Files:**` 列表
- 涉及 UI 的必须覆盖 PC + 移动端
- 数据库变更必须写完整 migration SQL（版本号用 `CURRENT + 1` 占位，IMPLEMENT 时动态确定）
- spec 中每个章节必须追溯到 backlog 需求点

> 路径 A 的 spec 已经过 brainstorming 内置的 spec-reviewer 子代理审查；路径 B 的 spec 由架构师自行把关。两条路径都要确保上述四项达标后才能进入 IMPLEMENT。

---

## Phase 2: IMPLEMENT（开发者会话）

### 启动 Prompt

```
阅读 loop/autonomous-dev-loop.md 的 Phase 2: IMPLEMENT 部分。
阅读 loop/loop-state.json 获取当前任务、dev 分支名和 plan 路径。
阅读对应的 plan 文件。

你是开发者。严格按照 plan 逐步实现，使用 TDD。
功能分支从 dev 分支拉出（不是从 master）。
全程自主工作，不要问我问题。
```

### 工作流程

1. **执行状态校验**（见"通用步骤：状态校验"）
2. 读取 loop-state.json，获取 dev 分支名、plan 路径
3. **检查是否为断点续传：**
   - 如果 `implement_progress.last_commit_sha` 非空：
     ```bash
     # 验证功能分支存在且包含该 commit
     git checkout feat/{slug}
     git log --oneline | head -5
     # 验证 last_commit_sha 在分支历史中（防止 force-push 或 reset 导致 SHA 丢失）
     git branch --contains {last_commit_sha} | grep -q "feat/{slug}" \
       || echo "WARN: last_commit_sha 不在分支历史中，可能需要从头开始"
     ```
   - **以 `implement_progress` 为主源**（与 commit SHA 绑定，可验证），plan 的 `[x]` 作为辅助参考。当两者冲突时，以 `git log` 中能找到 `last_commit_sha` 且对应的 task 为准
   - 从 `implement_progress.current_task` 继续，不重做已完成的 Task
   - 如果功能分支不存在或状态异常 → 从头开始（但先确认不会覆盖已有工作）
4. **从 dev 最新代码创建功能分支**（如果还没有）：
   ```bash
   git checkout {dev_branch}
   git pull origin {dev_branch}       # 必须 pull，确保包含前序功能
   npm install                        # 确保依赖与分支一致
   git checkout -b {branch}           # branch 值从 loop-state.json 读取（DESIGN 阶段已设定）
   ```
   > ⚠️ 功能分支必须从 dev 最新代码拉出，不是从 master。切分支后必须 `npm install`。分支名从 loop-state.json 的 `branch` 字段读取，由 DESIGN 阶段统一命名。
5. **动态确定 migration 版本号**（如果 plan 涉及数据库变更）：
   ```bash
   grep "MIGRATION_VERSION" backend/src/storage/migrations.ts
   ```
   将 plan/spec 中的 `CURRENT + 1` 替换为实际值。
6. **严格按 plan 的 Chunk → Task → Step 顺序执行：**
   - 每个 Step 完成后在 plan 文件中勾选 `- [x]`
   - TDD 节奏：写测试 → 确认失败 → 实现 → 确认通过
7. **每完成一个 Task，执行硬性门禁：**
   ```bash
   npm run lint                           # 必须 exit 0
   npm test                               # 必须 exit 0
   npx tsc --noEmit -p frontend           # 必须 exit 0（vite build 不做类型检查）
   ```
   **全部 exit 0 才能 commit。** 如果失败，修复后重跑，不可跳过。
8. **commit + push + 更新 checkpoint：**
   ```bash
   git add <具体文件>
   git commit -m "feat({slug}): {task 描述}"
   git push -u origin feat/{slug}
   ```
   更新 loop-state.json 的 `implement_progress`：
   ```json
   {
     "implement_progress": {
       "current_chunk": 1,
       "current_task": 3,
       "last_committed_task": "Task 描述",
       "last_commit_sha": "{刚才的 commit SHA}"
     }
   }
   ```
   保存 loop-state.json（**每完成一个 Task 就更新，不等全部完成**）。
9. 全部 Task 完成后，执行最终门禁：
   ```bash
   npm run lint && npm test && npm run build
   npx tsc --noEmit -p frontend       # vite build 不做类型检查，需单独验证
   ```
10. 更新并保存 loop-state.json：`{ "current_phase": "VERIFY" }`

### 卡住时的处理

- **测试写不出来：** 检查 spec 是否有歧义，如有则在 plan 中记录偏差并继续
- **现有代码有 bug 挡路：** 在当前分支修复，commit message 标注 `fix:`
- **plan 步骤明显有误：** 修正 plan 后继续，在 plan 顶部记录偏差
- **同一步骤失败 5 次：** 通过 `implement_progress.current_step_attempts` 计数（**每次重试递增并保存到 loop-state.json**，跨会话持久化）。达到 5 次后标注 `[STUCK]`。**跳过前检查：** 后续 Task 是否依赖此 Task（看 `depends` 标注）。如果有依赖，将依赖链上的所有 Task 也标注 `[STUCK]`。在 plan 顶部记录原因。进入下一个 Task 时重置 `current_step_attempts = 0`
- **上下文接近 60% 消耗：** 立即保存 `implement_progress` + commit + push，在 plan 顶部写 `[CONTEXT_BREAK at Task X]`，结束当前会话

---

## Phase 3: VERIFY（审查者会话）

> **核心价值：这是一个全新的 Claude Code 会话，没有开发过程的上下文偏见。它同时审查"实现是否符合 spec"和"spec 是否覆盖了原始需求"。**

### 启动 Prompt

```
阅读 loop/autonomous-dev-loop.md 的 Phase 3: VERIFY 部分。
阅读 loop/loop-state.json 获取当前任务、功能分支名和 spec 路径。
读取 loop-state.json 中当前任务的 desc_path 字段指向的文件，获取原始需求描述。
如果 desc_path 为空（历史任务），读取 backlog.md 中对应条目。
如果 spec_path 非 null，阅读对应的 spec 文件（路径见 loop-state.json 的 spec_path 字段）。
如果 spec_path 为 null（FAST_TRACK 任务），跳过 spec 阅读，改为阅读功能分支的全部 commit：
`git log {dev_branch}..feat/{slug} --format="%H %s%n%b"` 获取相对于 dev 的所有 commit message + body。

你是代码审查者。你的目标是**找到问题**，而不是确认通过。

强制要求：
1. 运行 Step 2.5 的自动化代码规范扫描，任何 WARN 必须在 review 中分类记录
2. review 文件中必须至少列出 2 个发现（含 Minor 级别），如果真的完美无缺，
   必须逐项写出排查过程（查看了哪些文件的哪些行）来论证为何无问题
3. 对每个检查项写出具体排查行为（查看了哪个文件的哪些行），不只是打勾
4. 禁止使用"整体质量良好"、"代码结构清晰"等模糊正面结论

你的职责是验证：
1. spec（或 FAST_TRACK 的 commit message）是否完整覆盖了 backlog 中的原始需求
2. 实现是否正确且符合 spec 和项目规范
全程自主工作，不要问我问题。
```

### 工作流程

#### Step 0: 状态校验

执行"通用步骤：状态校验"。

#### Step 1: 需求覆盖审查

**如果 `spec_path` 为 null（FAST_TRACK 任务）：** 跳过本步骤的 spec 对照检查。Step 3 中对照 backlog.md 原始需求 + 功能分支全部 commit message（`git log {dev_branch}..feat/{slug} --format="%H %s%n%b"`）进行审查。

**否则（完整流程任务）：** 读取 desc_path 指向的文件（或 backlog.md 中对应条目）的**原始需求**，与 spec 对照：

| # | 检查项 | 判定方法 |
|---|--------|----------|
| 0a | **需求完整性** | backlog 中的每个需求点在 spec 中都有对应设计 |
| 0b | **决策合理性** | spec 中对"待定"项的决策是否合理，是否遗漏了关键场景 |
| 0c | **过度设计** | spec 是否包含 backlog 未要求的功能（避免范围蔓延） |

如果发现 spec 本身有重大缺陷 → 直接判 FAIL，修复清单标注"spec 层面问题"，FIX 阶段需要同时修正 spec 和实现。

#### Step 2: 自动化门禁（硬性，不可跳过）

```bash
# 切到功能分支
git checkout feat/{slug}
npm install                    # 确保依赖与分支一致

# 以下全部必须 exit 0，任何一个失败 = 直接 FAIL
npm run lint
npm test
npm run build
npx tsc --noEmit -p frontend
```

> 注：`npm run build` 的后端部分已包含 `tsc` 编译，无需再单独执行 `npx tsc --noEmit -p backend`。前端的 `vite build` 不做类型检查，因此需要单独执行 `npx tsc --noEmit -p frontend`。

如果任何一个失败 → 直接判定 FAIL，将失败输出写入修复清单。

#### Step 2.5: 增量代码规范扫描（硬性，不可跳过）

> 这些检查针对功能分支相对于 dev 的增量变更，捕获 lint 无法检测的项目规范违规。

```bash
# 2.5a: 检查硬编码颜色值（应使用 tokens.json / Tailwind theme）
git diff {dev_branch}...feat/{slug} -- '*.tsx' '*.ts' | grep '^+' | grep -v '^+++' \
  | grep -E '#[0-9a-fA-F]{6}\b|rgba?\(' | grep -v '// allow-color' \
  && echo "WARN: 新增代码包含硬编码颜色值"

# 2.5b: 检查新增代码中的 emoji 图标（应使用 Lucide React 图标库）
git diff {dev_branch}...feat/{slug} -- '*.tsx' | grep '^+' | grep -v '^+++' \
  | grep -P '[\x{1F300}-\x{1F9FF}]' \
  && echo "WARN: 新增代码使用了 emoji 作为图标"

# 2.5c: 检查新增 any 类型（排除 eslint-disable 行）
git diff {dev_branch}...feat/{slug} -- '*.ts' '*.tsx' | grep '^+' | grep -v '^+++' \
  | grep -v 'eslint-disable' | grep ': any\b' \
  && echo "WARN: 新增代码包含 any 类型"

# 2.5d: 检查原生 HTML 组件（应使用 shadcn/ui 的 Select/Dialog/AlertDialog）
git diff {dev_branch}...feat/{slug} -- '*.tsx' | grep '^+' | grep -v '^+++' \
  | grep -v 'components/ui' | grep -iE '<(select|dialog|alert)\b' \
  && echo "WARN: 可能使用了原生 HTML 组件而非 shadcn/ui"

# 2.5e: 检查新建 CSS 文件（新代码应使用 Tailwind）
git diff {dev_branch}...feat/{slug} --name-only --diff-filter=A | grep '\.css$' \
  && echo "WARN: 新建了 CSS 文件，新代码应使用 Tailwind"
```

**WARN 分类规则：**
| WARN 类型 | 严重级别 | 是否阻塞 |
|-----------|----------|----------|
| 硬编码颜色值 | **Major** | 阻塞 — 必须修复 |
| 新增 any 类型 | **Major** | 阻塞 — 必须修复 |
| 原生 HTML 组件 | **Major** | 阻塞 — 必须修复（除非是 `<input>` 等无 shadcn 替代的基础元素） |
| emoji 作为图标 | **Minor** | 不阻塞 — 记录但不要求修复 |
| 新建 CSS 文件 | **Minor** | 不阻塞 — 记录但不要求修复（除非是全新组件） |

任何 WARN 必须在 review 文件中记录，即使判定为 Minor 不阻塞。

#### Step 3: diff 审查（逐文件）

```bash
# 注意：diff 基准是 dev 分支，不是 master
git diff {dev_branch}...feat/{slug} --stat
git diff {dev_branch}...feat/{slug}
```

对照 spec 文件 + backlog 原始需求，逐项检查：

| # | 检查项 | 判定方法 |
|---|--------|----------|
| 1 | **功能完整性** | spec 中的每个需求点在 diff 中都有对应实现 |
| 2 | **API 契约** | 路由、请求体、响应体与 spec 定义一致 |
| 3 | **数据库 migration** | migration 版本递增、SQL 正确、有 IF NOT EXISTS 防护 |
| 4 | **测试覆盖** | 新增后端路由有对应测试、核心逻辑函数有单元测试 |
| 5 | **移动端实现** | 涉及 UI 的变更同时覆盖 PC 和移动端 |
| 6 | **类型安全** | 新代码无 `any`、时间字段为 `string` 类型 |
| 7 | **UI 规范** | 使用 Tailwind + tokens 颜色、`cn()` 合并 class、shadcn/ui 组件 |
| 8 | **安全** | SQL 参数化、权限中间件覆盖、无敏感信息硬编码 |
| 9 | **副作用检查** | 是否意外修改了不相关的文件或功能 |

#### Step 4: 出具判定

**PASS 条件：** Step 1 无重大问题 + Step 2 全部通过 + Step 2.5 无未处理的 Major WARN + Step 3 无 Critical/Major 问题

**判定结果写入文件** `loop/reviews/{date}-{slug}-review.md`：

```markdown
# Review: {功能名}
**Branch:** feat/{slug}
**Verdict:** PASS | FAIL
**Date:** {date}

## 需求覆盖审查
- [x] backlog 需求完整覆盖
- [x] 决策合理性
- [x] 无过度设计
{如有问题，逐条列出}

## 自动化门禁
- [x] lint
- [x] test
- [x] build
- [x] typecheck frontend

## 代码规范扫描（Step 2.5）
- [ ] 硬编码颜色值: {PASS/WARN — 具体发现}
- [ ] emoji 图标: {PASS/WARN — 具体发现}
- [ ] any 类型: {PASS/WARN — 具体发现}
- [ ] 原生 HTML 组件: {PASS/WARN — 具体发现}
- [ ] 新建 CSS 文件: {PASS/WARN — 具体发现}

## 审查发现
### Critical（必须修复）
- ...
### Major（应该修复）
- ...
### Minor（建议改进，不阻塞）
- ...

## 排查过程
> 对每个检查项写出具体排查行为，不只是打勾。

### 功能完整性
排查范围: {具体文件和行号}
结论: {PASS/FAIL — 具体依据}

### API 契约
排查范围: {具体文件和行号}
结论: {PASS/FAIL/N/A — 具体依据}

### 测试覆盖
排查范围: {具体测试文件}
结论: {PASS/FAIL — 新增测试数量和覆盖范围}

### 移动端实现
排查范围: {具体文件和行号}
结论: {PASS/FAIL/N/A — 具体依据}
```

#### Step 5: 更新状态（⚠️ 必须在 commit/push 之前完成）

> **状态更新优先原则：** 先保存 loop-state.json，再执行 git commit/push。这样即使会话在 commit/push 时中断，编排脚本也能检测到 phase 已推进，下一个会话可以补做收尾。

**5a. 立即更新并保存 loop-state.json：**

- **PASS →** `{ "current_phase": "MERGE" }`
- **FAIL →**
  - `verify_attempts += 1`
  - 如果 `verify_attempts >= 3`：标记 BLOCKED，执行**BLOCKED 字段清空**（见下方），推进到下一条
  - 否则：`current_phase = "FIX"`，`fix_list_path = review 文件路径`

**BLOCKED 字段清空（VERIFY 和 MERGE 通用）：**
```json
{
  "branch": null, "spec_path": null, "plan_path": null, "fix_list_path": null,
  "verify_attempts": 0, "merge_fix_attempts": 0,
  "implement_progress": { "current_chunk": 0, "current_task": 0, "last_committed_task": null, "last_commit_sha": null }
}
```
> 必须清空这些字段，否则下一个任务的 DESIGN 会读到上一个 BLOCKED 任务的脏状态（branch 指向已废弃分支、spec_path 指向无关文件）。

**5b. 收尾操作：** commit 并 push review 文件到功能分支。

---

## Phase 3.5: FIX（修复会话）

当 VERIFY 不通过时，启动新会话修复。

### 启动 Prompt

```
阅读 loop/autonomous-dev-loop.md 的 Phase 3.5: FIX 部分。
阅读 loop/loop-state.json 获取修复清单路径。
阅读修复清单文件。
如果 loop-state.json 的 spec_path 非 null，阅读对应的 spec 文件。
如果 loop-state.json 的 plan_path 非 null，阅读对应的 plan 文件。

你是修复开发者。只修复清单中的 Critical 和 Major 问题，不做额外改动。
如果清单包含"spec 层面问题"，需要同时修正 spec 和对应的实现代码。
修完后 push，全程自主工作，不要问我问题。
```

### 工作流程

1. **执行状态校验**（见"通用步骤：状态校验"）
2. 切到功能分支：`git checkout feat/{slug} && npm install`
3. 读取 review 文件中的 Critical + Major 问题
4. **区分问题类型：**
   - **spec 层面问题：** 先修正 spec 文件，再修正实现代码
   - **实现层面问题：** 直接修正实现代码
5. 逐个修复，每个修复后跑完整门禁：`npm run lint && npm test && npm run build && npx tsc --noEmit -p frontend`
6. 更新并保存 loop-state.json：`{ "current_phase": "VERIFY" }`（触发重新审查）
7. commit + push

---

## Phase 4: MERGE（合入 dev + CI 验证）

> **功能分支通过审查后，合入 dev 集成分支。合入后必须在 dev 上跑 CI，确保与已有功能无冲突。**

### 启动 Prompt

```
阅读 loop/autonomous-dev-loop.md 的 Phase 4: MERGE 部分。
阅读 loop/loop-state.json 获取当前任务和分支信息。

你是集成者。将功能分支合入 dev，跑 CI，更新 backlog，推进到下一个任务。
全程自主工作，不要问我问题。
```

### 工作流程

#### Step 1: 合入前备份 + 合入 dev

```bash
# 确保 dev 是最新的
git checkout {dev_branch}
git pull origin {dev_branch}
npm install                    # 确保依赖与分支一致

# 记录合入前的 dev HEAD，用于安全回退（-f 覆盖同名 tag，防止重试时冲突）
git tag -f "backup/pre-merge-{slug}" HEAD
git push origin "backup/pre-merge-{slug}" -f    # 推到远程，防跨会话崩溃后丢失

# 合并功能分支（保留合并记录）
git merge --no-ff feat/{slug} -m "merge: feat/{slug} into {dev_branch}"
```

如果有**合并冲突**（loop-state.json 已移出 Git，不会再产生该文件的冲突）：
1. 优先保留 dev 上已有功能的逻辑，在此基础上融入新功能
2. `git add` 冲突文件
3. `git commit`（不要用 `--no-edit`，写清楚冲突解决方式）

#### Step 2: 在 dev 上跑 CI（硬性门禁）

```bash
# 合并后可能引入新依赖
npm install

# 以下全部必须在 dev 分支上 exit 0
npm run lint
npm test
npm run build
npx tsc --noEmit -p frontend           # vite build 不做类型检查
```

**四个命令全部 exit 0 = CI 通过。** 任何一个失败 = CI 不通过。

#### Step 3: 根据 CI 结果决定下一步

**CI 通过：**

```bash
# push dev 分支
git push origin {dev_branch}

# 删除备份 tag（已不需要）
git tag -d "backup/pre-merge-{slug}"
git push origin --delete "backup/pre-merge-{slug}" 2>/dev/null || true

# ⚠️ 不删除功能分支，在 FINALIZE 后统一清理，保留回溯能力
```

更新 loop-state.json：
- 当前项移入 `completed`（格式：`{ "id": N, "name": "...", "slug": "..." }`），queue 中对应项 `status = "done"`，`completed_at` 设为当前时间（格式：`YYYY-MM-DD HH:mm`）
- **传播 BLOCKED 状态：** 遍历 queue 中所有 `status = "pending"` 的项，如果其 `depends_on` 中任一 id 在 `blocked` 列表中，将该项也标记为 `blocked`（原因标注"依赖 #N 被阻塞"），并递归传播直到无新增
- `current_item_id` 推进到下一个 queue 中 `status = "pending"` 且 `depends_on` 全部在 `completed` 中的项（**按 queue 数组顺序取第一个满足条件的**）；如无可执行项则设 `current_phase = "FINALIZE"` 并跳过后续步骤
- `current_phase` 根据下一个任务的复杂度设置：`"FAST_TRACK"`（低）、`"DESIGN_IMPLEMENT"`（中）、`"DESIGN"`（高）。如果复杂度尚未评估（默认"中"），设为 `"DESIGN_IMPLEMENT"`。仅当还有可执行项时设置。
- 清空 `branch`, `spec_path`, `plan_path`, `fix_list_path`, `verify_attempts`, `merge_fix_attempts`
- 重置 `implement_progress` 为初始值

保存 loop-state.json。（backlog.md 由编排脚本在下一轮循环开始时自动生成，MERGE 阶段无需修改。）

**CI 失败：**

```bash
# 使用备份 tag 安全回退到合入前状态（比 ORIG_HEAD 更可靠，不受其他 Git 操作影响）
git reset --hard "backup/pre-merge-{slug}"
git tag -d "backup/pre-merge-{slug}"
git push origin --delete "backup/pre-merge-{slug}" 2>/dev/null || true
```

> ⚠️ `reset --hard` 会丢弃所有工作区变更。loop-state.json 和 CI 失败日志均为本地文件，不在 Git 中，不受 reset 影响。

记录 CI 失败日志到 `loop/reviews/{date}-{slug}-ci-fail.md`（**本地文件，不 commit 到 Git**，避免调试日志污染 dev/master 分支）：
```markdown
# CI Failure: {功能名} merge into dev
**Date:** {date}
**Failed command:** npm test / npm run lint / npm run build
**Error output:**
{粘贴关键错误输出}
**Analysis:**
{失败原因分析 — 是功能本身的问题还是与 dev 上已有代码的集成冲突}
```

更新 loop-state.json：
- `merge_fix_attempts += 1`
- `fix_list_path = "loop/reviews/{date}-{slug}-ci-fail.md"`（**必须设置**，MERGE_FIX 会话依赖此路径读取失败日志）
- 如果 `merge_fix_attempts >= 3`：标记 BLOCKED，执行**BLOCKED 字段清空**（见 VERIFY Step 5），推进到下一条。注意：此时 dev 已在上方 `reset --hard` 时恢复，无需再次 reset
- 否则：`current_phase = "MERGE_FIX"`

保存 loop-state.json。

---

## Phase 4.5: MERGE_FIX（合入冲突修复会话）

当功能合入 dev 后 CI 失败时，回到功能分支修复。

### 启动 Prompt

```
阅读 loop/autonomous-dev-loop.md 的 Phase 4.5: MERGE_FIX 部分。
阅读 loop/loop-state.json 获取当前状态。
阅读 CI 失败日志（路径见 loop-state.json 的 fix_list_path）。

你是集成修复开发者。CI 在 dev 合入后失败，你需要在功能分支上修复兼容性问题。
全程自主工作，不要问我问题。
```

### 工作流程

1. **执行状态校验**（见"通用步骤：状态校验"）
2. 读取 CI 失败日志，理解失败原因
3. 切到功能分支并同步 dev：
   ```bash
   git checkout feat/{slug}
   npm install                    # 确保依赖与分支一致

   # 同步 dev 最新代码到功能分支（确保包含其他已合入功能的变更）
   # 注意：此时 dev 已被 reset 到合入前状态，不包含当前 feat 的代码，
   # 但包含所有前序功能。这里的 merge 是把前序功能的最新状态同步到 feat，
   # 以便在 feat 上修复兼容性问题。
   git merge {dev_branch}
   npm install                    # merge 后再次同步依赖
   ```
   如果有**合并冲突**：优先保留 dev 上已有功能的逻辑，在此基础上融入当前功能的修复。`git add` 冲突文件后 `git commit`，写清楚冲突解决方式。
4. 在功能分支上修复问题（通常是与 dev 上已有功能的兼容性问题）
5. 确认功能分支本身 CI 通过：
   ```bash
   npm run lint && npm test && npm run build
   npx tsc --noEmit -p frontend           # vite build 不做类型检查
   ```
6. 更新并保存 loop-state.json：`{ "current_phase": "MERGE" }`（重新尝试合入 dev）
7. commit + push

---

## Phase 5: FINALIZE（全部完成后 — 逐功能创建 PR）

当 queue 中没有更多可执行项时（所有 `pending` 项的 `depends_on` 均不满足，或全部已 `done`/`blocked`），进入最终阶段。剩余 `pending` 但依赖被 BLOCKED 的项应记入 `blocked`（原因标注"依赖 #N 被阻塞"）。

### 启动 Prompt

```
阅读 loop/autonomous-dev-loop.md 的 Phase 5: FINALIZE 部分。
阅读 loop/loop-state.json 获取完成情况。

你是发布经理。为每个已完成功能创建独立的 feat→master PR（仅创建，不合并）。
每个功能分支先 rebase origin/master，通过本地 CI 后再创建 PR。
有依赖关系的功能标记为 deferred，等待前序 PR 合并后处理。
⚠️ 禁止执行 gh pr merge 或任何将代码合入 master 的操作。
全程自主工作，不要问我问题。
```

### 工作流程

1. **执行状态校验**（见"通用步骤：状态校验"）

2. **按完成顺序逐个 rebase 功能分支：**
   ```bash
   git fetch origin master
   ```

   按 `completed` 列表的完成顺序处理。**关键：** 功能之间可能有依赖关系，必须区分两类功能分支：

   **a) 无依赖（或仅依赖已处理的独立功能）：** 直接 rebase master
   ```bash
   git checkout feat/{slug}
   git rebase origin/master
   ```

   **b) 有依赖（depends_on 中有本轮其他功能）：** 标记为「待前序 PR 合并后处理」
   - 这类功能的 PR **暂不创建**，因为 rebase master 后会缺少前序功能代码导致 CI 失败
   - 在 loop-state.json 中记录 `"deferred_prs": [{ "id": N, "slug": "...", "waiting_for": [前序 PR 编号] }]`
   - 在最终输出中提示人工：合并前序 PR 后，对这些功能分支执行 `git rebase origin/master` 再创建 PR

   对每个 rebase 成功的功能分支执行 CI 验证：
   ```bash
   npm install
   npm run lint && npm test && npm run build
   npx tsc --noEmit -p frontend
   ```
   > rebase 后的功能分支与 dev 的历史已分叉（commit SHA 不同），这是预期行为。rebase 后的功能分支仅用于 PR 到 master，不应再 merge 回 dev。

   如果 rebase 有冲突，解决后继续。如果 CI 失败，在功能分支上修复（最多 3 次尝试）。
   3 次后仍失败 → 在 loop-state.json 的 `blocked` 中记录该功能及原因，跳过继续处理下一个。

3. **按完成顺序逐个创建 PR：**

   遍历 `completed` 列表（按完成顺序），为每个功能创建独立 PR：

   ```bash
   # 确保功能分支已 push 到远程（rebase 后需要 force push）
   git push origin feat/{slug} --force-with-lease

   gh pr create \
     --base master \
     --head feat/{slug} \
     --title "feat: {功能名称}" \
     --body "$(cat <<'EOF'
   ## Summary

   {功能描述，1-3 句话}

   - Spec: {spec_path}
   - Backlog: #{id}

   ## Test plan
   - [x] `npm run lint` 通过
   - [x] `npm test` 全部通过
   - [x] `npm run build` 构建成功
   - [ ] 人工验收

   🤖 Generated with [Claude Code](https://claude.com/claude-code)
   EOF
   )"
   ```

   将每个 PR 编号记录到 loop-state.json 的 `pr_numbers` 数组中。

4. **如果有 blocked 或 deferred 功能**，使用 `gh issue create` 创建 GitHub Issue，列出：
   - blocked 功能及原因（CI 修复失败、rebase 冲突等）
   - deferred 功能及其等待的前序 PR 编号
   将 Issue 编号记录到 loop-state.json 的 `blocked_issue` 字段。

5. **到此为止。** 不要合并任何 PR，不要操作 master 分支。PR 创建后等待人工处理。

   > ℹ️ 根据项目规范（CLAUDE.md），master 只接受 Squash Merge。每个功能 PR 被 squash merge 后，master 上会得到一个独立的 commit，历史清晰，支持按功能回滚。
   >
   > ⚠️ **人工合并操作指南：**
   > 1. 按 completed 列表的顺序逐个 squash merge 无依赖的 PR
   > 2. 每合并一个 PR 后，检查 deferred 列表中是否有功能的前序 PR 都已合并
   > 3. 对满足条件的 deferred 功能：`git checkout feat/{slug} && git rebase origin/master && git push --force-with-lease`，然后创建 PR
   > 4. 所有 PR 合并完成后，手动删除 dev 分支：`git push origin --delete dev/backlog-batch-{date}`

6. 更新 loop-state.json：`{ "current_phase": "CI_FIX" }`，同时记录 PR 编号数组到 `pr_numbers` 字段

## Phase 5.5: CI_FIX（PR CI 修复）

PR 创建后，GitHub CI 会自动运行。此阶段负责检查 CI 结果并修复失败项，直到 CI 全部通过。

### 启动 Prompt

```
阅读 loop/autonomous-dev-loop.md 的 Phase 5.5: CI_FIX 部分。
阅读 loop/loop-state.json 获取 PR 编号列表（pr_numbers 数组）。

你是 CI 修复工程师。多个功能 PR 已创建，你需要检查每个 PR 的 CI 并修复失败项。

工作流程：
1. 遍历 pr_numbers，对每个 PR 运行 `gh pr checks {pr_num} --watch` 等待 CI 完成并查看状态
2. 如果所有 PR 的 CI 都通过，更新 loop-state.json 的 current_phase 为 "AWAITING_HUMAN_REVIEW"，结束
3. 如果某个 PR 的 CI 失败，切到对应功能分支修复
4. 修复后提交并 push 到功能分支
5. 每个 PR 最多 3 轮修复。3 轮后仍失败则在 blocked 中记录原因

⚠️ 禁止执行 gh pr merge 或任何将代码合入 master 的操作。
全程自主工作，不要问我问题。
```

### 工作流程

1. **执行状态校验**（见"通用步骤：状态校验"）
2. 遍历 `pr_numbers` 数组中的每个 PR，等待 CI 完成并检查状态：
   ```bash
   gh pr checks {pr_num} --watch   # 等待 CI 完成（pending → pass/fail）
   ```
   > `--watch` 会阻塞直到所有 checks 完成。如果 CI 处于 pending 状态，不要提前判断为失败。
3. **如果所有 PR 的 CI 都通过** → 更新 `current_phase` 为 `"AWAITING_HUMAN_REVIEW"`，保存 loop-state.json，结束
4. **如果某个 PR 的 CI 失败：**
   ```bash
   gh pr checks {pr_num}   # 查看具体哪个 check 失败
   ```
5. 切到对应的功能分支修复：
   ```bash
   git checkout feat/{slug}
   npm install
   ```
6. 分析失败原因，修复（lint / test / build / typecheck / UI pattern check 等）
7. 执行本地门禁确认修复有效：
   ```bash
   npm run lint && npm test && npm run build
   npx tsc --noEmit -p frontend
   ```
8. 提交并 push（`git push origin feat/{slug}`），等待 CI 重新运行
9. **每个 PR 最多 3 轮修复。** 3 轮后仍失败 → 在 `blocked` 中记录该功能的 CI 失败原因
10. 所有 PR 处理完后，更新 `current_phase` 为 `"AWAITING_HUMAN_REVIEW"`，保存 loop-state.json

### AWAITING_HUMAN_REVIEW 期间的新任务处理

当 loop 处于 `AWAITING_HUMAN_REVIEW` 状态时，编排脚本不停止，而是持续轮询 `inbox/` 目录：

1. 编排脚本每 60 秒检查 `inbox/` 是否有新文件
2. 如果发现新任务 → 执行 `process_inbox()` 入队到 `loop-state.json` 的 queue
3. 在**当前 dev 分支**上继续处理新任务（复用现有 dev 分支，不创建新 batch）
4. 更新 `current_phase` 为新任务对应的起始 phase（根据复杂度评估结果）
5. 新任务完成后，在下次 FINALIZE 时为其创建新的独立 PR
6. 如果人工已合并所有 PR 且 inbox 有新任务，编排脚本会回到 INIT 创建新 dev 分支

---

## 任务队列与依赖

| 序号 | 任务 | 依赖 | 复杂度 | 路径 |
|------|------|------|--------|------|
| 1 | 去掉默认品牌预设 | 无 | 低 | FAST_TRACK |
| 2 | 去掉城市选项 | 无 | 低 | FAST_TRACK |
| 3 | 空间品牌配置扩展 | 无 | 中 | DESIGN_IMPLEMENT |
| 4 | 品牌关键词管理 | #3 | 中 | DESIGN_IMPLEMENT |
| 5 | POI 展示模式切换 | 无 | 中 | DESIGN_IMPLEMENT |
| 6 | 空间邀请能力优化 | 无 | 低 | FAST_TRACK |
| 7 | 空间算法优化 | #3 | 高 | DESIGN → IMPLEMENT |
| 8 | POI 缓存管理后台 | 无 | 中 | DESIGN_IMPLEMENT |
| 9 | 非餐饮类 POI 接入 | 无 | 中 | DESIGN_IMPLEMENT |
| 10 | Onboarding 流程优化 | #1,#2,#3,#4,#5,#6 | 中 | DESIGN_IMPLEMENT |

> 注：`复杂度` 列为预估值，实际由首个接手会话按"复杂度自动评估标准"动态评估后可能调整。

**依赖规则：**
- `depends_on` 在 JSON 中必须为**整数数组**（如 `[1, 2, 3, 4, 5, 6]`），禁止范围简写（如 `"#1-#6"`）
- 推进到下一条时，如果下一条有 `depends_on`，检查依赖项是否全部在 `completed` 中。不满足则跳过，取下一个可执行的

---

## 异常处理

| 场景 | 处理 |
|------|------|
| **loop-state.json 解析失败** | FATAL，停止执行，输出诊断信息等待人工 |
| **状态校验发现分支不存在** | 尝试从 remote fetch；如果 remote 也没有则 FATAL |
| 自动化门禁失败修不好 | 3 次 VERIFY 循环后标记 BLOCKED，记录原因，跳下一条 |
| dev 合入后 CI 失败修不好 | 3 次 MERGE_FIX 循环后标记 BLOCKED（注：每次 CI 失败时 dev 已 reset 到备份 tag，BLOCKED 时无需再次 reset） |
| plan 与实际代码冲突 | IMPLEMENT 阶段直接修正 plan，在顶部记录偏差原因 |
| 依赖项被 BLOCKED | 跳过依赖它的后续项，取下一个无阻塞依赖的 |
| migration 版本冲突 | 动态读取当前 `MIGRATION_VERSION`，在其基础上 +1 |
| backlog 条目含"待定" | DESIGN 阶段做出决策，在 spec 中记录决策理由 |
| merge dev 时有冲突 | 优先保留 dev 已有逻辑，在此基础上融入新功能 |
| 上下文接近耗尽（**所有 Phase 通用**） | 立即保存当前进度到 loop-state.json + commit 已完成的工作 + push，结束会话。具体保存内容：DESIGN 保存已完成的 spec 章节；DESIGN_IMPLEMENT 保存 spec/plan + 切换到 IMPLEMENT 分离模式（见"上下文熔断"）；IMPLEMENT 保存 `implement_progress` + 在 plan 写 `[CONTEXT_BREAK]`；VERIFY 保存已审查的文件列表和中间结论到 review 文件；FIX 保存已修复的 issue 列表 |
| npm install 失败 | 检查 lock file 冲突 → 删 node_modules 重试 → 仍失败则停止等待人工 |
| 功能分支需要回溯 | 从 FINALIZE 前保留的功能分支检出，功能分支在 FINALIZE 统一清理 |
| **FINALIZE rebase master 冲突** | 在功能分支上解决冲突后 `git rebase --continue`；3 次失败后标记 BLOCKED |
| **FINALIZE 有依赖的功能 CI 失败** | 预期行为（缺少前序功能代码），标记为 deferred 而非 blocked，不计入修复次数 |
| **人工合并 PR 后后续 PR 冲突** | 对冲突的 PR 重新 `git rebase origin/master && git push --force-with-lease` |
| **PR 被关闭（非合并）** | 对应功能分支不会被 GitHub 自动删除，需人工执行 `git push origin --delete feat/{slug}` |
| **[STUCK] 有依赖链** | 级联标注所有依赖此 Task 的后续 Task 为 `[STUCK]` |
| **3 个以上功能连续 BLOCKED** | 可能有系统性问题，等待人工排查 |

---

## 运维：如何启动和恢复

### 前置条件

- GitHub master 分支保护已开启
- 本地 PostgreSQL 运行中
- `npm install` 在根目录执行过
- `claude` CLI 已安装且可用

---

### 模式 A：自动模式（编排脚本，真正无人值守）

通过外部 shell 脚本循环调用 `claude` CLI，自动完成 phase 之间的会话衔接，无需人工粘贴 prompt。

**编排脚本：`loop/dev-loop.sh`**（完整代码见该文件，以下仅说明核心机制）

> ⚠️ 编排脚本的完整实现在 `loop/dev-loop.sh`，不在本文档中维护副本，避免 drift。

```bash
# 快速查看脚本结构
head -50 loop/dev-loop.sh

# 查看完整实现请直接阅读 loop/dev-loop.sh
```

#### 核心机制

| 机制 | 说明 |
|------|------|
| Phase 驱动 | 读取 loop-state.json 的 current_phase，选择对应 prompt 调用 `claude -p` |
| inbox 处理 | 每轮循环开始前扫描 `inbox/*.md`，解析 frontmatter 自动入队 |
| backlog 刷新 | 每轮会话结束后从 loop-state.json 全量生成 `backlog.md` |
| Phase 变化检测 | 会话结束后 phase 未变化计入连续失败 |
| 连续失败上限 | 默认 3 次后停止整个循环，避免无限空转 |
| AWAITING 时轮询 | 不停止循环，每 60 秒轮询 inbox 等待新任务 |
| 会话日志 | 每个会话的完整输出保存到 `loop/logs/` |
| 断点续传提示 | 连续失败时在下一轮 prompt 前追加检查已有产出物的提示 |

#### 支持的 Phase（build_prompt 映射）

`INIT` · `FAST_TRACK` · `DESIGN_IMPLEMENT` · `DESIGN` · `IMPLEMENT` · `VERIFY` · `FIX` · `MERGE` · `MERGE_FIX` · `FINALIZE` · `CI_FIX`

> ℹ️ 如需新增 Phase，在 `dev-loop.sh` 的 `build_prompt()` case 语句中添加对应分支。

**使用方式：**

```bash
# 首次启动（确保前置条件满足后）
chmod +x loop/dev-loop.sh
./loop/dev-loop.sh

# 后台运行（真正无人值守）
nohup ./loop/dev-loop.sh > dev-loop-output.log 2>&1 &

# 查看实时进度
tail -f dev-loop-output.log
# 或查看 loop-state.json
cat loop/loop-state.json | python3 -m json.tool
```

> ℹ️ 编排脚本不参与任何 Git 操作或代码修改，它只负责读取状态、选择 prompt、调用 claude、检测异常。所有实际工作由 claude 会话内部完成。

---

### 模式 B：手动模式（逐个会话粘贴 prompt）

适用于调试单个 phase、或不想运行完整循环的场景。

**首次启动：**

1. 确认前置条件满足
2. 打开 Claude Code
3. 粘贴以下初始化命令（执行 Phase 0）：

```
阅读 loop/autonomous-dev-loop.md 的 Phase 0: INIT 部分。
执行初始化：从 master 创建 dev 集成分支，更新 loop-state.json。
然后检查第一个任务的复杂度：
- 如果是"低"，直接进入 FAST_TRACK
- 否则进入 Phase 1: DESIGN
全程自主工作，不要问我问题。
```

**会话结束后恢复：**

每个阶段结束后（或会话因上下文耗尽而中断），打开新的 Claude Code 会话，粘贴**当前 phase 对应的启动 Prompt**（见各 Phase 章节）。新会话读取 loop-state.json → 执行状态校验 → 确认无误后继续。

**关键：** 如果是 IMPLEMENT 阶段中断恢复，新会话会读取 `implement_progress` 确定从哪个 Task 继续，不会重做已完成的工作。

---

### 查看进度

随时查看 `loop/loop-state.json` 了解：
- 当前在哪个功能、哪个阶段、阶段内哪个 Task
- 已完成和被阻塞的功能列表
- 审查/CI 修复尝试次数

### 人工干预点

无论使用哪种模式，以下情况**必须**人工介入：
- **AWAITING_HUMAN_REVIEW** — AI 的工作到此结束，每个功能的独立 PR 已创建，人工按顺序逐个 squash merge 到 master
- 某功能被标记 BLOCKED — 检查原因，可能需要产品决策
- 3 个以上功能连续 BLOCKED — 可能有系统性问题
- 状态校验报 FATAL — 状态文件损坏或 Git 状态异常
- master 分支保护未开启 — INIT 阶段会检测并提示
- 自动模式连续失败停止 — 查看会话日志排查原因

> ⚠️ **再次强调：AI 全程不会将任何代码合入 master。master 的变更只能由人工操作。**
