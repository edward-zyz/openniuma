<!-- Phase: RELEASE_PREP (also handles legacy FINALIZE) -->
<!-- 配置变量: {{main_branch}}, {{common_rules}} -->

# RELEASE_PREP：冻结并检查批次发布候选

读取 state.json，确认 `batch_branch`、`batch_status`、`queue`、`release_pr_number`。

## 约束
- 只处理批次发布，不处理单任务 feat 分支
- 禁止创建或操作 feat → master PR
- 只允许修改：`batch_status`、`current_phase`、`release_started_at`

## 工作流程
1. 检查 `batch_branch` 非空，`batch_status` 为 `frozen` 或 `failed_release`
   - `batch_branch` 为空（legacy FINALIZE）→ current_phase = "AWAITING_HUMAN_REVIEW"，`"_error": "legacy finalize without batch context"`
2. 检查 queue 中是否仍有 `pending` / `in_progress` 任务
   - 若有 → current_phase = "AWAITING_HUMAN_REVIEW"，`"_error": "batch still has unfinished tasks"`
3. 检查是否存在 `done_in_dev` 任务
   - 若没有 → current_phase = "AWAITING_HUMAN_REVIEW"，`"_error": "nothing to release"`
4. 记录 `release_started_at = 当前 UTC ISO 时间`
5. 设 `batch_status = "releasing"`，`current_phase = "RELEASE"`

{{common_rules}}
