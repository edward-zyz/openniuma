# fix(openniuma): worker 重启时进度丢失 + 环境污染导致任务卡死

## 背景

2026-03-28 的 batch 运行中，task 7 和 task 8 反复卡住。经排查发现 5 个独立根因，其中 4 个已修复，1 个需要架构改进。

---

## 案例 1：Task 8 从 CI_FIX 回退到 DESIGN_IMPLEMENT，已完成的工作从头重做

### 症状
Task 8 已走完 MERGE → FINALIZE → CI_FIX，PR #40 已创建。调度器重启后，task 8 回退到 DESIGN_IMPLEMENT，Claude 重新探索代码、写 spec、实现功能——做了一遍已完成的工作。

### 根因链
1. MERGE 阶段的 Claude 把 `current_item_id` 写成了 `null`（Claude 认为"任务完成，队列无 pending"）
2. CI_FIX 阶段被 SIGTERM 杀掉，调度器重启
3. `create_worker_state()` 检查 worker state 是否已存在：`json.get('current_item_id', -1)` 当 key 存在但值为 `None` 时返回 `None`（不是默认值 `-1`）
4. shell 比较 `[ "None" = "8" ]` → 不匹配 → 重建 state，`current_phase` 被硬编码为 `DESIGN_IMPLEMENT`
5. 已完成的任务从头开始

### 已修复
- `create_worker_state` 去掉 ID 匹配检查，改为只判断文件是否存在且非空（`-f && -s`）— commit `5d7ef61`

### 附带问题
- CI_FIX 不在 `needs_worktree()` 列表 → Claude 在主仓库执行 `git checkout` 污染工作目录 — commit `1ebeb60`
- trap handler 直接 `exit 0` 跳过分支守卫 → SIGTERM 时分支污染无法恢复 — commit `1ebeb60`
- PR #40 包含 63 个文件（feat 分支从 dev 切出，带上了所有其他 task 的改动），有 merge conflict → 手动关闭，从 master cherry-pick 创建干净的 PR #41

---

## 案例 2：Task 7 MERGE 阶段陷入盲跑测试循环

### 症状
Task 7 的 MERGE 会话中，Claude 成功解决了 merge conflict、提交了 merge commit，但跑 `npm test` 时后端测试大面积失败（>10 个 test suite），Claude 反复重跑测试 3 次（每次 2 分钟+），共消耗 ~10 分钟，始终无法通过。

### 根因
Worktree 的 `backend/.env` 中 `DATABASE_URL=postgresql://...poi_dev`，指向**主库**而非 worktree 独立库 `poi_dev_loop_recalibrate_detail_page_stats`。

原因：`ensure_worktree()` 复用已有 worktree 目录时（第 175-178 行）直接返回，**不重新执行 `after_create` hook**。如果 worktree 被 cleanup 后重建，或 .env 因某种原因丢失/错误，不会修复。

错误日志特征：`error: relation "error_logs" does not exist`（数据库缺表，因为 migration 没在正确的库上跑）。

### Claude 的行为
Claude 无法识别这是环境问题而非代码问题：
- 09:44:29 `npm test` — 大面积失败
- 09:46:47 重跑 `npm test | grep fail` — 仍然失败
- 09:49:07 只跑前端测试 — 通过（确认是后端问题）
- 09:49:19 检查了 `DATABASE_URL` — 看到 `poi_dev` 但**没有意识到这是错的**
- 09:49:36 ~ 09:54:12 反复跑后端测试 — 死循环

### 已修复
- `ensure_worktree` 复用时检测 `.env` 是否缺失或指向主库，异常时重跑 `after_create` hook — commit `0d21624`
- `merge.md` / `verify.md` 增加环境预检步骤，指导 Claude 在跑 CI 前检查 `DATABASE_URL` — commit `0d21624`

---

## 待改进（架构层面）

以上修复都是针对具体 bug 的补丁。更系统的改进方向：

1. **State 写入受控** — Claude 不应直接 Write 整个 state.json，应通过受控的 CLI 命令做合法的状态转换，框架校验前置条件
2. **Phase 注册表** — 所有 phase 定义在单一位置，`needs_worktree` / `build_prompt` / model config 都从注册表派生，新增 phase 时不会遗漏
3. **会话前 Preflight Check** — 每次启动 Claude 会话前校验环境完整性（worktree 隔离、DB 连通、.env 正确性），失败则修复或阻断
4. **PR 隔离性** — FINALIZE 阶段应从 master cherry-pick 创建 PR，而非基于 dev 分支（后者会带上所有无关改动）

---

## 相关 Commits
- `1ebeb60` fix(openniuma): 三层防护修复 CI_FIX 阶段主仓库分支污染
- `5d7ef61` fix(openniuma): 修复 worker 重启时进度丢失回退到 DESIGN_IMPLEMENT
- `0d21624` fix(openniuma): 防止 worktree 复用时 .env 指向主库导致测试全挂
- `d892e9c` feat(openniuma): 启动时 --model 参数一键覆盖所有 phase 模型
