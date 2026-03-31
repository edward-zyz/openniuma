# Bug Report: autonomous-dev-loop v2.1 — QA Round 1

> **审查对象:** `loop/loop_v2/autonomous-dev-loop.md` (975 行)
> **审查日期:** 2026-03-23
> **审查角色:** 资深测试 / Debug

---

## P0 — 会导致流程中断/数据错误

### Bug 1: FAST_TRACK 降级到 full 后，VERIFY 读不到 spec → 流程卡死

**位置:** FAST_TRACK 降级机制 (L289-290) + VERIFY Step 1 (L528-538)

**问题:** 降级机制说"已有 commit → 保留分支，更新状态 `track: "full"`, `current_phase: "VERIFY"`"。但 `track: "full"` 会让 VERIFY 走完整流程的 Step 1（需求覆盖审查），需要读取 `spec_path` 指向的 spec 文件。而 FAST_TRACK 根本不产生 spec 文件，`spec_path` 为 null。

**后果:** VERIFY 会话启动后尝试读一个不存在的 spec → 要么 FATAL 退出，要么跳过审查变成走过场。

**修复建议:** 降级后应设置 `track: "full"` 但 `current_phase: "DESIGN"`（回到设计阶段补 spec），或者降级后保持 `track: "fast"` 让 VERIFY 按快速通道审查。

---

### Bug 2: BLOCKED 清理流程不清理 backup tag → 残留 tag 导致下次 MERGE 打 tag 失败

**位置:** BLOCKED 清理流程 (L617-629) vs MERGE Step 1 (L683-685)

**问题:** BLOCKED 清理流程（第4步）清空了 `branch`, `slug` 等字段，但**没有清理 backup tag**。如果是 MERGE CI 失败 3 次触发 BLOCKED，此时 `backup/pre-merge-{slug}` tag 仍然存在于本地和远程。虽然当前 slug 不会再用，但 tag 会一直残留。

另外，如果 MERGE_FIX 在 step 9（清理 tag）之前就 BLOCKED 了（比如修复后再次 MERGE 又失败累计到 3 次），tag 也不会被清理。

**修复建议:** BLOCKED 清理流程增加一步：如果存在 `backup/pre-merge-{slug}` tag，删除它（本地 + 远程）。

---

### Bug 3: DESIGN 步骤编号跳跃，步骤 8 丢失

**位置:** Phase 1: DESIGN (L397-405)

**问题:** 步骤编号从 7 直接跳到 9，缺少步骤 8。当前步骤 7 是 "commit + push spec + plan"，步骤 9 是 "更新状态"。中间可能丢失了一个步骤（比如创建功能分支？或者 spec review？）。

**后果:** 如果只是编号错误还好，但如果确实丢了一个步骤（看路径 B 的流程，spec 写完后似乎缺少了自检环节），那 full track 路径 B 的 spec 质量没有保障。

---

### Bug 4: DESIGN 将 spec/plan commit 到 dev 分支 → IMPLEMENT 修改 plan 后 merge 回 dev 产生冲突

**位置:** DESIGN Step 7 (L397) + IMPLEMENT Step 5 (L461) + MERGE (L690)

**问题:** DESIGN 在 dev 分支上 commit 了 spec 和 plan 文件。IMPLEMENT 从 dev 创建 feat 分支（带着这些文件），然后在实现过程中会修改 plan（步骤 5 勾选 `[x]`、记录偏差、修正步骤）。MERGE 时把 feat 分支 merge 回 dev，plan 文件在两边都有修改（dev 侧有后续功能的 DESIGN commit 也可能修改同目录文件）→ 合并冲突风险。

**后果:** 每次 MERGE 都可能在 plan 文件上产生冲突。

**修复建议:** 两种方案：
- **方案 A:** DESIGN 创建 feat 分支，spec/plan commit 到 feat 分支而非 dev
- **方案 B:** IMPLEMENT 不修改 plan 文件（只更新状态文件中的 `implement_progress`），plan 保持只读

---

## P1 — 逻辑漏洞，可能导致意外行为

### Bug 5: verify_attempts 不在 FIX 后重置 → 实际只有 3 次总机会

**位置:** VERIFY Step 5 (L608-611) + FIX Step 7 (L657)

**问题:** FIX 完成后将 `current_phase` 设为 "VERIFY"，但不重置 `verify_attempts`。VERIFY→FIX→VERIFY→FIX→VERIFY 循环中，第 3 次 VERIFY 失败就直接 BLOCKED，不管 FIX 是否取得了进展。

**是否有意为之？** 如果是有意限制总尝试次数，应在文档中明确说明"3 次是总次数上限，非每轮重置"。如果期望"连续 3 次 FIX 都没效果才 BLOCKED"，需要不同的计数策略（如 FIX 通过了新的门禁后重置计数器）。

---

### Bug 6: `blocked_by_dependency` 没有恢复机制

**位置:** BLOCKED 清理流程 (L623-624) + 依赖规则 (L891-893)

**问题:** 如果 #3（空间品牌配置扩展）被 BLOCKED，#4 和 #7 会被级联标记为 `blocked_by_dependency`。但没有定义**解除**这个状态的流程。人工后来修复了 #3 并改为 `done`，依赖它的 #4 和 #7 不会自动恢复为 `pending`。

**修复建议:** 要么文档明确说明 `blocked_by_dependency` 在当前 batch 中不可恢复（需在下一个 batch 重新处理），要么增加人工干预后的状态恢复流程。

---

### Bug 7: MERGE CI 失败后，CI 失败日志 commit 去向不明确 → 可能误 commit 到 dev

**位置:** MERGE Step 4 CI 失败部分 (L742)

**问题:** 文档说"记录 CI 失败日志到 `reviews/{date}-{slug}-ci-fail.md`，commit + push 到 feat 分支（dev 已 reset，日志存在 feat 分支上）"。但此时当前分支是 dev（刚执行了 `git reset --hard`），文档没有写 `git checkout feat/{slug}` 的显式切换步骤。

**后果:** 如果 AI 忘记切分支，CI 失败日志会被 commit 到 dev 分支（在 backup 点之上新增 commit），搞乱 dev 分支的干净状态。

**修复建议:** 在"记录 CI 失败日志"前增加显式步骤：`git checkout feat/{slug}`。

---

### Bug 8: FINALIZE 中 CI 失败无处理流程

**位置:** FINALIZE Step 3 (L818-822)

**问题:** FINALIZE 在 merge master 后跑 CI 验证，如果 CI 失败（lint/test/build），没有定义处理流程。这不是单个 feat 分支的问题，是整个 dev 分支在 sync master 后的集成失败，影响最终 PR 质量。

**修复建议:** 增加 FINALIZE CI 失败处理：
- 尝试在 dev 上修复（限 2 次）
- 仍失败则回退 merge、设置 `system_alert = true`、等待人工

---

### Bug 9: 模板中 `current_phase` 初始值与任务复杂度不匹配

**位置:** `loop-state.template.json` (L5-6)

**问题:** 模板文件中 `"current_phase": "DESIGN"` 且 `"track": "fast"`。但任务 #1 复杂度为"低"，INIT 阶段应设置 `current_phase: "FAST_TRACK"`。模板与文档逻辑不一致，可能误导 AI 使用错误的初始状态。

**修复建议:** 模板改为 `"current_phase": "FAST_TRACK"` 或添加注释说明"由 INIT 阶段根据复杂度填充"。

---

## P2 — 健壮性/可维护性问题

### Bug 10: 状态文件写入非原子操作 → 中途崩溃可能损坏

**问题:** 写入流程是"先 cp 备份，再 Write 写入新内容"。如果 Write 过程中 Claude Code 会话被杀掉或机器崩溃，状态文件可能是空的或部分写入的。`loop-state.prev.json` 备份机制能兜底，但需要人工干预恢复。

**修复建议:** 写入到临时文件 `loop-state.tmp.json`，再 `mv` 覆盖（`mv` 在大多数文件系统上是原子操作）：
```bash
cp ~/.poi-loop/loop-state.json ~/.poi-loop/loop-state.prev.json 2>/dev/null || true
# Write 工具写入 ~/.poi-loop/loop-state.tmp.json
mv ~/.poi-loop/loop-state.tmp.json ~/.poi-loop/loop-state.json
```

---

### Bug 11: INIT 没有幂等性检查 → 误执行覆盖进度

**位置:** Phase 0: INIT (L240-274)

**问题:** 如果 INIT 被误执行两次（用户不确定上次是否成功），会再次创建 dev 分支（可能失败因为已存在）、覆盖状态文件（丢失已有进度）。

**修复建议:** INIT 启动时先检查 `~/.poi-loop/loop-state.json` 是否已存在且 `schema_version == 3`，如果存在则输出当前进度并提示用户确认是否重新初始化。

---

### Bug 12: 状态校验中的 shell 变量来源不明

**位置:** 通用步骤：状态校验 (L192-220)

**问题:** 校验脚本使用了 `${dev_branch}` 和 `${branch}` 变量，但没有说明从 JSON 提取的方式（`jq`？`python3`？Claude Code 的 Read 工具？）。作为伪代码可以理解意图，但如果 AI 尝试直接执行这段 shell 脚本会失败。

**修复建议:** 在脚本前增加变量提取步骤，或改为伪代码标注。

---

### Bug 13: IMPLEMENT 和 VERIFY 的门禁命令不一致

**位置:** IMPLEMENT Step 6 (L463-469) vs VERIFY Step 2 (L542-553)

**问题:**
| 阶段 | 门禁命令 |
|------|----------|
| IMPLEMENT 每个 Task | `lint` + `test` + `tsc --noEmit -p frontend` |
| IMPLEMENT 最终 | `lint` + `test` + `build` |
| VERIFY | `lint` + `test` + `build` + `tsc --noEmit -p frontend` |

IMPLEMENT 最终门禁**缺少** `tsc --noEmit -p frontend`，而 VERIFY 有。这意味着 IMPLEMENT 可能通过但 VERIFY 因前端类型错误失败，浪费一轮 VERIFY→FIX 循环。

**修复建议:** 统一所有阶段的完整门禁为 4 条命令：`lint` + `test` + `build` + `tsc --noEmit -p frontend`。

---

### Bug 14: MERGE_FIX 无条件 merge dev 到 feat → 可能引入不相关变更干扰修复

**位置:** MERGE_FIX Step 4 (L771-774)

**问题:** 文档说将 dev 最新代码 merge 到 feat 分支。但此时 dev 已 `git reset --hard` 回到 merge 前状态。如果 CI 失败原因是**当前功能本身的 bug**（非兼容性问题），merge dev 是多余操作，反而可能引入干扰。

**建议:** 先分析 CI 失败原因，只在明确是兼容性/集成问题时才 merge dev，否则直接在 feat 分支上修。

---

## P3 — 设计层面的讨论点

### 观察 1: 一个功能一个会话的约束 vs 上下文重建成本

每个 Phase 开一个新会话意味着 full track 需要 4 个会话才能完成一个功能。如果 VERIFY 失败进入 FIX→VERIFY 循环，每次还要开 2 个新会话。每次新会话都要：读文档 → 读状态 → 读代码 → 读 spec → 读 plan，上下文重建成本不低。

**思考:** 对于中等复杂度的功能，是否可以将 DESIGN+IMPLEMENT 合并为一个会话（类似 FAST_TRACK 但保留 spec/plan 产出）？

### 观察 2: FAST_TRACK 和完整流程的 Phase 枚举混在同一字段

`FAST_TRACK` 和 `DESIGN`/`IMPLEMENT` 是互斥路径，但共用 `current_phase` 字段。如果未来增加更多 track 类型，语义会模糊。可考虑将 track 和 phase 完全解耦。

---

## 总结

| 级别 | 数量 | 核心风险 |
|------|------|----------|
| **P0** | 4 | 流程中断、数据冲突、步骤丢失 |
| **P1** | 5 | 逻辑漏洞、状态不一致 |
| **P2** | 5 | 健壮性、一致性 |
| **P3** | 2 | 设计讨论 |

**最高优先级修复:**
1. **Bug 1** — FAST_TRACK 降级后 VERIFY 卡死（几乎必然触发）
2. **Bug 4** — spec/plan commit 到 dev 导致 merge 冲突（每个 full track 功能必触发）
3. **Bug 13** — 门禁不一致导致无谓的 VERIFY 失败循环（高概率触发）
