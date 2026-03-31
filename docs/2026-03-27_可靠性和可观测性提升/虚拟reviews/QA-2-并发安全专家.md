# QA-2 评审：并发安全与进程管理

> **评审人**: QA-2（并发安全与进程管理专家）
> **评审对象**: openNiuMa 合并升级设计（Symphony + v4）
> **评审范围**: 文件锁与竞态、进程管理（kill/zombie/orphan）、跨语言调用可靠性
> **评审日期**: 2026-03-27

---

## 🔴 严重问题

### S-1: mkdir 锁不保护 Python→JSON 读写——Bash 和 Python 使用两套独立锁机制

**问题描述:**
现有 `dev-loop.sh` 使用 `mkdir` 实现原子锁（`lock_state` / `unlock_state`），而设计中 `lib/state.py` 将使用 Python 文件锁（`fcntl.flock` 或类似机制）保护 `loop-state.json` 的读写。两套锁机制互不感知：

- Bash 通过 `locked_state_op()` 加 mkdir 锁后调用 `python3 -c` 操作 JSON
- Python `state.py` 用自己的文件锁保护读写
- `reconcile.py`、`stats.py` 等新模块直接调用 `state.py`，走 Python 锁
- 但 `dev-loop.sh` 中未迁移的代码仍走 mkdir 锁

如果编排器（Bash mkdir 锁）和 reconcile.py（Python flock）同时操作 `loop-state.json`，两个锁不互斥，可能导致写覆盖（lost update）。

**复现场景:**
1. Worker 完成 → `parallel_main` 的 `sync_worker_result()` 通过 `locked_state_op` 获取 mkdir 锁，开始写 state
2. 同一时刻 `reconcile.py run` 被调度循环调用（设计中每轮调用），通过 `state.py` 的 Python flock 获取锁，也在写 state
3. 两者同时持有各自的锁，最后一个写入者覆盖前一个的修改

**建议修改:**
- 统一为单一锁机制。推荐方案：所有对 `loop-state.json` 的操作都通过 `state.py` 进行，Bash 侧废弃 `locked_state_op` + mkdir 锁，改为 `python3 loop/lib/state.py <operation>`
- 如果必须保留 Bash 锁，让 Python `state.py` 也使用同一个 mkdir 目录作为锁（但这会牺牲 Python 端的可测试性）
- 迁移计划中需明确标注：Phase 1 完成后，`dev-loop.sh` 中所有 `locked_state_op()` + 内联 Python 必须同步迁移为 `state.py` 调用，不允许两套锁并存

---

### S-2: `locked_state_op()` 中 Python 异常导致锁泄漏

**问题描述:**
现有代码：

```bash
locked_state_op() {
  lock_state
  python3 -c "$1"
  local rc=$?
  unlock_state
  return $rc
}
```

如果 `python3 -c "$1"` 因 `set -e` 被中止（例如 `python3` 命令自身 crash、OOM kill、被 signal 杀掉），`unlock_state` 永远不会执行。虽然有 10 秒超时强制清除，但 10 秒内所有其他 worker 的状态操作全部阻塞。

**复现场景:**
1. Worker A 调用 `locked_state_op`，Python 内存分配失败被 OOM killer 杀掉
2. `set -e` 触发，脚本在 `python3 -c` 行退出（或被 trap 捕获），跳过 `unlock_state`
3. 其他 4 个 worker 的状态操作全部在 `lock_state` 中 spin-wait 10 秒
4. 10 秒后强制清除锁，但中间 10 秒的调度完全停滞

**建议修改:**
使用 `trap` 保证解锁：

```bash
locked_state_op() {
  lock_state
  trap 'unlock_state' RETURN  # bash 4+ RETURN trap
  python3 -c "$1"
  local rc=$?
  return $rc
}
```

或使用子 shell + 背景清理：

```bash
locked_state_op() {
  lock_state
  local rc=0
  python3 -c "$1" || rc=$?
  unlock_state
  return $rc
}
```

注意第二种方案需要去掉 `set -e` 对该行的影响（已通过 `|| rc=$?` 实现）。设计文档应明确选择哪种方式。

---

### S-3: Stall 检测 kill 后可能产生 zombie 或 orphan 进程

**问题描述:**
`reconcile.py` 检测到 stall 后执行 `kill worker PID` + `pkill -P worker_PID`，但存在以下问题：

1. **进程组不完整**: `claude` CLI 可能 fork 出多层子进程（如 node → claude → subprocess），`pkill -P` 只杀直接子进程，孙进程成为 orphan
2. **kill 和 pkill 之间有窗口**: kill 父进程后子进程可能已被 reparent 到 init/PID 1，此时 `pkill -P` 找不到目标
3. **zombie 回收**: reconcile.py 是 Python 进程，不是 worker 的父进程，无法 `wait()` 回收 zombie。worker 的 zombie 只能由 `parallel_main`（bash）的 `wait` 回收，但如果 reconcile.py 先 kill 了 worker，bash 侧的 `wait "$pid"` 可能报错或获取错误的 exit code

**复现场景:**
1. Worker 55 启动 `claude -p ...`，claude 内部 fork 了 node 子进程
2. Stall 超时，reconcile.py kill worker 55 的 PID
3. Worker 55 死亡，但 claude 的 node 子进程成为 orphan，继续占用 CPU/内存
4. Bash 侧 `kill -0 "$pid"` 返回 false，`wait "$pid"` 可能失败（因为进程已被 reconcile.py 杀掉，不再是当前 shell 的子进程）

**建议修改:**
- 使用进程组（process group）而非单个 PID 管理 worker。启动 worker 时 `setsid` 或 `set -m`，kill 时 `kill -TERM -$PGID`（负号表示整个进程组）
- 或者不在 reconcile.py 中直接 kill，而是写一个 `workers/{task_id}/stalled` 标记文件，由 `parallel_main`（bash 父进程）在下一轮循环中执行 kill + wait，保证 zombie 正确回收
- 文档中增加进程管理架构图，明确谁负责 kill、谁负责 wait

---

### S-4: `stats.json` 无锁保护，多 worker 并发写入导致数据损坏

**问题描述:**
设计中 `stats.py record-session` 在每次 Claude 会话结束后被调用，而并行模式下最多 5 个 worker 同时运行。多个 worker 可能同时：
1. 读取 `stats.json`
2. 追加 session 记录
3. 写回 `stats.json`

这是经典的 read-modify-write 竞态。`loop-state.json` 有锁保护，但设计文档中未提及 `stats.json` 的并发保护。

**复现场景:**
1. Worker A 和 Worker B 几乎同时完成
2. Worker A 读取 `stats.json`（100 条记录），追加第 101 条，写回
3. Worker B 在 Worker A 写回之前也读取了旧的 `stats.json`（100 条），追加第 101 条，写回
4. Worker A 的记录被 Worker B 覆盖，数据丢失

**建议修改:**
- `stats.py` 使用与 `state.py` 相同的文件锁机制保护 `stats.json` 读写
- 或改为 append-only 日志格式（如 JSONL），每行一条记录，只追加不回写。查询时全量扫描聚合。这样 `>>` 追加操作在 POSIX 下对小写入是原子的

---

## 🟡 中等问题

### M-1: 跨语言调用的错误传播不可靠 — eval + Python stdout 的信任链脆弱

**问题描述:**
设计采用以下模式传递 Python 输出到 Bash：

```bash
eval "$(python3 loop/lib/config.py export-shell)"
```

如果 Python 脚本在 stdout 输出了非预期内容（如 Python 的异常 traceback 混入 stdout、warning 消息、PyYAML 的解析警告），`eval` 会尝试执行这些内容作为 shell 命令，可能导致不可预测行为。

现有 `dashboard.sh` 实现中也使用了 `eval "$(python3 -c "...")"` 模式（第 125-163 行），同样存在此问题。

**复现场景:**
1. PyYAML 版本升级，某个弃用功能输出 `DeprecationWarning:` 到 stdout（PyYAML 的某些版本确实会这样做）
2. `eval` 尝试执行 `DeprecationWarning: ...` 作为 shell 命令
3. 如果恰好 PATH 中有同名命令，或内容恰好是合法 shell 语法，可能产生意外副作用

**建议修改:**
- Python 脚本必须将所有 warning/error 输出到 stderr，stdout 仅输出结构化数据
- 在 `eval` 之前校验输出格式，例如检查每行是否匹配 `^[A-Z_]+=` 模式
- 或使用临时文件传递：Python 写 JSON 到临时文件 → Bash 用 `jq` 解析。避免 eval 完全可消除此攻击面
- 对 `dashboard.sh` 中的 `eval "$(python3 ...)"` 也要同步修改

---

### M-2: Stall 超时基于日志 mtime 不可靠 — 存在误杀和漏检

**问题描述:**
设计方案使用 worker 日志文件的 mtime 判断是否 stall，但存在两种失效场景：

**误杀（False Positive）:**
- Claude CLI 在长时间思考（如复杂设计阶段），stdout 无输出但进程正常。日志 mtime 不更新，30 分钟后被 reconcile 误杀
- 后台 `npm install` 或 `npm test` 运行时间长，输出不经过 claude 日志路径

**漏检（False Negative）:**
- 进程死锁但仍在输出心跳日志（不太常见但可能）
- Worker 进程已死但日志文件被其他进程写入（如 tee 子进程残留）

**建议修改:**
- 增加多维检测：mtime + `kill -0 $PID`（进程存活检查）+ `/proc/$PID/status`（或 macOS 的 `ps -p $PID`）
- 对 stall 检测增加宽限期机制：首次检测到 mtime 超时后发 SIGUSR1 给 worker，等待 60s；仍无响应才 kill
- stall_timeout_sec 应根据 phase 差异化配置（DESIGN 阶段允许更长思考时间，MERGE 阶段超时应更短）

---

### M-3: CANCEL 信号文件存在 TOCTOU 竞态

**问题描述:**
取消流程：`inbox/CANCEL-{id}` 文件存在 → reconcile.py 检测到 → kill worker → 删除信号文件。

如果两个 reconcile 周期都检测到同一个 CANCEL 文件（第一轮 kill 了但删除信号文件失败），会对已死进程重复 kill。更严重的是，如果 PID 已被系统复用（分配给了新进程），会误杀无关进程。

**复现场景:**
1. 用户创建 `inbox/CANCEL-55`
2. reconcile.py 第 1 轮：检测到文件，kill PID 12345（worker 55），删除信号文件时磁盘 I/O 错误
3. PID 12345 被系统回收，分配给了新启动的 worker 58
4. reconcile.py 第 2 轮：信号文件还在，PID 文件中仍是 12345，kill 12345 → 误杀 worker 58

**建议修改:**
- Kill 前验证 PID 对应的进程确实是目标 worker（检查 `workers/{task_id}/pid` 中的 PID 与 `/proc/$PID/cmdline` 或 `ps -p` 输出匹配）
- 信号文件删除应在 kill 之前或作为原子操作（先 rename 再处理再删除）
- 增加 PID + 启动时间戳双重校验机制：`workers/{task_id}/pid` 文件中记录 `PID:START_TIME`，kill 前检查进程的启动时间是否匹配

---

### M-4: `cleanup_all_workers` 中 `wait 2>/dev/null` 可能无限挂起

**问题描述:**
现有代码在 trap handler 中：

```bash
cleanup_all_workers() {
    for pidfile in ...; do
        kill "$pid" 2>/dev/null
        pkill -P "$pid" 2>/dev/null
    done
    wait 2>/dev/null   # 等待所有子进程
    exit 0
}
```

`wait` 不带参数会等待所有子进程。如果某个子进程（如 npm 进程）不响应 SIGTERM，`wait` 将无限挂起。用户 Ctrl+C 后程序不退出，只能 `kill -9`。

**复现场景:**
1. 用户 Ctrl+C
2. `cleanup_all_workers` 向所有 worker 发 SIGTERM
3. 某个 worker 中的 `npm test` 进程不响应 SIGTERM（Node.js 进程有时需要 SIGKILL）
4. `wait` 无限等待，终端挂起

**建议修改:**
增加超时强制退出：

```bash
cleanup_all_workers() {
    for pidfile in ...; do
        kill "$pid" 2>/dev/null
        pkill -P "$pid" 2>/dev/null
    done
    # 给进程 5 秒优雅退出，然后 SIGKILL
    ( sleep 5 && for pidfile in ...; do
        [ -f "$pidfile" ] && kill -9 "$(cat "$pidfile")" 2>/dev/null
    done ) &
    wait 2>/dev/null
    exit 0
}
```

或使用 `timeout` 包裹 `wait`（但 bash 内置 `wait` 不能被 `timeout` 包裹，需要变通）。

---

### M-5: 孤儿回收逻辑未加锁，与 worker 启动存在竞态

**问题描述:**
现有代码（dev-loop.sh L1253-L1264）和设计中的 `reconcile.py` 孤儿回收逻辑：

1. 扫描 `workers/` 目录获取活跃 task_id 列表
2. 检查 `queue` 中 `in_progress` 的任务是否在活跃列表中
3. 不在 → 重置为 pending

但步骤 1 和步骤 2 之间存在窗口：如果一个新 worker 刚好在这两步之间启动，它的 PID 文件在步骤 1 时还不存在，但 state 已被标记为 `in_progress`。结果：刚启动的 worker 被误判为孤儿，任务被重置为 pending，可能被另一个 worker 再次领取 → 两个 worker 同时处理同一个任务。

**复现场景:**
1. 调度循环开始，扫描 `workers/` → 空
2. 调度循环分配 task 55 给新 worker，标记 `in_progress`
3. Worker 55 开始启动但还没来得及写 PID 文件
4. 孤儿回收：task 55 是 `in_progress` 但 workers/ 中无 PID → 重置为 pending
5. 下一轮分配 task 55 给 worker 56
6. Worker 55 完成启动，写入 PID 文件 → 两个 worker 同时处理 task 55

**建议修改:**
- 孤儿回收前先获取状态锁，确保与 worker 启动/分配互斥
- 或增加 grace period：任务进入 `in_progress` 后至少等 30 秒再检查孤儿（给 worker 启动留出时间）
- 或反转逻辑：worker 启动时先写 PID 文件，再标记 `in_progress`（而非反过来）

---

### M-6: `config.py` 热重载的 mtime 缓存在多进程场景下无意义

**问题描述:**
设计中 `config.py` 维护 `_last_mtime` 和 `_last_good_config` 做热重载缓存。但每次 Bash 调用 `python3 loop/lib/config.py` 都是一个新的 Python 进程，进程间不共享内存，`_last_mtime` 永远是初始值，每次调用都会重新解析 YAML。

**复现场景:**
1. Bash 调用 `python3 config.py export-shell` → Python 进程启动，解析 YAML，输出结果，退出
2. Bash 调用 `python3 config.py render-prompt fast-track` → 新 Python 进程启动，`_last_mtime` 为空，再次解析 YAML
3. 热重载的"缓存命中"逻辑永远不会触发

**建议修改:**
- 如果要实现真正的热重载缓存，需要将 mtime 和缓存配置持久化（如写到 `.cache/config.json`）
- 或者接受每次调用都解析 YAML 的开销（YAML 文件很小，解析耗时可忽略），将热重载功能简化为"解析失败时保留上次有效配置"——上次有效配置也需要持久化到磁盘
- 如果将来改为长驻 Python 进程（如 daemon），内存缓存才有意义

---

### M-7: Worktree 清理中 `git worktree remove --force` 可能丢失未提交修改

**问题描述:**
设计中 `cleanup_worktree` 在成功合入后执行 `git worktree remove --force`。如果 worker 的修改已 merge 到 dev 分支但 worktree 中还有未 commit 的文件（如临时日志、测试产物），`--force` 会直接删除。

更危险的场景：如果 `mark_task_done` 标记成功但实际 merge 未完成（如 git push 失败），worktree 中的代码就是唯一副本，此时清理会导致代码丢失。

**复现场景:**
1. Worker 完成任务，merge 到 dev 分支，`mark_task_done` 成功
2. 实际上 merge 有冲突，只是 `git merge` 命令退出码不为 0 但被忽略
3. `cleanup_worktree` 执行 `git worktree remove --force` → 唯一的代码副本丢失

**建议修改:**
- 清理前检查 worktree 是否有未提交修改：`git -C "$wt_path" status --porcelain`
- 如果有未提交修改，log 警告并跳过自动清理
- 或在清理前 `git stash` 保存一份快照

---

## 🟢 建议

### A-1: 建议 `failure.py` 的关键词匹配使用分层优先级而非简单遍历

**问题描述:**
设计中 `failure.py` 用关键词匹配日志尾部 50 行判定失败类型。但多种关键词可能同时出现（如 `permission denied` 出现在 `npm test` 的输出中），简单的顺序遍历可能导致误分类。

**建议修改:**
- 定义明确的优先级顺序（如 `permission` > `network` > `context` > `conflict` > `gate` > `unknown`）
- 使用加权评分：每个关键词有权重，所有匹配关键词加权求和，取最高分的类型
- 记录匹配过程到日志（哪些关键词命中、最终判定），方便后续调优

---

### A-2: 建议 notify.py 的 `osascript` 和 `curl` 调用增加超时

**问题描述:**
通知模块通过后台 `&` 调用不阻塞主流程，但 `curl` 到飞书 webhook 如果 DNS 解析慢或目标不可达，后台进程会长时间挂起，累积大量僵尸进程。

**建议修改:**
```bash
curl --connect-timeout 5 --max-time 10 -s -X POST ...
osascript -e '...' &  # 这个通常很快，可接受
```

---

### A-3: 建议 Prompt 模板渲染增加沙箱隔离

**问题描述:**
`config.py render-prompt` 对 `{{var}}` 做字符串替换。如果变量值中包含 `{{` 字符串（如任务描述中用户写了 `{{something}}`），可能导致二次替换或严格模式误报"未知变量"。

**建议修改:**
- 使用单遍替换（而非递归替换），替换后不再扫描结果
- 或换用成熟模板引擎（如 Python `string.Template` 的 `safe_substitute`）

---

### A-4: 建议为 `workers/{task_id}/` 增加 `.lock` 文件防止并发写

**问题描述:**
Dashboard 读取 `workers/*/state.json`，同时 worker 自身在写 `state.json`。虽然读脏数据不致命（dashboard 只是展示），但读到写了一半的 JSON 会导致解析报错。

**建议修改:**
- Worker 写 state 时先写 `.tmp` 文件，然后 `mv`（原子 rename）
- Dashboard 读取时 `try/except` JSON 解析错误，使用上一次成功读取的值

---

### A-5: 建议统一日志时间戳格式，避免时区歧义

**问题描述:**
设计中结构化日志使用 `[2026-03-27 10:00:00]` 格式，无时区标识。`stats.json` 使用 ISO 8601 带 Z 后缀。两者混用可能在日志分析时造成混乱。

**建议修改:**
- 统一使用 ISO 8601 格式 `[2026-03-27T10:00:00+08:00]` 或至少标注时区
- 与项目 CLAUDE.md 中的"全栈统一使用 ISO 8601"规范保持一致

---

### A-6: 建议 `add-task.sh` 的 ID 分配增加锁或原子操作

**问题描述:**
设计中 ID 分配逻辑："扫描 tasks/ 最大 ID + 1"。如果两个用户（或脚本）同时调用 `add-task.sh`，可能分配到相同 ID。

**建议修改:**
- 使用临时文件 + `mv` 的原子性：先生成到 `inbox/.tmp-{random}`，然后 rename
- 或使用基于时间戳的 ID（如 `YYYYMMDD-HHMMSS-random`），避免需要扫描现有文件

---

## 总体评价

**结论：设计方案有明确的架构思路，但在并发安全层面存在若干需要在实现前解决的关键问题。**

**优点：**
- 职责划分清晰（Bash 管进程，Python 管逻辑），方向正确
- 引入 reconcile.py 统一处理 stall/cancel/orphan，比现有散落逻辑好得多
- 失败分类 + 差异化重试策略是显著改进
- 配置外置 + prompt 模板化提升了可维护性

**主要风险：**
1. **锁机制分裂**（S-1）是最大隐患。Bash mkdir 锁和 Python 文件锁并存，必须在迁移阶段保证原子切换，否则并行模式下数据损坏概率很高
2. **进程管理缺少进程组概念**（S-3），kill 单个 PID 在多层进程树场景下不可靠，建议在 worker 启动时使用 `setsid` 创建独立进程组
3. **stats.json 无并发保护**（S-4）是遗漏，需要补充
4. **热重载缓存失效**（M-6）说明设计对 "每次调用都是新 Python 进程" 这一关键约束未充分考虑

**建议在实现前先做的事：**
1. 明确锁统一方案，写出迁移过渡期的不变式（invariant）
2. 对进程管理画出 PID/PGID 关系图，确认每个 kill 场景的正确性
3. 为 stats.json 选择并发策略（加锁 or JSONL append-only）
4. 补充集成测试用例：模拟 5 worker 并发写 state + stats 的压力测试

**风险评级：** 中高。4 个严重问题如果不在实现阶段修复，并行模式（5 worker）下大概率会触发数据损坏或进程泄漏。建议在 Phase 1 基础设施阶段就把锁统一和进程组管理做到位，不要留到后续 Phase。
