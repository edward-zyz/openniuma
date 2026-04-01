<!-- Phase: RELEASE -->
<!-- 配置变量（构建时替换）: {{main_branch}} -->
<!-- 运行时变量（保持原样）: {dev_branch} -->

# RELEASE：为当前冻结批次创建 release PR

读取 state.json，获取 `batch_branch`、`release_pr_number`、`batch_status`。

## 约束
- 所有 git 操作只在当前 worktree 目录执行
- 禁止创建 feat PR
- 只允许创建 `batch_branch -> {{main_branch}}` 的发布 PR
- 只允许修改：`release_pr_number`、`current_phase`、`batch_status`

## 工作流程
1. 确认 `batch_branch` 非空，且 `batch_status = "releasing"`
2. `git fetch origin {{main_branch}}`
3. `git fetch origin {dev_branch}`（若 state 中 `batch_branch` 与 `dev_branch` 不同，以 `batch_branch` 为准）
4. 创建或查询现有发布 PR：
   - `gh pr create --base {{main_branch}} --head <batch_branch> --title "Release <batch_branch>"`
   - 若已存在，读取现有 PR 编号
5. 将 PR 编号写入 `release_pr_number`
6. 设 `current_phase = "CI_FIX"`

若无法创建 PR：
- 设 `batch_status = "failed_release"`
- 设 `current_phase = "AWAITING_HUMAN_REVIEW"`
- 补充 `"_error": "release pr create failed"`

全程自主工作，不要问我问题。
