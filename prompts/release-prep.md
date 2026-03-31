<!-- Phase: RELEASE_PREP -->
<!-- 配置变量（构建时替换）: {{main_branch}} -->

# RELEASE_PREP：冻结并检查批次发布候选

读取 state.json，确认当前 `batch_branch`、`batch_status`、`queue`、`release_pr_number`。

## 目标
- 当前批次必须是一个冻结的发布候选
- 只处理批次发布，不处理单任务 feat 分支
- 若仍存在未完成任务，不允许继续发布

## 约束
- 所有 git 操作只在当前 worktree 目录执行
- 禁止创建或操作 feat → master PR
- 只允许修改批次级字段：`batch_status`、`current_phase`、`release_started_at`

## 工作流程
1. 检查 `batch_branch` 非空，`batch_status` 为 `frozen` 或 `failed_release`
2. 检查 queue 中是否仍有 `pending` / `in_progress` 任务
   - 若有：设 `current_phase = "AWAITING_HUMAN_REVIEW"`，补充 `"_error": "batch still has unfinished tasks"`
3. 检查是否存在 `done_in_dev` 任务
   - 若没有：设 `current_phase = "AWAITING_HUMAN_REVIEW"`，补充 `"_error": "nothing to release"`
4. 记录 `release_started_at = 当前 UTC ISO 时间`
5. 设 `batch_status = "releasing"`，`current_phase = "RELEASE"`

全程自主工作，不要问我问题。
