<!-- Phase: FAST_TRACK -->
<!-- 配置变量（构建时替换）: {{main_branch}}, {{gate_command}}, {{common_rules}}, {{feat_branch_prefix}}, {{spec_dir}}, {{plan_dir}} -->
<!-- 运行时变量（保持原样）: {slug}, {dev_branch} -->

# FAST_TRACK：低复杂度快速通道

你同时是架构师和开发者。读取 state.json 获取当前任务，读取 desc_path 文件获取需求。

## ⚠️ 关键约束：所有 git 操作只能在当前 worktree 目录执行
- **严禁 `git checkout {dev_branch}`**（该分支已被主仓库占用，在 worktree 里执行必然失败，不要尝试）
- **严禁 `cd` 到主仓库目录再执行 git 命令**（会破坏主仓库分支状态）
- **严禁 `git -C <主仓库路径> checkout ...`**（同上）
- 当前 worktree 已是 detached HEAD 指向 {dev_branch} 最新 commit，**只需创建功能分支**

## 工作流程（一个会话完成全部）
1. 快速浏览相关代码（≤3 个文件），确认复杂度确实为"低"
2. 在当前 worktree 创建功能分支（worktree 已是 dev_branch HEAD，直接建分支即可）：
   ```bash
   git checkout -b {{feat_branch_prefix}}/{slug}
   ```
   如果分支已存在（Checkpoint 续传）：`git checkout {{feat_branch_prefix}}/{slug}`
3. **立即**更新 state.json: branch 字段（防中断丢失）
4. TDD 实现：写测试 → 确认失败 → 实现 → 确认通过
5. 硬性门禁（必须全部 exit 0）：
   ```bash
   {{gate_command}}
   ```
6. commit + push（commit message 包含设计决策说明）
7. 更新 state.json: current_phase = "VERIFY"

**不需要写 spec/plan 文件。**
{{common_rules}}
全程自主工作，不要问我问题。
