# SPEC: 测试任务17 — CI 失败处理

## 1. 概述

**任务：** 验证 CI 阶段失败时正确触发 CI_FIX 循环的集成测试。
**文件：** `openniuma/lib/test_ci_failure.py`

## 2. 核心设计决策

- 遵循 `test_merge_conflict.py` 建立的测试模式：纯状态机验证 + failure 分类测试 + Phase 流转测试
- 不使用真实 GitHub API mock，使用模拟 log 输出分类 + 状态机断言
- 所有测试在临时目录运行，不依赖真实数据

## 3. 测试覆盖

### 3.1 Gate 失败分类（5 tests）
验证 CI 失败日志被正确分类为 `FailureType.GATE`：
- `npm ERR! code ELIFECYCLE` → GATE
- `eslint found 3 error(s)` → GATE
- `error TS2304` → GATE
- `FAIL src/utils/keywords.test.ts` → GATE
- `npm run lint exit code 1` → GATE

### 3.2 Gate 重试策略（3 tests）
验证 retry.py 的 gate 类型重试行为：
- `compute_delay(GATE, 1)` ≈ 20s（base=10, 2^0=1）
- `compute_delay(GATE, 2)` ≈ 40s（base=10, 2^1=2）
- `should_retry(GATE, 1, max=3)` = True，`should_retry(GATE, 4, max=3)` = False

### 3.3 Phase 流转（4 tests）
验证状态机支持完整 CI 失败 → CI_FIX → 完成流程：
- `RELEASE` 阶段后 `current_phase = "CI_FIX"`
- `CI_FIX` 阶段识别 CI 失败（log 分类为 GATE）
- `CI_FIX` 阶段识别 CI 通过
- 最多 3 轮重试后标记 `failed_release`

### 3.4 workflow.yaml 配置验证（1 test）
验证 `failure.max_retries_gate = 3` 已配置

### 3.5 真实 CI 输出模拟（2 tests）
验证 CI 日志尾部含 GATE 信号时正确分类：
- gate command 失败日志（lint + test + build）
- 长日志中尾部 CI 结果覆盖前面内容

## 4. 实现步骤

1. 创建 `openniuma/lib/test_ci_failure.py`
2. 实现所有测试类
3. 运行 `python -m pytest openniuma/lib/test_ci_failure.py -v` 验证
4. Commit
