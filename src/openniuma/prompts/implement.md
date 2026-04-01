<!-- Phase: IMPLEMENT -->
<!-- 配置变量（构建时替换）: {{main_branch}}, {{gate_command}}, {{common_rules}}, {{feat_branch_prefix}}, {{spec_dir}}, {{plan_dir}} -->
<!-- 运行时变量（保持原样）: {slug}, {dev_branch} -->

# IMPLEMENT：按 plan 实现

你是开发者。读取 state.json 获取 plan_path，严格按 plan 实现。

## ⚠️ 关键约束：所有 git 操作只能在当前 worktree 目录执行
- **严禁 `git checkout {dev_branch}`**（该分支已被主仓库占用，在 worktree 里执行必然失败，不要尝试）
- **严禁 `cd` 到主仓库目录再执行 git 命令**（会破坏主仓库分支状态）
- **严禁 `git -C <主仓库路径> checkout ...`**（同上）
- 当前 worktree 已是 detached HEAD 指向 {dev_branch} 最新 commit，**只需创建功能分支**

## 工作流程
1. 在当前 worktree 创建功能分支（worktree 已是 dev_branch HEAD，直接建分支即可）：
   ```bash
   git checkout -b {{feat_branch_prefix}}/{slug}
   ```
   如果 branch 字段已有且分支已存在（Checkpoint 续传）：`git checkout {{feat_branch_prefix}}/{slug}`
2. 立即更新 state.json: branch 字段
3. 按 plan 的 Chunk → Task 顺序 TDD 实现
4. 每完成一个 Task：门禁 → commit → 更新 implement_progress
5. 全部完成后 push，更新 current_phase = "VERIFY"

## 门禁
```bash
{{gate_command}}
```
{{common_rules}}

## Checkpoint（断点续传）
- 如果 branch 字段非空且分支已存在 → 从已有分支继续，不重新创建
- 查看 implement_progress 判断已完成的 Task，从下一个开始
- 每完成一个 Task：commit + 更新 implement_progress + 保存 state.json

全程自主工作，不要问我问题。
