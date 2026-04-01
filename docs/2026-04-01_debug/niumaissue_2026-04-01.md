# openNiuMa Issue 追踪 — 2026-04-01

运行时目录拆分（`openniuma/` → `.openniuma-runtime/`）后的遗留问题，以及之前 bugfix 改动丢失的回归问题。

## 第一轮：运行时拆分遗漏

### BUG-00: 运行时目录拆分改动全部丢失 [已修复]
**严重性:** 致命 — 8 个文件引入 `RUNTIME_DIR="${REPO_DIR}/.openniuma-runtime"`

### BUG-01: status.py 进度条 `or` → `+` [已修复]
**文件:** `lib/status.py:367` — 修复前 61% → 修复后 76%

### BUG-02: dev-loop.sh git 操作硬编码 `openniuma/tasks/` [已修复]
**文件:** `dev-loop.sh` 三处 — 改为 `.openniuma-runtime/`

### BUG-03: desc_path 硬编码 [已修复]
**文件:** `dev-loop.sh:645` — 改为 `f"{tasks_dir}/{f.name}"`

### BUG-04: init.sh .gitignore 规则未更新 [已修复]
简化为 `.openniuma-runtime/`、`openniuma/.cache/`、`openniuma/.env`、`.trees/`

### BUG-05: worktree hint 注释引用旧路径 [已修复]

### OPT-01: stats.py `total_tasks: 0` [已修复]
改为从 sessions 中 distinct task_id 计算

## 第二轮：回归 Bug（之前 bugfix 改动丢失）

### BUG-06: cancelled 依赖阻塞 [已修复]
**文件:** `lib/state.py:214` — `ready_statuses` 加入 `cancelled`

### BUG-07: reconcile 孤儿回收不检查 worker state [已修复]
**文件:** `lib/reconcile.py:133` — reclaim 前检查 `is_worker_state_done()`

### BUG-08: git worktree add 无并发重试 [已修复]
**文件:** `dev-loop.sh:233` — 3 次重试 + 指数退避

### OPT-02: 已完成任务的 worker 目录不清理 [已修复]
- `cleanup_stale_worktrees` 改为只保护有活跃进程的 worker
- parallel_main 每轮循环清理已完成任务的 worker 目录
