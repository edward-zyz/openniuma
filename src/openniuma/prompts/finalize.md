<!-- Phase: FINALIZE -->

# FINALIZE：兼容旧状态的批次发布入口

该阶段已废弃为旧别名。

读取 state.json：
- 若存在 `batch_branch`，将 `current_phase` 更新为 `"RELEASE_PREP"`
- 若不存在 `batch_branch`，设 `current_phase = "AWAITING_HUMAN_REVIEW"` 并补充 `"_error": "legacy finalize without batch context"`

禁止创建 feat → master PR。
