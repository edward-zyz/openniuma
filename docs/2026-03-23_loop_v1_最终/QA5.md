# QA5: autonomous-dev-loop.md Bug Report — 运行时语义 / 并发安全 / 恢复路径

> 审查对象: `loop/autonomous-dev-loop.md`
> 审查维度: 运行时语义歧义、并发/中断恢复、缺失的防御机制、Prompt 与 Agent 行为的 gap
> 审查日期: 2026-03-23
> 审查角色: 资深 QA（第五轮，聚焦 QA1-QA4 未覆盖的维度）

---

## Critical

### Bug 19: `git stash` 自动保存但永无恢复 — 静默丢失工作

**位置:** 通用状态校验 Step 6 (L207-210)

```bash
if [ -n "$(git status --porcelain)" ]; then
  echo "WARN: 工作目录不干净，自动 stash"
  git stash push -m "auto-stash before ${current_phase}"
fi
```

**与 QA4 Bug 17 的区别:** QA4 Bug 17 指出状态校验缺少脏目录检查。该 bug 已修复，加了上述 `git stash` 逻辑。但本 bug 指出的是：**stash 之后全文没有任何 `git stash pop` 或 `git stash apply` 的逻辑。**

**触发场景链:**
1. IMPLEMENT 会话在 Task 3 中途崩溃（上下文耗尽），工作区有未 commit 的代码修改
2. 新会话启动，执行状态校验 → 检测到脏文件 → `git stash` → 修改消失
3. 新会话从 `implement_progress.current_task = 3` 续传 → 重做 Task 3
4. stash 中的代码永远不会被 pop（没有任何 Phase 包含此操作）
5. 后续 `git stash` 会堆积多条，越来越难定位哪条有用

**后果:** 未 commit 的工作被静默丢失。如果 Agent 在 Task 3 完成了 80% 的代码但还没过门禁、没 commit，这些工作全部丢失。用户除非手动 `git stash list` 否则不会知道。

**修复方案:**
- **方案 A（推荐）:** stash 后在 loop-state.json 记录 `"stashed": true`，Phase 启动后在切分支/pull 完成后执行 `git stash pop`，如有冲突则 WARN 并保留 stash
- **方案 B:** 不 stash，改为 `git add -A && git commit -m "WIP: auto-save before ${current_phase}"` 形成可追溯的 WIP commit，续传时可 `git reset HEAD~1` 恢复

---

### Bug 20: MERGE → MERGE_FIX → MERGE 重试循环中，backup tag 生命周期断裂

**位置:** Phase 4 MERGE Step 1 (L714-716) vs CI 失败路径 (L773-775) vs Phase 4.5 MERGE_FIX Step 7 (L840)

**与 QA1 Bug 4 的区别:** QA1 Bug 4 指出 tag 重名问题（已通过 `git tag -f` 修复）。本 bug 指出的是**多轮重试中 tag 的语义不一致**。

**完整生命周期分析:**

| 步骤 | 操作 | backup tag 状态 |
|------|------|----------------|
| MERGE 第 1 次 | `git tag -f backup/pre-merge-{slug} HEAD` | ✅ 指向 merge 前的 dev HEAD |
| MERGE CI 失败 | `git reset --hard backup/...` → `git tag -d backup/...` | ❌ tag 已删除 |
| MERGE_FIX | 在 feat 分支修复 → push → `current_phase = "MERGE"` | ❌ tag 不存在 |
| MERGE 第 2 次 | `git tag -f backup/pre-merge-{slug} HEAD` | ✅ 指向新的 dev HEAD |
| MERGE 第 2 次 CI 失败 | `git reset --hard backup/...` → `git tag -d backup/...` | ❌ tag 已删除 |
| MERGE_FIX 第 2 次 | 修复 → `current_phase = "MERGE"` | ❌ tag 不存在 |
| MERGE 第 3 次 CI 失败 | → `merge_fix_attempts >= 3` → BLOCKED | 需要 reset |

**问题:** 第 3 次 CI 失败触发 BLOCKED 时（L794），文档说"reset dev 到备份 tag"，但此时 tag 在 CI 失败路径中已被删除（L774）。

**实际上 L773-775 的执行顺序是:**
```bash
git reset --hard "backup/pre-merge-{slug}"   # 先 reset（tag 此时还在）
git tag -d "backup/pre-merge-{slug}"          # 再删 tag
```
所以单次 reset 没问题。但 **BLOCKED 路径（L794）是在 `merge_fix_attempts += 1` 之后判断的**，此时已在 CI 失败路径中，reset 已经执行过了。也就是说 BLOCKED 时的"reset dev 到备份 tag"是**多余的** — dev 已经被 reset 过了，但文档仍然要求再次执行。

**后果:** BLOCKED 时尝试 `git reset --hard backup/pre-merge-{slug}` 但 tag 不存在 → 命令失败 → 如果没有错误处理会中断流程。

**修复:** 明确 BLOCKED 路径不需要再次 reset（已在 CI 失败路径中完成），或在 CI 失败路径中仅当 `merge_fix_attempts < 3` 时才删除 tag。

---

## Major

### Bug 21: `depends_on: "#1-#6"` 范围语法无解析规范

**位置:** 任务队列表 (L945) + queue JSON 定义 (L160)

**现象:** 任务 #10 的依赖写作 `#1-#6`（L945 的表格）。但 queue JSON 定义中 `depends_on` 是数组类型：

```json
{ "id": 10, "depends_on": [], "complexity": "中" }
```

**歧义:**
- 表格中 `#1-#6` 是人类可读的范围表示，JSON 中应展开为 `[1,2,3,4,5,6]`
- 但文档没有定义这个映射规则。INIT 阶段从 backlog 解析 queue 时，Agent 可能：
  - 正确展开为 `[1,2,3,4,5,6]` ✅
  - 字面量存储为 `["#1-#6"]` ❌ → 依赖检查永远不匹配
  - 只取首尾 `[1,6]` ❌ → 遗漏中间依赖

**后果:** 如果 Agent 误解范围语法，任务 #10 可能在依赖未全部完成时被执行，或永远不被执行（幽灵 pending，与 QA4 Bug 18 相同后果）。

**修复:** 在 queue JSON 示例中明确使用展开形式 `"depends_on": [1, 2, 3, 4, 5, 6]`，不使用范围简写。或在 INIT 阶段添加校验规则："depends_on 必须为整数数组"。

---

### Bug 22: 多个 pending 任务同时满足依赖时，选择策略未定义

**位置:** MERGE CI 通过后的推进逻辑 (L762)

```
current_item_id 推进到下一个 queue 中 status = "pending" 且 depends_on 全部在 completed 中的项
```

**问题:** 如果有多个任务同时满足条件（例如 #5、#6、#8 都是无依赖的 pending 任务），"下一个"是什么？

- 按 `id` 升序？（最可能的隐含假设）
- 按 queue 数组顺序？（id 和顺序可能不一致）
- 按 complexity 优先？（先做低复杂度 FAST_TRACK？）

**场景:** 假设 #3 完成后，#4（依赖 #3）和 #5（无依赖）同时可执行。如果选 #5，那 #4 延后；如果选 #4，则优先处理依赖链。不同选择影响 dev 分支的代码基线和 merge 冲突概率。

**后果:** Agent 的选择不可预测，不同会话可能做出不同选择，导致 loop-state 中的执行顺序与人类预期不符。

**修复:** 明确选择策略，例如"按 queue 数组顺序，取第一个满足条件的"。或增加 `priority` 字段。

---

### Bug 23: IMPLEMENT 的 `[STUCK]` 计数器未持久化 — 中断后重试次数归零

**位置:** Phase 2 IMPLEMENT 卡住处理 (L517)

> "同一步骤失败 5 次：标注 [STUCK]"

**问题:** "5 次失败"的计数在哪里？

- `implement_progress` 只记录 `current_chunk`、`current_task`、`last_committed_task`、`last_commit_sha`
- 没有 `step_failure_count` 或类似字段
- 如果会话在第 4 次失败后因上下文耗尽中断，新会话无法知道已经失败了 4 次
- 新会话从 `current_task` 续传，重新尝试同一步骤，又从 0 开始计数
- 理论上可以无限重试同一步骤，永远不触发 STUCK

**后果:** STUCK 机制形同虚设。如果某个 Step 因为代码库根本性问题而不可能通过（如第三方 API 变更），循环会在该 Step 上无限消耗会话/token。

**修复:** 在 `implement_progress` 中增加 `"current_step_attempts": 0` 字段，每次重试同一 task+step 时递增，跨会话持久化。

---

### Bug 24: 无并发保护 — 两个 Claude 会话同时操作同一循环

**位置:** 全文（无锁机制）

**触发场景:**
1. 用户启动 IMPLEMENT 会话 A
2. 会话 A 运行缓慢，用户以为卡死，开新终端启动另一个 IMPLEMENT 会话 B
3. 两个会话同时读取 loop-state.json → 同一个 `current_task`
4. 两个会话同时修改同一文件、同时 commit、同时 push
5. Git push 冲突 → 一个会话成功、另一个失败 → 失败的会话可能 force push 或进入错误恢复

**更隐蔽的变体:**
- VERIFY 会话与迟到的 IMPLEMENT 会话重叠
- MERGE 会话与用户手动 git 操作重叠

**后果:** 分支历史损坏、loop-state 被覆盖、两个会话互相踩踏的工作。

**修复方案:**
- **方案 A（轻量级）:** 在 loop-state.json 中添加 `"lock": { "session_id": "uuid", "acquired_at": "ISO8601" }` 字段。每个 Phase 启动时检查锁：如果锁存在且 `acquired_at` 在 30 分钟内 → FATAL 拒绝启动；超时则强制接管
- **方案 B:** 使用文件锁 `flock` 机制，但跨平台兼容性差

---

## Medium

### Bug 25: FINALIZE 同步 master 可能引入与已通过 CI 不兼容的变更

**位置:** Phase 5 FINALIZE Step 2 (L867-869)

```bash
git fetch origin master
git merge origin/master -m "merge: sync master into {dev_branch} before finalize"
```

**问题:** 在循环运行期间（可能持续数小时甚至数天），master 上可能有其他人合入的 PR。FINALIZE 同步 master 到 dev 后跑 CI（Step 3），如果 CI 通过则一切正常。

**但如果 master 的变更引入了 breaking change**（如重命名了某个共用组件），FINALIZE 的 CI 修复逻辑（L881-884）允许"在 dev 上直接修复"，但此时修复的范围可能涉及**多个已完成功能的代码** — 修复者（Agent）对这些功能的上下文理解有限（不是当初实现它们的会话），可能引入回归。

**后果:** FINALIZE 的"直接修复"可能破坏已通过独立审查的功能代码，且这些修改不经过 VERIFY 审查。

**修复:** FINALIZE CI 修复时，如果修改涉及已完成功能的核心逻辑（非单纯的 import 路径调整），应创建临时 feat 分支走轻量 VERIFY 流程，而非直接在 dev 上修改。

---

### Bug 26: VERIFY 对 FAST_TRACK 的 Prompt 要求"读 commit message"，但无具体指引

**位置:** Phase 3 VERIFY 启动 Prompt (L533) + Step 1 (L550)

```
如果 spec_path 为 null（FAST_TRACK 任务），跳过 spec 阅读，改为阅读功能分支的 commit message。
```

**问题:** FAST_TRACK 可能有多个 commit（TDD 过程中可能拆分多次 commit）。"阅读 commit message" 没有指定：
- 读哪些 commit？全部？只看最后一个？
- 设计决策可能散落在不同 commit message 中
- Agent 可能只 `git log -1` 看最后一条，遗漏中间 commit 的设计决策说明

**对比:** 完整流程的 VERIFY 有明确的 spec 文件作为单一审查基线。FAST_TRACK 的审查基线分散且不明确。

**后果:** VERIFY 审查质量下降，可能遗漏 FAST_TRACK 实现中的设计决策偏差。

**修复:** 明确指定 `git log {dev_branch}..feat/{slug} --format="%H %s%n%b"` 读取功能分支相对于 dev 的所有 commit 的完整 message + body。

---

### Bug 27: Phase 间没有超时/熔断机制

**位置:** 全文

**问题:** 单个 Phase 没有时间或 token 消耗上限。文档只在 IMPLEMENT 中提到"上下文接近 60% 消耗时"的处理（L518），其他 Phase 没有类似机制。

**场景:**
- DESIGN 阶段使用 `/brainstorming` 技能陷入循环（L389 提到"同一确认点循环 3 次以上"，但只是"强制通过"，没有整体超时）
- VERIFY 阶段 diff 过大时逐文件审查耗尽上下文
- FIX 阶段修复一个问题引入新问题，反复修复

**后果:** 单个 Phase 消耗完整个会话上下文后崩溃，loop-state 可能处于不完整状态（Phase 中间态而非结束态），恢复时可能触发各种边界问题。

**修复:** 每个 Phase 定义 checkpoint 保存策略：
- DESIGN: brainstorming 每完成一个章节保存一次中间产出
- VERIFY: 每审查 3 个文件保存一次中间结论
- FIX: 每修复一个 issue commit 一次（当前已有此逻辑，但可明确为"每 issue 一 commit"）
- 所有 Phase: 添加"上下文接近 60% 时"的通用保存指引，不仅限于 IMPLEMENT

---

### Bug 28: CI 失败日志 commit 到 dev 会污染集成分支

**位置:** Phase 4 MERGE CI 失败路径 (L780-797)

**与 QA1 Bug 2 的区别:** QA1 Bug 2 关注 reset 后 loop-state 修改丢失的问题。本 bug 关注的是**CI 失败日志作为 commit 进入 dev 分支的副作用**。

**问题链:**
1. MERGE CI 失败 → `git reset --hard` 回退 dev
2. 写 CI 失败日志到 `loop/reviews/{date}-{slug}-ci-fail.md`
3. `git add` + `git commit` + `git push` 到 dev

此时 dev 分支上多了一个**不属于任何 feat 分支的 commit**，内容是 CI 失败日志。这违反了"dev 上不直接写代码"的规则（L62），虽然是日志不是代码，但它会：
- 被后续每个 feat 分支继承（从 dev 拉分支时包含此文件）
- 进入最终 squash merge 到 master 的内容
- 如果同一功能多次 CI 失败，dev 上堆积多个 ci-fail.md

**后果:** 生产仓库 master 中包含 CI 调试日志文件。

**修复:** CI 失败日志只写到本地（不 commit），或 commit 到 feat 分支（MERGE_FIX 时可读取），或在 FINALIZE 前清理 `loop/reviews/*-ci-fail.md`。

---

## Minor

### Bug 29: INIT 的 `.gitignore` 修改只存在于 dev 分支

**位置:** Phase 0 INIT (L259-263)

```bash
echo "loop/loop-state.json" >> .gitignore
git add .gitignore && git commit -m "chore: gitignore loop-state.json"
```

此时已在 dev 分支上（L247）。如果循环中途放弃（dev 从未 merge 回 master），master 的 `.gitignore` 不包含 loop-state.json。下次循环 INIT 又会重复追加同一行，且 `.gitignore` 中会出现重复条目。

**后果:** 轻微 — 重复的 `.gitignore` 条目不影响功能，但不整洁。

**修复:** 追加前先 `grep -q "loop-state.json" .gitignore || echo ...`，避免重复。

---

### Bug 30: review 文件和 CI 日志的存放位置不一致

**位置:** VERIFY (L606) vs MERGE CI 失败 (L780)

- VERIFY review: `loop/reviews/{date}-{slug}-review.md` → commit 到 **feat 分支**
- CI 失败日志: `loop/reviews/{date}-{slug}-ci-fail.md` → commit 到 **dev 分支**

同一个 `reviews/` 目录的文件分散在不同分支上，MERGE_FIX 需要读 CI 日志（在 dev 上），但工作在 feat 分支上。虽然 MERGE_FIX Step 3 会 `git merge {dev_branch}` 同步 dev 到 feat，但如果此步骤因冲突失败，Agent 可能读不到 CI 日志。

**后果:** MERGE_FIX 无法获取失败日志，盲修。

**修复:** 统一 review 类文件的 commit 策略 — 全部 commit 到 feat 分支，或全部 commit 到 dev 分支。

---

## 汇总

| 级别 | 数量 | 最高优先 |
|------|------|----------|
| Critical | 2 | Bug 19 — stash 无恢复静默丢失工作, Bug 20 — backup tag BLOCKED 时不存在 |
| Major | 4 | Bug 24 — 无并发保护最危险, Bug 23 — STUCK 计数不持久化 |
| Medium | 4 | Bug 25/27 — FINALIZE 修复和超时机制缺失 |
| Minor | 2 | 一致性和整洁性问题 |

### 修复优先级建议

1. **Bug 19（stash 恢复）** — 加 `stashed` 标记 + pop 逻辑，防止静默丢失工作
2. **Bug 24（并发保护）** — 加轻量级锁，防止两个会话互相踩踏
3. **Bug 20（backup tag 生命周期）** — 明确 BLOCKED 路径不需二次 reset
4. **Bug 23（STUCK 计数持久化）** — 在 implement_progress 加 `current_step_attempts`
5. **Bug 21（依赖范围语法）** — 在 queue JSON 示例中展开为明确数组
6. 其余按迭代节奏处理

---

## 与 QA1-QA4 的关系

| 轮次 | 维度 | Bug 范围 |
|------|------|----------|
| QA1 | 初始发现（状态、分支、幂等） | 原始 Bug 1-10 |
| QA2 | QA1 修复验证 + 新发现 | New Bug 1-6 |
| QA3 | 状态机逻辑、门禁一致性 | Bug 1-8 |
| QA4 | 跨阶段一致性、边界场景、安全 | Bug 9-18 |
| **QA5** | **运行时语义、并发安全、恢复路径** | **Bug 19-30** |

五轮合计：

| 级别 | QA3 | QA4 | QA5 | 总计 |
|------|-----|-----|-----|------|
| Critical | 3 | 3 | 2 | **8** |
| Major | 3 | 3 | 4 | **10** |
| Medium | 2 | 4 | 4 | **10** |
| Minor | 2 | — | 2 | **4** |

**QA5 观察:** 经过四轮修复，状态机和门禁的硬伤已基本收敛。本轮聚焦的运行时语义和恢复路径问题属于"happy path 之外的 edge case"，在实际多会话运行中有较高触发概率（尤其是 Bug 19 stash 丢失和 Bug 24 并发踩踏）。建议优先修复后再启动实际循环。
