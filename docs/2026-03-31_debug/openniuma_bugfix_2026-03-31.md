# Bug Fix 日志 — 2026-03-31

## 修复的 Bug

### 1. Worker 退出时的 break 语句异常

**文件**: `openniuma/dev-loop.sh`

**问题**: Worker 在 single-task 模式下，`worker_task_done()` 函数使用 `sys.exit(0/1)` 来返回布尔值。当 `is_worker_state_done` 返回 `True` 时，`sys.exit(0)` 触发 Python 子进程以退出码 0 退出。但这可能导致子 shell 退出码混淆，在某些边缘情况下导致 "break: only meaningful in a for, while, or until loop" 错误。

**修复**: 改用 `&& return 0 || return 1` 模式，直接通过函数返回码传递布尔值。

---

### 2. Single-task 模式 sync_worker_result 不被调用

**文件**: `openniuma/dev-loop.sh`

**问题**: Worker 在 single-task 模式下完成任务后直接 `break` 退出，没有调用 `sync_worker_result` 同步结果到主 state。

**修复**: 在两处退出路径前添加 `sync_worker_result` 调用。

---

### 3. 孤儿任务回收逻辑不完善

**文件**: `openniuma/lib/reconcile.py`

**问题**: `reclaim_orphan_tasks()` 只检查 worker 进程是否还活着，没有检查任务是否真的未完成。

**修复**: 增加检查 worker state 中的任务状态，只有当任务真的未完成时才 reclaim。

---

### 4. parallel_main 中的孤儿回收逻辑

**文件**: `openniuma/dev-loop.sh`

**问题**: 与 reconcile.py 类似，parallel_main 中的孤儿回收只检查进程是否活着。

**修复**: 使用 Python heredoc 重写孤儿回收逻辑，增加 worker state 检查。

---

### 5. git worktree add 并发竞争

**文件**: `openniuma/dev-loop.sh`

**问题**: 多个 worker 并发创建 worktree 时，`git worktree add` 可能因为竞争条件失败。

**修复**: 增加 3 次重试 + 竞争条件检测。

---

### 6. 任务 #12 因 cancelled 依赖无法执行

**文件**: `openniuma/lib/state.py`

**问题**: 任务 #12 依赖任务 #4，但 #4 状态为 `cancelled`，不满足 `find_ready_tasks` 的依赖检查条件（`cancelled` 不在 `ready_statuses` 中），导致 #12 永远无法执行。

**修复**: 将 `cancelled` 加入 `ready_statuses`，使被取消任务的依赖也被视为满足：

```python
ready_statuses = {"done", "done_in_dev", "released", "cancelled"}
```

**验证**: 修改后 #12、#36、#37 均变为 ready 状态。

---

## 观察到的其他问题（未修复）

### 1. Terminated: 15 信号
- **现象**: 21 次 "Terminated: 15" 日志
- **原因**: 用户主动发送 SIGTERM 信号终止 worker，非 bug

### 2. nodemon@3.1.14 版本不存在
- **现象**: 临时 npm registry 错误
- **状态**: nodemon@3.1.14 实际存在

### 3. API Error: UNKNOWN_CERTIFICATE_VERIFICATION_ERROR
- **现象**: 偶发证书验证错误
- **状态**: 已通过 retry 机制处理
