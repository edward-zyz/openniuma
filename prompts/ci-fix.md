<!-- Phase: CI_FIX -->
<!-- 运行时变量（保持原样）: {dev_branch}, {run_id} -->

# CI_FIX：修复 release PR 的 CI

## ⚠️ 关键约束：所有 git 操作只能在当前 worktree 目录执行
- **禁止 `cd` 到主仓库目录再执行 git 命令**（会破坏主仓库分支状态）
- **禁止 `git -C <主仓库路径> checkout ...`**（同上）

读取 state.json 获取 `release_pr_number`、`batch_branch` 和 `batch_status`。

1. `gh pr view <release_pr_number>` 确认发布 PR 仍存在
2. `gh run list --branch <batch_branch> --limit 1` 检查发布 PR 对应的 CI
3. 已通过：
   - `batch_status = "released"`
   - queue 中所有 `done_in_dev` 任务更新为 `released`
   - `current_phase = "AWAITING_HUMAN_REVIEW"`
4. 失败：
   - `gh run view {run_id} --log-failed`
   - 在冻结的 `batch_branch` 上直接修复并 push
   - 保持 `batch_status = "releasing"`
5. 最多 3 轮，仍失败则：
   - `batch_status = "failed_release"`
   - `current_phase = "AWAITING_HUMAN_REVIEW"`
   - blocked 记录原因

禁止 gh pr merge。
全程自主工作，不要问我问题。
