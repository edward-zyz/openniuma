# 自治研发循环 QA 审查报告 — 第二轮

> 审查对象：`loop/autonomous-dev-loop.md`（QA1 修复后版本）
> 审查日期：2026-03-23
> 审查视角：资深 QA，验证 QA1 修复 + 寻找新问题

---

## QA1 修复验证

| QA1 Bug | 状态 | 修复方式 |
|---------|------|----------|
| Bug 1 (P0) 分支校验 FATAL | **已修复** | 第 190 行：校验仅在 `VERIFY/FIX/MERGE/MERGE_FIX` 时执行，IMPLEMENT 阶段跳过 |
| Bug 5 (P0) 计数器不重置 | **已修复** | 第 600、739 行：BLOCKED 时显式重置 `verify_attempts`, `merge_fix_attempts` 为 0 |
| Bug 9 (P0) loop-state 分叉 | **已修复** | 第 669 行：merge 冲突时取 feat 分支版本 |
| Bug 2 (P1) reset 后状态丢失 | **已修复** | 第 723 行：添加 warning 说明 reset 后需重新写入 |
| Bug 8 (P1) 依赖传播 | **部分修复** | 第 707 行：MERGE 成功时递归传播 BLOCKED，但 BLOCKED 路径本身未传播（见 New Bug 1） |
| Bug 7 (P1) FINALIZE 无 CI 失败处理 | **已修复** | 第 822-826 行：添加了修复+重试+人工兜底逻辑 |
| Bug 3 (P2) MERGE_FIX merge 脏状态 | **已修复** | 第 770-775 行：添加详细注释说明 dev 已回退的语境 |
| Bug 6 (P2) FAST_TRACK 缺后端 typecheck | **已修复** | 第 281 行：添加 `npx tsc --noEmit -p backend` |
| Bug 10 (P3) 校验变量未绑定 | **已修复** | 第 176 行：声明为伪代码，Agent 需从 JSON 提取值 |
| Bug 4 (P3) tag 重名 | **已修复** | 第 662 行：使用 `git tag -f` 强制覆盖 |

---

## 新发现的问题

### New Bug 1 (P1): BLOCKED 传播只在 MERGE 成功路径执行，BLOCKED 路径本身不传播

**位置:** 第 600 行 vs 第 707 行

BLOCKED 传播逻辑（第 707 行）写在 **"CI 通过"** 分支内部。但功能被 BLOCKED 有两个入口：

- **VERIFY 失败 3 次**（第 600 行）："标记 BLOCKED...推进到下一条"
- **MERGE_FIX 失败 3 次**（第 739 行）："标记 BLOCKED...推进到下一条"

这两处都只说"推进到下一条"，**没有触发第 707 行的传播逻辑**。

**场景:** #3 在 VERIFY 阶段被 BLOCKED → #4（依赖 #3）不会被标记 BLOCKED，仍是 pending。循环跳过 #4（因 depends_on 不满足），但不标记它。当所有无依赖任务做完后，#4 仍然是 pending 状态 → 循环发现"还有 pending 项"但无法执行。

实际上第 708 行"推进到下一条"的逻辑是找 `status = "pending"` 且 `depends_on` 全部在 `completed` 中的项，如果没有就进 FINALIZE，所以不会卡死。但 **`blocked` 列表在中间过程不完整**，人工查看进度会被误导——可能以为只有 1 个功能 BLOCKED，实际有一整条依赖链受影响。

**建议:** BLOCKED 路径（第 600、739 行）也需要执行和第 707 行相同的递归传播逻辑。

---

### New Bug 2 (P1): VERIFY 阶段门禁标准比 IMPLEMENT 和 FAST_TRACK 宽松

**位置:** 第 528-535 行 vs 第 438-443 行 vs 第 277-282 行

| 阶段 | lint | test | build | tsc frontend | tsc backend |
|------|------|------|-------|-------------|-------------|
| IMPLEMENT Task 级 (第 438-443 行) | ✅ | ✅ | ❌ | ✅ | ✅ |
| IMPLEMENT 最终 (第 463-467 行) | ✅ | ✅ | ✅ | ✅ | ❌ |
| FAST_TRACK (第 277-282 行) | ✅ | ✅ | ✅ | ✅ | ✅ |
| VERIFY (第 528-535 行) | ✅ | ✅ | ✅ | ✅ | ❌ |

VERIFY 是最终把关关卡，反而是唯一不跑 `npx tsc --noEmit -p backend` 的阶段。第 535 行注释说 "npm run build 的后端部分已包含 tsc 编译"，但如果 build 的 tsconfig 与 `--noEmit` 的 tsconfig 有差异（比如 `skipLibCheck`、`strict` 配置不同），VERIFY 就可能漏过 IMPLEMENT 阶段能捕获的后端类型错误。

更关键的是 **IMPLEMENT Task 级没跑 build，VERIFY 没跑 backend typecheck**，两个阶段的检查项是互补的而不是包含关系。如果 FIX 阶段修改了后端代码引入了类型错误，VERIFY 不会捕获。

**建议:** 统一所有阶段的门禁为 5 项全跑（lint + test + build + tsc frontend + tsc backend），消除不一致。或者至少让 VERIFY 作为最终关卡覆盖全部检查项。

---

### New Bug 3 (P2): FAST_TRACK 的 loop-state.json commit 到哪个分支未指定

**位置:** 第 293 行

> "commit + push loop-state.json"

FAST_TRACK 在第 274 行创建了 feat 分支并在上面工作。第 293 行的 commit 隐含是在 feat 分支上。但 DESIGN 阶段（第 377 行）明确说 commit 到 dev 分支。

这意味着 FAST_TRACK 的 loop-state.json 也会出现 dev/feat 分支分叉问题——和原 Bug 9 同样的根因。第 669 行的 merge 冲突处理策略虽然解决了 MERGE 阶段的问题，但 **FAST_TRACK 没有经过 DESIGN 阶段**，loop-state.json 在 dev 上的版本可能仍是上一个功能结束时的状态（`current_phase` 不是当前功能的 phase）。

MERGE 阶段是独立会话启动，启动 Prompt（第 644 行）说"阅读 loop-state.json"，但没说先切到哪个分支。Agent 如果在 dev 分支上读到旧版 loop-state，会对当前状态产生错误判断。

**建议:** 明确 FAST_TRACK 第 293 行 commit 到 feat 分支，并在 MERGE 启动 Prompt 中指示"先 checkout feat 分支读取 loop-state.json 获取最新状态，再切到 dev 执行合入"。

---

### New Bug 4 (P2): "3 个以上功能连续 BLOCKED" 检测没有实现

**位置:** 第 910 行（异常处理表）

异常处理表声称"3 个以上功能连续 BLOCKED → 可能有系统性问题，等待人工排查"，但整个流程中**没有任何地方统计连续 BLOCKED 次数**。

`blocked` 列表只记录功能名和原因，不记录顺序。MERGE/VERIFY 的 BLOCKED 路径只做"推进到下一条"，不检查是否已经连续 BLOCKED 了多个功能。

**后果:** 如果存在系统性问题（比如 dev 分支上有一个隐藏的 CI 破坏），所有功能都会逐个被 BLOCKED，循环会跑完整个 queue 才进入 FINALIZE，浪费大量时间和 API 调用。

**建议:** 在"推进到下一条"逻辑中添加检查：`if blocked.length >= 3 && 最近 3 个完成的动作都是 BLOCKED → FATAL，等待人工`。或者在 loop-state.json 中添加 `consecutive_blocked_count` 字段。

---

### New Bug 5 (P3): FINALIZE 允许在 dev 上直接写代码，与规则矛盾

**位置:** 第 824 行 vs 第 62 行

第 62 行规则："dev 分支上不直接写代码，只接受来自 feat/* 分支的 merge"

第 824 行："在 dev 分支上直接修复（这是唯一允许在 dev 上直接写代码的场景，因为所有功能分支已合入）"

虽然写了例外说明，但如果 Agent 在状态校验阶段先读到了第 62 行的规则并内化为硬约束，可能会拒绝在 dev 上直接修复，转而去创建一个新的 feat 分支来修复——但此时已经没有功能分支的概念了，流程会混乱。

**建议:** 在第 62 行的规则中添加"（FINALIZE 阶段除外，见 Phase 5）"的例外说明，让两处描述一致。

---

### New Bug 6 (P3): IMPLEMENT 最终门禁缺 `npx tsc --noEmit -p backend`

**位置:** 第 463-467 行

```bash
npm run lint && npm test && npm run build
npx tsc --noEmit -p frontend       # vite build 不做类型检查，需单独验证
```

Task 级门禁（第 438-443 行）跑 4 项包含 `tsc --noEmit -p backend`，但最终门禁换成了 `npm run build`（假设 build 覆盖了后端 typecheck）却丢了显式的 backend typecheck。

如果最后一个 Task 修复中在 `npm run build` 之后又做了一次小改动只跑了 lint+test，最终门禁的 build 虽然会编译后端，但与 `--noEmit` 的严格程度可能不同。

**建议:** 最终门禁也加上 `npx tsc --noEmit -p backend`，保持和 Task 级门禁一致。

---

## 严重程度排序

| 严重度 | Bug | 影响 |
|--------|-----|------|
| **P1** | New Bug 1 | BLOCKED 传播不在 BLOCKED 路径执行，状态文件中间过程不完整 |
| **P1** | New Bug 2 | VERIFY 门禁比 IMPLEMENT/FAST_TRACK 宽松，可能漏过后端类型错误 |
| **P2** | New Bug 3 | FAST_TRACK 的 loop-state 分支归属不清，MERGE 启动时可能读到旧状态 |
| **P2** | New Bug 4 | 连续 BLOCKED 检测只在异常表声明，实际流程未实现 |
| **P3** | New Bug 5 | FINALIZE dev 直接写代码与规则表第 62 行自相矛盾 |
| **P3** | New Bug 6 | IMPLEMENT 最终门禁缺 backend typecheck，与 Task 级门禁不一致 |

---

## 总结

**QA1 的 10 个 bug 全部得到修复或缓解**（Bug 8 部分修复，遗留为 New Bug 1）。

新发现 6 个问题，最高 P1，**无 P0 阻断性 bug**。循环的 happy path 已基本可靠，剩余问题集中在：

1. **门禁一致性**（New Bug 2、6）— 不同阶段的检查项不统一，建议定义一个"标准门禁"统一引用
2. **BLOCKED 传播完整性**（New Bug 1）— 传播逻辑只在一个代码路径中，需要提取为通用步骤
3. **状态文件分支归属**（New Bug 3）— loop-state.json 的"真相来源"需要更明确的约定
4. **防御性检测**（New Bug 4、5）— 连续 BLOCKED 检测和规则例外需要落地到实际流程中
