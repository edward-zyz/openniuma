# openNiuMa Issue 追踪 — 2026-04-01

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

### BUG-05: worktree hint 注释引用旧路径 [已修复]

### OPT-01: stats.py `total_tasks: 0` [已修复]

## 第二轮：回归 Bug

### BUG-06: cancelled 依赖阻塞 [已修复]
**文件:** `lib/state.py:214` — `ready_statuses` 加入 `cancelled`

### BUG-07: reconcile 孤儿回收不检查 worker state [已修复]
**文件:** `lib/reconcile.py:133`

### BUG-08: git worktree add 无并发重试 [已修复]
**文件:** `dev-loop.sh:233` — 3 次重试 + 指数退避

### OPT-02: 已完成任务的 worker 目录不清理 [已修复]

## 第三轮：日志分析发现的 Bug

### BUG-09: single-task worker 启动时误清理其他 worker 的 worktree [已修复]

**严重性:** 高
**文件:** `dev-loop.sh:1312`

**根因链:**
1. Worker #37 启动时执行 `cleanup_stale_worktrees`
2. 误判 worker #42 的 `loop-test-05` worktree 为"残留"（slug 保护逻辑在部分场景下失效）
3. 删除了正在运行的 worker #42 的工作目录
4. Worker #42 的 Claude 会话发现 worktree 不存在，无法继续
5. 误将 `current_phase` 写为 `INIT`
6. Worker 进入 INIT 循环，3 次失败后停止

**证据:**
```
worker-37.log: [15:08:46] 🧹 清理残留 worktree: loop-test-05  ← 误杀
worker-42.log: Worktree loop-test-05 已被删除，无法继续工作
worker-42.log: Phase 推进: FAST_TRACK → INIT  ← 错误的 phase 转换
worker-42.log: ❌ 连续 3 次 Phase 未推进，停止循环
```

**修复:** `cleanup_stale_worktrees` 仅在 orchestrator/串行模式下执行，single-task worker 跳过。

### BUG-10: Worker 可进入 INIT phase 导致全局污染 [已修复]

**严重性:** 高
**文件:** `dev-loop.sh` 主循环

当 Claude session 误写 `current_phase = "INIT"` 到 worker state 时，worker 会执行 INIT（批次初始化），这是全局操作，不应由 task worker 执行。

**修复:** 在主循环中加守卫：如果 `SINGLE_TASK` 且 phase=INIT，自动重置为 DESIGN_IMPLEMENT。

### OPT-03: Python "Bad file descriptor" 错误 [观察]

**位置:** worker-42.log（10 次）
**严重性:** 低

`Fatal Python error: init_sys_streams: can't initialize sys standard streams / OSError: [Errno 9] Bad file descriptor`

原因是 `exec 3>&-` 关闭 fd3 后影响了子进程的 fd 继承。仅在 worktree 创建重试多次时出现，不影响核心功能。
