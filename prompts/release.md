<!-- Phase: RELEASE -->
<!-- 配置变量: {{main_branch}}, {{common_rules}} -->
<!-- 运行时变量: {dev_branch} -->

# RELEASE：为当前冻结批次创建 release PR

读取 state.json，获取 `batch_branch`、`release_pr_number`、`batch_status`。

## 约束
- 禁止创建 feat PR
- 只允许创建 `batch_branch -> {{main_branch}}` 的发布 PR
- 只允许修改：`release_pr_number`、`current_phase`、`batch_status`

## 工作流程
1. 确认 `batch_branch` 非空，且 `batch_status = "releasing"`
2. `git fetch origin {{main_branch}}` 和 `git fetch origin {dev_branch}`
3. 创建或查询现有发布 PR：
   - `gh pr create --base {{main_branch}} --head <batch_branch> --title "Release <batch_branch>"`
   - 若已存在，读取现有 PR 编号
4. 将 PR 编号写入 `release_pr_number`
5. 设 `current_phase = "CI_FIX"`

若无法创建 PR：
- `batch_status = "failed_release"`，`current_phase = "AWAITING_HUMAN_REVIEW"`
- `"_error": "release pr create failed"`

{{common_rules}}
