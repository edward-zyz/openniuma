# Spec: 测试任务16 — 合并冲突处理验证

## 背景

openNiuMa dev-loop 在 MERGE 阶段执行 `git merge feat/{slug}` 合入功能分支。当两个分支修改了同一文件时，git 会报合并冲突，导致合并失败。系统需要：

1. 正确检测冲突（CONFLICT 分类）
2. 自动触发 MERGE_FIX 阶段
3. 解决冲突后继续流程

## 核心设计

### 失败分类（failure.py）

`failure.py` 的 `classify()` 函数通过模式匹配对 CI/merge 输出进行分类。CONFLICT 类型的匹配规则：

```python
FailureType.CONFLICT: {
    "keywords": [
        r"CONFLICT\s+\(",
        r"merge conflict",
        r"Merge conflict in",
    ],
    "excludes": [r'["\']CONFLICT', r"#.*CONFLICT", r"//.*CONFLICT"],
}
```

优先级：NETWORK > CONTEXT > PERMISSION > CONFLICT > GATE > UNKNOWN。

### 重试策略（retry.py）

CONFLICT 类型的重试延迟为 10s + 小抖动（0~5s），最大重试次数由 `workflow.yaml` 的 `failure.max_retries_conflict: 2` 控制。

### MERGE → MERGE_FIX 流程

```
MERGE 阶段
  ├── git merge feat/{slug}
  ├── CI 门禁通过 → current_phase = "MERGE" 完成
  └── CI 门禁失败 + 分类为 CONFLICT
        → current_phase = "MERGE_FIX"
        → 记录失败日志到 fix_list_path
        → 在 merge-fix.md prompt 指引下修复冲突
        → 重新跑门禁 → 继续到 MERGE 完成
```

### merge-fix.md prompt

提供冲突解决的指令指引，包括 fetch 最新 batch 分支、在功能分支修复、重新验证门禁等步骤。

## 实现步骤

1. **编写 `test_merge_conflict.py`**：覆盖合并冲突检测的端到端测试
   - 多种 git 合并冲突输出格式的分类验证
   - CONFLICT 优先级验证（高于 GATE）
   - CONFLICT 重试延迟验证
   - phase 流转：MERGE → MERGE_FIX → MERGE 完成

2. **门禁检查**：所有测试通过

## 测试文件

`openniuma/lib/test_merge_conflict.py`：新增集成测试文件

## 验收标准

- [ ] CONFLICT 类型分类：多种 git 冲突输出格式（`CONFLICT (content):`、`merge conflict`、`Merge conflict in`）均被正确分类为 CONFLICT
- [ ] 排除规则：字符串字面量中的 CONFLICT 不触发
- [ ] 优先级：CONFLICT 优先级高于 GATE（当同时出现时）
- [ ] 重试延迟：CONFLICT 类型重试延迟在 10~15s 范围内
- [ ] Phase 流转：模拟 MERGE 阶段遇冲突 → MERGE_FIX → 解决后完成
