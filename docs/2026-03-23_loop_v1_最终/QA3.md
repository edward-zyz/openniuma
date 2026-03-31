# QA3: autonomous-dev-loop.md Bug Report

> 审查对象: `loop/autonomous-dev-loop.md`
> 审查日期: 2026-03-23
> 审查角色: 资深 QA（逐条对照源文件复核）

---

## Critical

### Bug 1: BLOCKED 状态转移不完整 — 新会话必 FATAL

**位置:** L609（VERIFY BLOCKED）、L759（MERGE CI 失败 BLOCKED）

**问题:** 两处都说"标记 BLOCKED，执行 BLOCKED 字段清空，推进到下一条"。但 BLOCKED 字段清空（L613-619）**只清空了关联字段**：

```json
{
  "branch": null, "spec_path": null, "plan_path": null, "fix_list_path": null,
  "verify_attempts": 0, "merge_fix_attempts": 0,
  "implement_progress": { ... }
}
```

**缺失以下操作（对比 MERGE 成功时 L725-730 的完整逻辑）：**

| 操作 | MERGE 成功 (L725-730) | BLOCKED (L609/L759) |
|------|----------------------|---------------------|
| 当前项移入 `completed` / `blocked` 数组 | ✅ 移入 `completed` | ❌ 未移入 `blocked` |
| queue 中更新 `status` | ✅ `"done"` | ❌ 未设 `"blocked"` |
| 更新 `current_item_id` | ✅ 推进到下一个 | ❌ 未更新 |
| 设置 `current_phase` | ✅ `"DESIGN"` / `"FAST_TRACK"` | ❌ 未设置 |
| BLOCKED 传播 | ✅ 遍历 depends_on | ❌ 未执行 |

**后果:** 字段清空后，`current_phase` 仍为 `"FIX"` 或 `"MERGE_FIX"`，`current_item_id` 仍指向已废弃任务。新会话启动时状态校验发现 phase 要求的 branch/fix_list_path 均为 null → FATAL，流程卡死。

**修复:** 在 BLOCKED 字段清空后，补充与 MERGE 成功等价的完整状态推进逻辑（移入 blocked、更新 queue status、传播依赖、推进 current_item_id、设置 current_phase）。

---

### Bug 2: MERGE 和 FINALIZE 缺少前端类型检查

**位置:**
- Phase 4 MERGE Step 2 (L699-703)
- Phase 5 FINALIZE Step 3 (L837-839)

**问题:** 两处的 CI 门禁都只有：

```bash
npm run lint && npm test && npm run build
```

缺少 `npx tsc --noEmit -p frontend`。

**对比:** IMPLEMENT（L472）、VERIFY（L541）、FAST_TRACK（L287）都包含此检查。文档自身在 L544 也明确说明："前端的 vite build 不做类型检查，因此需要单独执行 `npx tsc --noEmit -p frontend`。"

**后果:** MERGE 是多功能集成的关键节点，不同功能的类型定义最容易在此冲突（如两个功能给同一组件 props 加了不同字段），恰恰是最需要类型检查的地方。FINALIZE 是发 PR 前最后一道关卡，同理。类型错误可以一路潜入到 master PR。

**修复:** 两处均补充 `npx tsc --noEmit -p frontend`。

---

### Bug 3: INIT 缺少幂等性和完整初始化

**位置:** Phase 0 INIT (L219-246)

**问题 A — 无幂等性:**

```bash
git checkout -b dev/backlog-batch-2026-03-23   # 分支已存在时直接报错
```

中断重试或日期重复时，`git checkout -b` 失败，没有检测和兜底逻辑。

**问题 B — 初始化不完整:**

INIT 只设了 `dev_branch` 一个字段（L236），但后续所有 Phase 都依赖完整的 loop-state 结构：
- MERGE 的 L726 遍历 `completed` 数组 → undefined
- 推进逻辑读取 `queue` → undefined
- VERIFY 读取 `verify_attempts` → undefined

**修复:**
- A: 先 `git rev-parse --verify` 检测分支存在性，已存在则 checkout 复用
- B: 输出完整的 loop-state.json 初始结构，包含 `queue`（从 backlog 解析）、`completed: []`、`blocked: []`、`current_item_id`、`current_phase` 等全部字段

---

## Major

### Bug 4: BLOCKED 依赖传播逻辑只在 MERGE 成功时执行

**位置:** L726（MERGE 成功的传播逻辑）vs L609/L759（BLOCKED 路径无传播）

**问题:** "传播 BLOCKED 状态"（遍历 queue 中 pending 项，标记依赖被阻塞任务的后续项）只写在 MERGE CI 通过的分支中。BLOCKED 路径没有这段逻辑。

**影响分析:** L727 的 `depends_on` 检查可以兜底——被阻塞任务的依赖项不会被选为 next item（因为 depends_on 不在 completed 中）。但这些任务在 queue 中仍显示 `status: "pending"`，导致：
- FINALIZE 汇总 PR 时无法准确报告哪些功能因依赖被阻塞
- Bug 10 的连续 BLOCKED 检测（如果实现的话）计数不准确
- 循环结束条件判断可能出错——有 pending 项但实际不可执行

**修复:** 将传播逻辑提取为通用步骤，在每次标记 BLOCKED 和每次 MERGE 成功后都执行。

---

### Bug 5: 状态校验遗漏 `fix_list_path` 检查

**位置:** 通用状态校验 (L180-204)

**问题:** 校验了 `spec_path`（L197）和 `plan_path`（L201）的文件存在性，但遗漏了 `fix_list_path`。

**触发场景:** FIX（L646）和 MERGE_FIX（L775）都依赖此文件。如果 review/CI-fail 文件被意外删除或路径设错，运行中才发现而非启动时。

**修复:** 状态校验中添加：当 phase ∈ {FIX, MERGE_FIX} 时，校验 `fix_list_path` 对应文件存在。

---

### Bug 6: "3+ 连续 BLOCKED" 检测只有声明无实现

**位置:** 异常处理表 L930

**问题:** 文档声称"3 个以上功能连续 BLOCKED → 等待人工排查"，但整个流程中没有任何计数器或检测逻辑。

**后果:** 系统性问题（如 CI 环境损坏、数据库连接失败）导致所有任务连续 BLOCKED 时，循环默默跳过全部任务直到 FINALIZE，产出一个几乎为空的 PR。

**修复:** 在 loop-state.json 中增加 `consecutive_blocked_count` 字段，每次 BLOCKED 时 +1，MERGE 成功时归零，达到阈值时 FATAL 停止。

---

## Minor

### Bug 7: DESIGN 在 dev 上直接 commit spec/plan

**位置:** Phase 1 DESIGN Step 9 (L384)

**问题:** "commit 并 push spec + plan 到 dev 分支"，而 L62 规则写"dev 分支上不直接写代码 — 只接受来自 feat/* 分支的 merge"。

**评估:** spec/plan 是设计产物而非实现代码，L62 的"代码"语义偏向实现层。但如果功能后来 BLOCKED，dev 上会残留无用的 spec/plan 文件，无法通过回退 merge 来清理。

**建议:** 可接受现状，但建议在 L62 规则中补充例外说明："设计文档（spec/plan）除外"，消除歧义。

---

### Bug 8: FINALIZE 分支清理用 `git branch -d` 可能遗留本地分支

**位置:** Phase 5 FINALIZE Step 4 (L852)

**问题:** `-d` 要求分支已完全合并到当前 HEAD。如果 feat 分支在 FIX 阶段有额外 commit 未被 `--no-ff` merge 完整包含，`-d` 会拒绝删除。

**实际影响有限:** `|| true` 兜底不会中断流程，远程分支删除独立执行不受影响。仅本地分支可能残留。

**建议:** 改用 `git branch -D` 强制删除（FINALIZE 阶段分支已无保留价值）。

---

## 建议（不影响正确性）

### 建议 1: review 文件会进入 master

**位置:** VERIFY Step 5 (L622)

review 文件 commit 到功能分支 → merge 到 dev → 最终 PR 到 master。`loop/reviews/` 下的审查记录会进入生产仓库。

如果不希望这些文件进入 master，可在 FINALIZE 前清理，或将 `loop/reviews/` 加入 `.gitignore`。

---

## 汇总

| 级别 | 数量 | 最高优先 |
|------|------|----------|
| Critical | 3 | Bug 1 — 任何 BLOCKED 场景都会卡死，0% 容错 |
| Major | 3 | Bug 2 — 每次 MERGE/FINALIZE 必定漏检 |
| Minor | 2 | 不阻塞流程，建议择机修复 |
| 建议 | 1 | 视项目偏好决定 |

### 修复优先级建议

1. **先修 Bug 1** — 补全 BLOCKED 状态转移逻辑（不修则 BLOCKED 功能必卡死）
2. **再修 Bug 2** — 两处补 `npx tsc --noEmit -p frontend`（一行代码的事）
3. **然后修 Bug 3** — 完善 INIT 初始化（首次启动就会遇到）
4. 其余按迭代节奏处理

### 修复优先级建议

1. **先修 Bug 1** — 补全 BLOCKED 状态转移逻辑（不修则 BLOCKED 功能必卡死）
2. **再修 Bug 2** — 两处补 `npx tsc --noEmit -p frontend`（一行代码的事）
3. **然后修 Bug 3** — 完善 INIT 初始化（首次启动就会遇到）
4. 其余按迭代节奏处理
