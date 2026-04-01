<!-- Phase: MERGE_FIX -->
<!-- 配置变量: {{gate_command}}, {{common_rules}} -->

# MERGE_FIX：修复任务进入 active batch 前的集成失败

读取 state.json 的 fix_list_path 获取 CI 失败日志。

在功能分支上修复兼容性问题，并重新基于当前 active batch 验证：
- 先 `git fetch origin {dev_branch}`
- 在功能分支修复后重新跑门禁
- 更新 `current_phase = "MERGE"`

如果 `batch_status` 已不是 `active`：
- 设 `current_phase = "AWAITING_HUMAN_REVIEW"` 并补充 `"_error": "batch already frozen"`

{{common_rules}}
