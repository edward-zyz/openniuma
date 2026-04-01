<!-- Phase: FAST_TRACK -->
<!-- 配置变量: {{main_branch}}, {{gate_command}}, {{common_rules}}, {{feat_branch_prefix}} -->
<!-- 运行时变量: {slug}, {dev_branch} -->

# FAST_TRACK：低复杂度快速通道

你同时是架构师和开发者。读取 state.json 获取当前任务，读取 desc_path 文件获取需求。

**严禁 `git checkout {dev_branch}`** — 该分支已被主仓库占用，worktree 里执行必然失败。
当前 worktree 已是 detached HEAD 指向 {dev_branch} 最新 commit，只需创建功能分支。

## 工作流程（一个会话完成全部）
1. 快速浏览相关代码（≤3 个文件），确认复杂度确实为"低"
2. 创建功能分支：`git checkout -b {{feat_branch_prefix}}/{slug}`
   - 分支已存在（断点续传）：`git checkout {{feat_branch_prefix}}/{slug}`
3. **立即**更新 state.json: branch 字段（防中断丢失）
4. TDD 实现：写测试 → 确认失败 → 实现 → 确认通过
5. 门禁通过 → commit + push（commit message 包含设计决策说明）
6. 更新 state.json: current_phase = "VERIFY"

**不需要写 spec/plan 文件。**

{{common_rules}}
