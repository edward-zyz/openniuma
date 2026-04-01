<!-- Phase: DESIGN_IMPLEMENT -->
<!-- 配置变量: {{main_branch}}, {{gate_command}}, {{common_rules}}, {{feat_branch_prefix}}, {{spec_dir}} -->
<!-- 运行时变量: {slug}, {dev_branch}, {date}, {task_id} -->

# DESIGN_IMPLEMENT：中复杂度合并模式

你同时是架构师和开发者。读取 state.json 获取当前任务，读取 desc_path 文件获取需求。

## 复杂度确认（先做这步！）
快速浏览需求 + ≤5 个相关文件：
- **低**（≤5 文件改动，无 DB migration，无新 API）→ current_phase="FAST_TRACK"，结束
- **高**（DB migration + 新 API + 新页面，或 >20 行需求）→ current_phase="DESIGN"，结束
- **中** → 继续

**严禁 `git checkout {dev_branch}`** — 该分支已被主仓库占用，worktree 里执行必然失败。
当前 worktree 已是 detached HEAD 指向 {dev_branch} 最新 commit，只需创建功能分支。

## 工作流程
1. 探索相关代码（5-10 个文件）
2. 写精简 spec（{{spec_dir}}/{date}-task{task_id}-{slug}-design.md）：
   - 核心设计决策 + 为什么、API/DB 变更（如有）、关键文件变更列表
   - **不写 plan 文件**（直接在 spec 中列出实现步骤）
3. 创建功能分支：`git checkout -b {{feat_branch_prefix}}/{slug}`
   - 分支已存在（断点续传）：`git checkout {{feat_branch_prefix}}/{slug}`
4. **立即**更新 state.json: branch + spec_path
5. TDD 实现：按 spec 步骤逐个完成，每步：门禁 → commit → 更新 implement_progress
6. 全部完成后 push，更新 current_phase = "VERIFY"

## 断点续传
- branch 字段非空且分支已存在 → 从已有分支继续
- 查看 implement_progress 判断已完成的 Task，从下一个开始

{{common_rules}}
