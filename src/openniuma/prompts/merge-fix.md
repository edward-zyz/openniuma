<!-- Phase: MERGE_FIX -->
<!-- 配置变量（构建时替换）: {{gate_command}} -->

# MERGE_FIX：修复任务进入 active batch 前的集成失败

读取 state.json 的 fix_list_path 获取 CI 失败日志。

## ⚠️ 关键约束：所有 git 操作只能在当前 worktree 目录执行
- **禁止 `cd` 到主仓库目录再执行 git 命令**（会破坏主仓库分支状态）

在功能分支上修复兼容性问题，并重新基于当前 active batch 验证：
- 先 `git fetch origin {dev_branch}`
- 在功能分支修复后重新跑门禁
- 更新 `current_phase = "MERGE"`

如果 `batch_status` 已不是 `active`：
- 不要再把该任务塞回旧 batch
- 设 `current_phase = "AWAITING_HUMAN_REVIEW"` 并补充 `"_error": "batch already frozen"`
门禁：
```bash
{{gate_command}}
```
全程自主工作，不要问我问题。
