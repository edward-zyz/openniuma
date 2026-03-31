# openNiuMa Worker 状态重置 Bug 复盘

> 2026-03-28，共修复 5 个 bug，涉及 4 次误诊，耗时约 2 小时

## 症状

Worker 任务已经推进到 VERIFY / MERGE / FINALIZE 阶段，但每次重启 dev-loop 后全部回退到 DESIGN_IMPLEMENT，已完成的代码被重新实现。

## 根因链（按发现顺序）

### Bug 1: FINALIZE prompt 写入错误 phase
- **位置**: `prompts/finalize.md`
- **现象**: task 6 已完成 MERGE，FINALIZE session 运行后 state.json 变成 `DESIGN_IMPLEMENT`
- **根因**: FINALIZE prompt 没有约束 state.json 的写入范围，Claude 在遇到错误时自行决定回退到 DESIGN_IMPLEMENT
- **修复**: prompt 增加严格写入约束（只允许 CI_FIX / AWAITING_HUMAN_REVIEW），dev-loop.sh 增加 phase 回退防护
- **commit**: `5a7adf4`

### Bug 2: VERIFY 产出校验误回滚
- **位置**: `dev-loop.sh` 第 1297-1330 行
- **现象**: task 7 的 DESIGN_IMPLEMENT→VERIFY 成功后 6 秒内被回滚到 DESIGN_IMPLEMENT
- **根因**: 每轮循环开头 `current_wt_path=""` 被清空，VERIFY 产出校验用主仓库路径做 git check，在某些时序下无法读到 worktree 内的 feat 分支 commit → 误判"无新产出"→ 回滚
- **修复**: 从 `state.json.worktree_path` 读取实际 worktree 路径作为 git_dir
- **commit**: `612947c`

### Bug 3: create_worker_state 无条件重建
- **位置**: `dev-loop.sh` `create_worker_state()` 函数
- **现象**: 每次 `--single-task` 启动时 worker state.json 被覆盖为全新的 DESIGN_IMPLEMENT
- **根因**: 函数无条件从主 state 提取任务并创建新 state，不检查已有文件
- **修复**: 如果 worker state.json 已存在且 `current_item_id` 匹配，跳过重建
- **commit**: `45158a9`
- **⚠️ 这个修复不够**: 见 Bug 5

### Bug 4: parallel_main 和 worker 回收的 rm -rf
- **位置**: `dev-loop.sh` `parallel_main()` 第 1032 行 + worker 回收第 1091 行
- **现象**: 即使 Bug 3 已修复，重启后 state 仍被重置
- **根因**:
  - `parallel_main()` 启动时 `rm -rf $WORKERS_DIR` 删除整个 workers 目录（包括所有 state.json）
  - worker 异常退出后回收逻辑也 `rm -rf` 整个 worker 目录
  - Bug 3 的保护在文件被删后自然无效
- **修复**:
  - 启动时只删 pid/name 文件，保留 state.json
  - 回收时只在 exit_code=0（任务完成）时删整个目录
- **commit**: `8927acd`
- **⚠️ 这个修复也不够**: 见 Bug 5

### Bug 5: sync_worker_result + 主 state 未同步（真正的根因）
- **位置**: `dev-loop.sh` `sync_worker_result()` + 主 `state.json`
- **现象**: Bug 3 和 Bug 4 都修了，task 6 仍然被重新 spawn
- **根因链**:
  1. `sync_worker_result` 中 `is_done AND worker_exit==0` 条件过严 — worker 被 kill 时 exit!=0，即使 phase=AWAITING_HUMAN_REVIEW 也被重置为 `pending`
  2. 主 state.json 中 task 6 始终为 `in_progress` → spawn 循环持续为已完成任务创建 worker
  3. `create_worker_state` 的 item_id 保护因 worker state 的 `current_item_id=None`（手动设的完成状态）不匹配 task_id=6 而失效
  4. 三层保护全部被绕过 → 创建全新 DESIGN_IMPLEMENT state
- **修复**: `is_done` 为真时直接标记 done，不再要求 exit_code==0
- **commit**: `2541a7f`

## 为什么前 4 次修复都没用

```
修复1: 改 FINALIZE prompt → 但 state 已经坏了，prompt 修复是未来预防
修复2: 改 VERIFY git 路径 → 对 task 6 的问题无关
修复3: 改 create_worker_state 跳过重建 → 但 rm -rf 先把文件删了
修复4: 改 rm -rf 为只删 pid → 但主 state 仍为 in_progress，spawn 循环仍触发
修复5: 改 sync_worker_result + 主 state → ✅ 终于切断了 spawn 循环的源头
```

每次修复都修了一个真实的 bug，但没有修到最上游的 bug。根本原因是 **主 state.json 中的任务状态** 才是 spawn 循环的决策源，worker state 只是执行层的状态。

## 架构教训

### 双层 state 的一致性问题

```
主 state.json (调度层)          worker state.json (执行层)
  queue[].status = in_progress    current_phase = AWAITING_HUMAN_REVIEW
  ↓                                ↓
  spawn 循环: "需要 worker!"       worker: "我已完成"
  ↓
  create_worker_state → 覆盖执行层状态
```

主 state 和 worker state 是两个独立的状态源，没有双向同步机制。当 worker 被信号杀死时：
- worker state 保留了最终状态（如 AWAITING_HUMAN_REVIEW）
- 但主 state 不知道，仍认为任务在进行中
- `sync_worker_result` 本应同步，但 `exit_code != 0` 的条件阻止了同步

### 防御性设计原则

1. **状态写入必须是幂等的** — `create_worker_state` 不应无条件覆盖
2. **删除操作必须精确** — `rm -rf` 整个目录是最危险的操作，应只删必要文件
3. **完成状态不可回退** — 一旦标记为 done/AWAITING_HUMAN_REVIEW，不应因信号/重启而回退
4. **主状态是权威源** — 所有 spawn 决策基于主 state，worker state 只是执行细节

## 最终防护矩阵

| 层 | 防护点 | 作用 |
|----|--------|------|
| 主 state.json | `status=done` | spawn 循环不选它 |
| parallel_main | 只删 pid/name | 保留 worker state |
| worker 回收 | exit=0 才删目录 | 异常退出保留 state |
| create_worker_state | item_id 匹配跳过 | 不覆盖已有进度 |
| sync_worker_result | is_done 即标记 done | kill 后不回退 |
| FINALIZE prompt | 禁写 DESIGN_IMPLEMENT | Claude 不误写 |
| dev-loop phase 防护 | FINALIZE/MERGE 后不回退 | 代码级兜底 |

### Bug 6: create_worker_state 的 log 污染 STATE_FILE（修复引入的 bug）
- **位置**: `dev-loop.sh` `create_worker_state()` 第 754 行
- **现象**: worker 7/8 陷入无限 INIT 循环
- **根因**: Bug 3 的修复中加了 `log "...跳过重建"` 调用，但 `log` 写 stdout → 被 `worker_state_file=$(create_worker_state ...)` 的 `$()` 捕获 → `STATE_FILE` 变成 `"日志消息\n文件路径"` → 所有内联 python 脚本 SyntaxError → 读不到 worker state → 回退到主 state → phase=INIT → 无限循环
- **修复**: `log ... >&2` 重定向到 stderr
- **commit**: `33f851c`
- **教训**: 在通过 `echo` 返回值的函数中，任何 stdout 输出都会污染返回值。`log` 在 `$()` 内必须走 stderr。

## 相关 Commits

| Commit | 描述 |
|--------|------|
| `612947c` | fix: VERIFY 产出校验误回滚 |
| `45158a9` | fix: create_worker_state 重启覆盖 |
| `5a7adf4` | fix: FINALIZE 错误写入 DESIGN_IMPLEMENT |
| `8927acd` | fix: rm -rf 删除 worker state |
| `2541a7f` | fix: sync_worker_result 已完成任务被重置 |
