# 自治研发循环 QA 审查报告

> 审查对象：`loop/autonomous-dev-loop.md`
> 审查日期：2026-03-23
> 审查视角：资深 QA，聚焦隐藏 bug 和潜在失败点

---

## Bug 1: DESIGN 阶段在 dev 分支上 commit spec/plan，但 loop-state 里 `branch` 已设为 `feat/{slug}`

**位置:** 第 368-372 行

```json
{ "current_phase": "IMPLEMENT", "branch": "feat/{slug}", "spec_path": "...", "plan_path": "..." }
```

DESIGN 阶段全程在 dev 分支上工作（第 318-323 行），spec/plan commit 到 dev 分支（第 372 行）。但此时 `branch` 已被设为 `feat/{slug}`，而这个 feat 分支**还不存在**——它要到 IMPLEMENT 阶段第 4 步才会创建。

**后果:** 如果 DESIGN 结束后会话中断，新会话执行状态校验（第 186-189 行）会发现 `branch` 非空，去 `git rev-parse --verify feat/{slug}`，结果分支不存在 → **FATAL 停止**。这是一个正常流程下必然触发的 bug。

---

## Bug 2: CI 失败回退后，在 dev 分支 commit loop-state.json，但 dev 已被 reset 了

**位置:** 第 710-733 行

CI 失败时先执行 `git reset --hard "backup/pre-merge-{slug}"`，把 dev 回退到合入前。然后第 733 行说"commit 并 push loop-state.json 和 CI 失败日志到 dev 分支"。

**问题:** `reset --hard` 之后工作区是干净的旧状态。CI 失败日志是刚写的新文件没问题，但 loop-state.json 的修改也被 reset 掉了。文档没有指示在 reset 之后重新写入 loop-state.json 的修改，Agent 可能会困惑或者直接 commit 一个没有变更的文件。

---

## Bug 3: MERGE_FIX 中 `git merge {dev_branch}` 到 feat 分支可能引入脏状态

**位置:** 第 761-762 行

```bash
git checkout feat/{slug}
git merge {dev_branch}
```

但此时 dev 分支刚刚被 `reset --hard` 回退过——dev 上**不包含**当前 feat 的代码。这个 merge 的意图是"同步 dev 最新代码到 feat 分支"，但 dev 已经回退了，实际上这步是多余的，甚至可能引入合并冲突（如果 dev 上有其他功能的改动与 feat 有交集）。

更关键的是：如果此次是第 2 或第 3 次 MERGE_FIX 尝试，dev 可能已经 push 过 reset 后的状态，然后又被 merge 过一次又 reset 了。**dev 分支的远程和本地可能不一致**，push 会被拒绝（non-fast-forward）。

---

## Bug 4: backup tag 管理的边界情况

**位置:** 第 657、713 行

CI 失败回退时只删了本地 tag（第 713 行），tag 本身没有 push 到远程所以远程不会残留。但如果 MERGE_FIX 后重新进入 MERGE 阶段（第 657 行），会再次创建同名 tag——如果上次删除失败或时序问题，tag 仍存在，`git tag` 会报错 "tag already exists"。

实际影响较小：新功能的 slug 不同，tag 名也不同，不会跨功能冲突。但同一功能的多次 MERGE 尝试可能冲突。

---

## Bug 5: `verify_attempts` 和 `merge_fix_attempts` 的重置时机不对称

**位置:** 第 703 行

MERGE 成功后会清空 `verify_attempts` 和 `merge_fix_attempts`。但如果功能被标记 BLOCKED 跳到下一条（第 595 行和第 730 行），**没有提到重置这两个计数器**。

**后果:** 下一个功能继承了上一个被 BLOCKED 功能的计数器值。如果上个功能 verify 了 2 次后 BLOCKED，下一个功能第 1 次 VERIFY 失败就会被认为是第 3 次，直接被 BLOCKED。**串联误杀。**

---

## Bug 6: FAST_TRACK 的门禁缺少 `npx tsc --noEmit -p backend`

**位置:** 第 273-277 行

FAST_TRACK 的门禁是：
```bash
npm run lint && npm test && npm run build
npx tsc --noEmit -p frontend
```

但 IMPLEMENT 阶段（第 437 行）有 4 项门禁，包括 `npx tsc --noEmit -p backend`。FAST_TRACK 少了后端 typecheck。

第 530 行 VERIFY 阶段的注释说"`npm run build` 的后端部分已包含 `tsc` 编译，无需再单独执行"——但 `tsc` 编译（生成 JS）和 `tsc --noEmit`（严格类型检查）的行为可能不同（取决于 tsconfig 配置）。如果 backend 的 tsconfig 里 build 用了 `skipLibCheck: true` 或其他宽松配置，`npm run build` 可能不会捕获所有类型错误。

**FAST_TRACK 和 IMPLEMENT 的门禁标准不一致**，低复杂度任务反而检查更松，这与"质量靠自动化门禁"的原则矛盾。

---

## Bug 7: FINALIZE 阶段 merge master 到 dev 后 CI 失败没有处理逻辑

**位置:** 第 797-807 行

```bash
git merge origin/master -m "merge: sync master into {dev_branch} before finalize"
# ...
npm run lint && npm test && npm run build
```

如果这个 CI 失败了呢？FINALIZE 没有像 MERGE 阶段那样的失败处理逻辑（回退、记录日志、重试）。文档只是线性地往下走到创建 PR。

**后果:** 如果 master 在循环期间被人工推入了其他变更，merge 后可能 CI 失败，FINALIZE 阶段会卡死——没有回退机制，没有重试机制，也没有 BLOCKED 逻辑。

---

## Bug 8: 依赖解析逻辑不处理间接依赖传播

**位置:** 第 868、870 行

考虑这个场景：**#3 BLOCKED → #4（依赖 #3）应该被 BLOCKED → #10（依赖 #1-#6，包含 #4）也应该被 BLOCKED**。

第 776 行提到了"剩余 pending 但依赖被 BLOCKED 的项应记入 blocked"，但这只在 FINALIZE 阶段处理。在功能推进时（第 701 行），`depends_on` 检查的是 `completed`，不会主动去传播 BLOCKED 状态。

**后果:** #3 BLOCKED 后，循环会跳过 #4 和 #7（它们的 depends_on 中有 #3 不在 completed 中），但不会将它们标记为 BLOCKED。当循环遍历完其他功能到达 #10 时，#10 的依赖包含 #4，#4 既不在 completed 也不在 blocked 中，状态仍是 pending。#10 会被跳过但不会被 BLOCKED，进入**无限等待**——直到 FINALIZE 时才会清理，但 FINALIZE 的触发条件是"没有更多可执行项"，而这些 pending 项的 depends_on 永远不会满足，可能导致循环无法判断是否应该终止。

---

## Bug 9: loop-state.json 同时被多个角色 commit 到不同分支

**状态文件写入分支不一致：**
- DESIGN：commit 到 **dev** 分支（第 372 行）
- IMPLEMENT：commit 到 **feat** 分支（第 457 行）
- VERIFY：commit 到 **feat** 分支（第 598 行）
- MERGE 成功后：commit 到 **dev** 分支（第 706 行）
- MERGE 失败后：commit 到 **dev** 分支（第 733 行）

**后果:** loop-state.json 在 feat 和 dev 上的版本会分叉。MERGE 阶段切到 dev 读到的 loop-state 可能是旧版的（DESIGN 阶段写入的），不包含 IMPLEMENT 和 VERIFY 阶段在 feat 分支上的更新。merge --no-ff 时如果 loop-state.json 有冲突（几乎必然），需要手动解决，但文档对此没有任何指导。

---

## Bug 10: 状态校验脚本使用的变量来源不明

**位置:** 第 176-200 行

校验脚本引用了 `${dev_branch}`、`${branch}`、`${spec_path}` 等 shell 变量，但没有说明这些变量从哪里来。对于 Claude Code 来说，它需要先读 JSON 文件再提取字段，但脚本是以 bash 片段形式写的，暗示可以直接执行。

**实际影响:** Agent 可能会尝试直接执行这些脚本片段，发现变量为空，所有校验都会被跳过（`[ -n "" ]` 为 false → if 分支不执行），**校验形同虚设**。

---

## 严重程度排序

| 严重度 | Bug | 影响 |
|--------|-----|------|
| **P0** | Bug 1 | 正常流程必触发 FATAL，循环一定会卡在 DESIGN→IMPLEMENT 交接处 |
| **P0** | Bug 5 | 计数器不重置导致后续功能被误杀 |
| **P0** | Bug 9 | loop-state.json 在多分支上分叉，merge 必冲突 |
| **P1** | Bug 2 | CI 失败处理中 reset 后状态丢失 |
| **P1** | Bug 8 | 依赖传播缺陷可能导致循环无法终止 |
| **P1** | Bug 7 | FINALIZE 缺失 CI 失败处理 |
| **P2** | Bug 3 | MERGE_FIX 中不必要的 merge 可能引入问题 |
| **P2** | Bug 6 | FAST_TRACK 门禁比完整流程宽松 |
| **P3** | Bug 10 | 校验脚本变量未绑定 |
| **P3** | Bug 4 | tag 管理的边界情况 |

**Bug 1、5、9 是最致命的**——它们会在正常执行路径上触发，不需要异常场景。建议优先修复这三个。
