# QA4: autonomous-dev-loop.md Bug Report — 跨阶段一致性 / 边界场景 / 安全性

> 审查对象: `loop/autonomous-dev-loop.md`
> 审查维度: Prompt 准确性、跨阶段门禁一致性、边界场景、数据完整性、安全模型
> 审查日期: 2026-03-23
> 审查角色: 资深 QA（第二轮，补充 QA3 未覆盖的维度）

---

## Critical

### Bug 9: FAST_TRACK 无断点续传 — 中断后必重做且必撞分支冲突

**位置:** FAST_TRACK 工作流程 Step 4 / Step 8 (L276-300)

**问题链:**

1. Step 4 创建功能分支 `git checkout -b feat/{slug}`
2. Step 5-7 实现 + 门禁 + commit push
3. **Step 8 才更新 loop-state.json** 设置 `branch: "feat/{slug}"` 和 `current_phase: "VERIFY"`

如果会话在 Step 4-7 之间耗尽上下文或崩溃：

- 功能分支 `feat/{slug}` **已存在**（可能已 push 到远程），可能包含部分 commit
- 但 loop-state.json 仍为上一轮结束的状态：`current_phase: "FAST_TRACK"`、`branch: null`
- 新 FAST_TRACK 会话读取状态 → branch 为 null → 执行 Step 4 → `git checkout -b feat/{slug}` **报错分支已存在**

**对比:** IMPLEMENT 阶段有完善的断点续传（L418-427：检查 `last_commit_sha`、读 plan 的 `[x]` 标记、从 `current_task` 继续），且 DESIGN 阶段已提前将 branch 写入 state（L382）。FAST_TRACK 两者都没有。

**后果:** 低复杂度任务反而比高复杂度任务更脆弱。如果 FAST_TRACK 任务接近上下文极限（比如"低复杂度"实际涉及多文件），已完成的工作无法恢复，且新会话启动直接报错。

**修复:** FAST_TRACK Step 4 创建分支后，立即更新 loop-state.json 设置 `branch`。增加续传检查：如果 branch 已存在且有 commit，checkout 复用而非 `-b` 创建。

---

### Bug 10: FIX 阶段门禁不完整 — 修复引入的 build/type 错误需多浪费一个会话才能发现

**位置:** Phase 3.5 FIX Step 5 (L650)

**现象:**

```bash
# FIX 阶段的门禁
npm run lint && npm test
```

**缺少:** `npm run build` 和 `npx tsc --noEmit -p frontend`

**对比其他阶段的门禁:**

| 阶段 | lint | test | build | tsc frontend |
|------|------|------|-------|-------------|
| FAST_TRACK (L286-287) | ✅ | ✅ | ✅ | ✅ |
| IMPLEMENT per-task (L446-448) | ✅ | ✅ | ❌ | ✅ |
| IMPLEMENT final (L471-472) | ✅ | ✅ | ✅ | ✅ |
| VERIFY (L537-541) | ✅ | ✅ | ✅ | ✅ |
| **FIX (L650)** | ✅ | ✅ | **❌** | **❌** |
| MERGE (L699-703) | ✅ | ✅ | ✅ | ❌* |
| MERGE_FIX (L799-800) | ✅ | ✅ | ✅ | ❌* |
| FINALIZE (L839) | ✅ | ✅ | ✅ | ❌* |

> *标注 ❌ 的是 QA3 Bug 2 已指出的遗漏

**后果:** FIX 开发者修复 VERIFY 指出的 Critical 问题时，可能引入编译错误或类型错误（例如修改了接口类型但没更新所有引用）。这些错误不会被 FIX 的门禁拦住，commit + push 后进入 VERIFY → VERIFY 的门禁立即失败 → 新一轮 FIX。白白浪费一次 VERIFY 会话。

**修复:** FIX Step 5 改为完整门禁：`npm run lint && npm test && npm run build && npx tsc --noEmit -p frontend`

---

### Bug 11: INIT 对 master 分支保护只警告不阻断 — 安全红线的系统级保障失效

**位置:** Phase 0 INIT (L225-227)

**现象:**

```bash
gh api repos/{owner}/{repo}/branches/master/protection 2>/dev/null \
  && echo "✅ master 分支保护已开启" \
  || echo "⚠️ 警告：master 分支保护未开启，请人工配置后再继续"
```

打印警告后**继续执行后续步骤**。没有 `exit 1`，没有阻断。

**与硬性红线的矛盾 (L19-21):**

> "系统级保障（Prompt 之外的硬约束）：GitHub 侧 master 分支保护必须开启"

整个安全模型依赖两层防护：Prompt 约束 + 分支保护。如果分支保护没开启，防护降级为"仅靠 Prompt 约束" — 而 Prompt 约束在复杂上下文中可能被 Agent 误解或遗忘。

**后果:** 在分支保护未开启的仓库上运行循环，如果 Agent 因理解偏差执行了 `git push origin master`，没有任何系统级机制阻止。

**修复:** 将警告改为硬阻断。检查失败时写入 loop-state.json `{ "current_phase": "FATAL", "reason": "master 分支保护未开启" }` 并终止。

---

## Major

### Bug 12: MERGE_FIX 的 `git merge {dev_branch}` 无冲突解决指引

**位置:** Phase 4.5 MERGE_FIX Step 3 (L794)

**现象:**

```bash
git merge {dev_branch}    # 无冲突处理说明
```

**对比:** MERGE 阶段（L688-691）有明确的冲突解决策略："优先保留 dev 上已有功能的逻辑，在此基础上融入新功能"。FINALIZE（L834）虽然简略但至少说了"如果 merge 有冲突，解决后继续"。MERGE_FIX 完全没有。

**触发条件:** CI 失败后 dev 被 reset，此时 CI 失败日志被 commit 到 dev（L762），feat 分支 merge dev 时可能在 `loop/reviews/` 目录产生冲突（如果 feat 分支的 VERIFY 也在同一目录写了 review 文件）。

**修复:** 补充冲突解决策略。建议与 MERGE 保持一致的原则。

---

### Bug 13: 双源头进度跟踪 — implement_progress vs plan [x] 标记可能不一致

**位置:** IMPLEMENT Step 3 (L425-426) + Step 6 (L442) + Step 8 (L457-468)

**问题:** 任务进度同时记录在两个地方：

1. `loop-state.json` 的 `implement_progress.current_task`
2. plan 文件中的 `- [x]` checkbox 标记

断点续传时 (L425-426)：

> "读取 plan 文件中的 `[x]` 标记，确认已完成的 Task"
> "从 `implement_progress.current_task` 继续"

**如果两者不一致怎么办？** 例如：

- Session 写完代码并在 plan 勾了 `[x]`，但 commit 前崩溃 → plan 有 `[x]` 但 state 的 `last_commit_sha` 指向更早的 commit
- 或反过来：commit 成功、state 更新了，但 plan 的 `[x]` 还没勾

文档没有定义冲突时以哪个为准。新会话可能重做已完成的工作，或跳过实际未完成的工作。

**修复:** 明确以 `implement_progress` 为主源（因为它与 commit SHA 绑定可验证），plan 的 `[x]` 作为辅助参考。当两者冲突时，以 `git log` 中能找到 `last_commit_sha` 且对应 task 为准。

---

### Bug 14: 同一天启动两轮循环，dev 分支名冲突

**位置:** INIT (L230) + 分支策略

**现象:** dev 分支名为 `dev/backlog-batch-{date}`，例如 `dev/backlog-batch-2026-03-23`。

**触发场景:**
- 第一轮循环完成 → FINALIZE 创建 PR → 人工 merge 到 master
- 同一天启动第二轮循环 → INIT 试图创建 `dev/backlog-batch-2026-03-23` → 分支已存在

即使 QA3 Bug 3 的幂等性修复后（检测已存在则 checkout），也会错误复用第一轮的 dev 分支（可能包含已合并的旧功能代码 + 旧 spec/plan 文件）。

**修复:** 分支名加序号后缀：`dev/backlog-batch-{date}-{N}`，或在 FINALIZE 成功后删除 dev 分支。

---

## Medium

### Bug 15: FIX Prompt 未引导读取 spec 和 plan

**位置:** Phase 3.5 FIX 启动 Prompt (L632-640)

**现象:** Prompt 只要求读取三个文件：

1. autonomous-dev-loop.md 的 FIX 部分
2. loop-state.json
3. 修复清单文件

但 FIX 工作流 Step 4 (L648) 写道：

> "spec 层面问题：先修正 spec 文件，再修正实现代码"

**问题:** 修复 spec 需要理解 spec 的完整上下文，但 Prompt 没有引导 Agent 读取 `spec_path` 指向的 spec 文件，也没提到读取 `plan_path`。Agent 可能只从修复清单中的片段理解问题，做出错误修正。

**修复:** Prompt 中增加 "如果 loop-state.json 的 spec_path 非 null，阅读对应的 spec 文件；如果 plan_path 非 null，阅读对应的 plan 文件。"

---

### Bug 16: 断点续传校验 commit SHA 不充分

**位置:** IMPLEMENT Step 3 (L419-424)

**现象:**

```bash
git checkout feat/{slug}
git log --oneline | head -5    # 只看最近 5 条
```

只检查分支是否存在并显示最近 log，**没有验证 `last_commit_sha` 是否在分支历史中**。

**触发场景:** 如果 feat 分支被意外 force-push 或 reset（例如另一个会话错误操作），`last_commit_sha` 可能不在分支中，但校验不会发现。Agent 会从 `current_task` 继续，基于已被覆盖的代码工作。

**修复:** 增加 SHA 验证：

```bash
git branch --contains {last_commit_sha} | grep -q "feat/{slug}" \
  || { echo "WARN: last_commit_sha not in branch history"; }
```

---

### Bug 17: 状态校验缺少工作目录干净检查

**位置:** 通用状态校验 (L179-205)

**问题:** 校验检查了 JSON 合法性、分支存在性、文件存在性，但没有检查 `git status` 是否干净。

**触发场景:** 上一个会话崩溃，留下未 commit 的修改。新会话启动，`git checkout {dev_branch}` 可能因 dirty working tree 失败，或自动 merge 产生意外结果。

**修复:** 状态校验增加：

```bash
git status --porcelain | head -1 && echo "WARN: working tree not clean, stashing changes" && git stash
```

---

### Bug 18: depends_on 引用不存在的 id 无校验

**位置:** queue 定义 (L159-161) + 推进逻辑 (L727)

**问题:** `depends_on: [99]` 中的 id 99 不存在于 queue 中，永远不会出现在 `completed` 列表。该任务会被永久跳过但不会被标记 BLOCKED（因为 id 99 也不在 `blocked` 中）。

**后果:** 任务变成幽灵 pending — 永远不执行、永远不 BLOCKED、永远在 queue 中。FINALIZE 的判定条件"没有更多可执行项"满足后退出，但该任务既不在 completed 也不在 blocked。

**修复:** INIT 阶段校验 queue 完整性：所有 `depends_on` 中的 id 必须存在于 queue 中。

---

## 汇总

| 级别 | 数量 | 最高优先 |
|------|------|----------|
| Critical | 3 | Bug 9 — FAST_TRACK 中断必撞分支冲突，0% 容错 |
| Major | 3 | Bug 13 — 双源头不一致可能导致重做或跳过工作 |
| Medium | 4 | Bug 15 — Prompt 遗漏导致 FIX 上下文不足 |

### 修复优先级建议

1. **Bug 9（FAST_TRACK 断点）** — 创建分支后立即更新 state，最小改动最大收益
2. **Bug 11（INIT 安全阻断）** — 加一个 `exit 1`，堵住安全模型的系统级漏洞
3. **Bug 10（FIX 门禁）** — 补齐 build + typecheck，避免浪费 VERIFY 会话
4. **Bug 14（分支名冲突）** — 加序号后缀，防止同日多轮冲突
5. 其余按迭代节奏处理

---

## 与 QA3 的关系

QA3 聚焦**状态机逻辑和门禁遗漏**（Bug 1-8），本篇聚焦**跨阶段一致性、边界场景、安全模型**（Bug 9-18）。

两轮合计：

| 级别 | QA3 | QA4 | 总计 |
|------|-----|-----|------|
| Critical | 3 (Bug 1-3) | 3 (Bug 9-11) | **6** |
| Major | 3 (Bug 4-6) | 3 (Bug 12-14) | **6** |
| Medium | 2 (Bug 7-8) | 4 (Bug 15-18) | **6** |
| Minor | 2 | — | **2** |
| 建议 | 1 | — | **1** |
