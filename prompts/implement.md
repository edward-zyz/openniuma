<!-- Phase: IMPLEMENT -->
<!-- 配置变量: {{main_branch}}, {{gate_command}}, {{common_rules}}, {{feat_branch_prefix}} -->
<!-- 运行时变量: {slug}, {dev_branch} -->

# IMPLEMENT：按 plan 实现

你是开发者。读取 state.json 获取 plan_path，严格按 plan 实现。

**严禁 `git checkout {dev_branch}`** — 该分支已被主仓库占用，worktree 里执行必然失败。
当前 worktree 已是 detached HEAD 指向 {dev_branch} 最新 commit，只需创建功能分支。

## 工作流程
1. 创建功能分支：`git checkout -b {{feat_branch_prefix}}/{slug}`
   - branch 字段已有且分支已存在（断点续传）：`git checkout {{feat_branch_prefix}}/{slug}`
2. 立即更新 state.json: branch 字段
3. 按 plan 的 Chunk → Task 顺序 TDD 实现
4. 每完成一个 Task：门禁 → commit → 更新 implement_progress
5. 全部完成后 push，更新 current_phase = "VERIFY"

## 断点续传
- branch 字段非空且分支已存在 → 从已有分支继续
- 查看 implement_progress 判断已完成的 Task，从下一个开始

{{common_rules}}
