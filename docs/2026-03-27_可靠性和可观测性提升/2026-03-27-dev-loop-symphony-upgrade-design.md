# dev-loop Symphony 升级设计

> 参考 OpenAI Symphony Service Specification，对 `loop/dev-loop.sh` 进行可靠性、可配置性、可观测性三维升级。

## 1. 背景与动机

`dev-loop.sh` 是一个 bash 编排器，驱动 Claude Code 完成自治研发循环。当前 1677 行，其中约 40% 是 `python3 -c` / `python3 <<PYEOF` 内联代码。

对照 Symphony 规范，识别出三类差距：

| 维度 | Symphony 做法 | dev-loop.sh 现状 | 差距 |
|------|-------------|-----------------|------|
| 运行时可靠性 | stall 检测 + 指数退避 + reconciliation | 固定 30s 重试，无卡死检测，无运行中取消 | 高 |
| 可配置性 | WORKFLOW.md (YAML front matter + prompt)，热重载 | 配置硬编码在脚本顶部，prompt 在 heredoc 中 | 高 |
| 可观测性 | 结构化日志 + HTTP API + Dashboard | `tail -f` 看日志，手动查 JSON | 中 |

## 2. 设计决策

### 2.1 实现路径：Bash 入口 + Python 模块

**选择理由：** 脚本中已有大量 Python 内联代码（状态读写、inbox 处理、backlog 生成），提取为模块是自然演进。Bash 保留擅长的领域（进程管理、信号处理、git/worktree 操作、claude CLI 调用），Python 承担所有 JSON/YAML/业务逻辑。

**否决的方案：**
- 纯 bash 改造 — YAML 解析和复杂状态逻辑用 bash 越来越别扭
- Python 全重写 — 一次性工作量大，且 bash 在进程管理和 shell 集成上仍有优势

### 2.2 不做的事

- 不做 HTTP Dashboard — 单人使用场景 `status.sh` 足够
- 不做远程通知（Slack/飞书） — 本地 CLI 工具，不需要
- 不引入日志框架 — 保持 bash echo + Python print
- 不引入 Python 包管理（pip/venv） — 只用标准库 + PyYAML（macOS 自带或 brew 安装）

## 3. 目标架构

```
loop/
├── dev-loop.sh              # 入口 + worktree/git + claude CLI（保留，精简）
├── workflow.yaml             # 运行时配置（新增）
├── prompts/                  # Prompt 模板（新增）
│   ├── _common-rules.md      # 公共规则片段
│   ├── fast-track.md
│   ├── design-implement.md
│   ├── design.md
│   ├── implement.md
│   ├── verify.md
│   ├── fix.md
│   ├── merge.md
│   ├── merge-fix.md
│   ├── finalize.md
│   └── ci-fix.md
├── lib/                      # Python 模块（新增）
│   ├── __init__.py
│   ├── config.py             # 配置加载 + 热重载 + prompt 渲染
│   ├── state.py              # loop-state.json 读写 + 文件锁
│   ├── inbox.py              # inbox 扫描 + 任务入队
│   ├── backlog.py            # backlog.md 全量生成
│   ├── reconcile.py          # stall 检测 + 取消 + 孤儿回收 + 依赖传播
│   ├── retry.py              # 指数退避计算
│   └── status.py             # 状态汇总输出（text / JSON）
├── status.sh                 # 一键状态查看（调用 lib/status.py）
├── loop-state.json
├── inbox/
├── tasks/
├── logs/
└── workers/
```

### 3.1 职责划分

**dev-loop.sh 保留：**
- 参数解析（`--workers`, `--verbose`, `--single-task`）
- 信号处理（`trap INT TERM HUP`）
- Worktree 生命周期（`ensure_worktree`, `cleanup_worktree`）
- Claude CLI 调用（`claude -p "$prompt" ...`）
- 主循环骨架（while true + sleep）
- Worker 进程管理（fork、PID 追踪、kill）

**lib/ Python 模块承担：**
- 所有 JSON 读写（替代 `python3 -c "import json..."` 内联）
- YAML 配置解析 + 热重载
- Prompt 模板渲染（`{{var}}` 替换）
- inbox 处理逻辑
- backlog.md 生成
- Reconciliation 逻辑
- 退避计算
- 状态汇总输出

### 3.2 Bash → Python 调用约定

```bash
# 配置导出为 shell 变量
eval "$(python3 loop/lib/config.py export-shell)"

# Prompt 渲染
prompt=$(python3 loop/lib/config.py render-prompt "$phase")

# 状态操作
python3 loop/lib/state.py read-phase
python3 loop/lib/state.py read-field branch
python3 loop/lib/state.py read-slug

# inbox 处理
python3 loop/lib/inbox.py process

# backlog 刷新
python3 loop/lib/backlog.py refresh

# reconciliation（并行模式每轮调用）
python3 loop/lib/reconcile.py run

# 退避计算
sleep $(python3 loop/lib/retry.py backoff "$consecutive_failures")

# 状态汇总
python3 loop/lib/status.py --format text
python3 loop/lib/status.py --format json
```

所有 Python 脚本：
- 通过 `sys.argv` 接收参数，stdout 输出结果
- 非零退出码表示错误
- stderr 输出日志/警告
- 无外部依赖（除 PyYAML）

## 4. 配置外置 + 热重载

### 4.1 workflow.yaml 完整 schema

```yaml
# loop/workflow.yaml

polling:
  inbox_interval_sec: 60          # 无活跃任务时的轮询间隔

workers:
  max_concurrent: 5               # 并行 worker 上限
  stall_timeout_sec: 1800         # 日志无更新超时（秒）
  max_consecutive_failures: 3     # 连续 phase 不推进上限

retry:
  base_delay_sec: 10              # 首次重试延迟
  max_backoff_sec: 300            # 退避上限
  rate_limit_default_wait_sec: 600  # 限流默认等待

worktree:
  base_dir: .trees                # 相对于主仓库
  prefix: loop

prompts:
  dir: loop/prompts               # prompt 模板目录
  common_rules: loop/prompts/_common-rules.md
```

### 4.2 热重载机制

`config.py` 维护两个内部状态：
- `_last_mtime`：上次读取时的文件修改时间
- `_last_good_config`：最近一次成功解析的配置

每次被 bash 调用时：
1. `stat workflow.yaml` 获取 mtime
2. mtime 未变 → 返回缓存配置
3. mtime 变了 → 重新解析
   - 成功 → 更新缓存 + 返回新配置
   - 失败 → stderr 输出警告 + 返回上次有效配置（不崩溃）

由于 bash 每轮循环都会 `eval "$(python3 ... export-shell)"`，配置变更在下一轮 tick 自动生效。

### 4.3 Prompt 模板

模板文件使用 `{{var}}` 占位符，`config.py render-prompt` 负责：
1. 读取 `loop/prompts/{phase}.md`（phase 名转小写 + 连字符，如 `FAST_TRACK` → `fast-track.md`）
2. 读取 `_common-rules.md` 内容
3. 从 `loop-state.json` 提取变量：`dev_branch`, `slug`, `branch`, `spec_path`, `plan_path`
4. 替换所有 `{{var}}`，注入 `{{common_rules}}`
5. 输出最终 prompt 到 stdout

未知变量 → 报错退出（严格模式，与 Symphony 一致）。

## 5. 运行时可靠性

### 5.1 Stall 检测

**触发条件：** `parallel_main` 每轮调度循环中，在回收 worker 之前执行。

**检测逻辑：**
```
for each active worker:
  log_file = logs/worker-{task_id}.log
  last_activity = max(log_file.mtime, worker_start_time)
  elapsed = now - last_activity

  if elapsed > stall_timeout_sec:
    kill worker PID
    pkill -P worker_PID          # 杀子进程（claude CLI）
    log "[reconcile] Worker #{task_id} 卡死 {elapsed}s，已终止"
    sync_worker_result(task_id, exit_code=1)   # 标记异常 → 进入重试
```

**串行模式不需要：** `claude -p` 调用本身会超时退出，bash 能感知到退出码。

### 5.2 指数退避

替换现有固定 `sleep 30`：

```
delay(attempt) = min(base_delay_sec * 2^(attempt-1), max_backoff_sec)

attempt=1 → 10s
attempt=2 → 20s
attempt=3 → 40s（随后触发 max_consecutive_failures 停止）
```

限流处理保持现有逻辑（解析重置时间、计算等待秒数），不计入失败次数。

### 5.3 Reconciliation

`lib/reconcile.py run` 在每轮调度循环中被调用，执行三项检查：

**1. 取消检测：**
- 扫描 `inbox/CANCEL-{task_id}` 文件
- 扫描 `tasks/` 文件 frontmatter `status: cancelled`
- 匹配到 → kill 对应 worker → 更新 loop-state.json 中任务状态为 cancelled → 删除哨兵文件

**2. 孤儿回收（从现有内联代码提取）：**
- 扫描 queue 中 `status == "in_progress"` 的任务
- 如果对应 `workers/{task_id}/pid` 不存在或进程已死 → 重置为 pending

**3. 依赖传播（从现有内联代码提取）：**
- 任务被标记 blocked → 依赖它的任务级联标记 blocked

### 5.4 失败时保留 Worktree

**规则：**

| Worker 结果 | Worktree | 数据库 |
|-------------|----------|--------|
| 成功 + 合入 dev | 清理 | 清理 |
| 失败 / 需要重试 | 保留 | 保留 |
| 手动取消 / STOP | 清理 | 清理 |

**改动点：** `parallel_main` 中回收 worker 后，仅在 `exit_code == 0` 时调用 `cleanup_worktree`。

## 6. 可观测性

### 6.1 status.sh

```bash
#!/usr/bin/env bash
# 一键查看 dev-loop 运行状态
python3 "$(dirname "$0")/lib/status.py" "$@"
```

**文本输出示例：**
```
═══ dev-loop 状态 ═══
分支: dev/backlog-batch-2026-03-26
更新: 2 分钟前

🔄 运行中 (2/5)
  #55 refresh-heatmap    DESIGN_IMPLEMENT  12m  日志活跃
  #58 notification-push  VERIFY            3m   日志活跃

⏳ 待执行 (3)
  #60 export-pdf [低]
  #61 brand-compare [中]  ← blocked by #58
  #62 mobile-swipe [低]

✅ 已完成 (4)
  #50 invite-bug  #51 poi-cache  #52 point-detail  #53 status-bar

⚠️ 告警
  （无）
```

**JSON 输出（`--format json`）：**
```json
{
  "timestamp": "2026-03-27T10:00:00Z",
  "dev_branch": "dev/backlog-batch-2026-03-26",
  "counts": {
    "running": 2,
    "pending": 3,
    "blocked": 1,
    "done": 4
  },
  "running": [
    {
      "task_id": 55,
      "name": "refresh-heatmap",
      "phase": "DESIGN_IMPLEMENT",
      "elapsed_min": 12,
      "log_stale": false
    }
  ],
  "pending": [...],
  "done": [...],
  "alerts": []
}
```

### 6.2 结构化日志

**主调度器日志格式增加 tag：**
```
[2026-03-27 10:00:00] [scheduler] 📊 活跃 worker: 2/5 [#55 #58]
[2026-03-27 10:00:01] [worker:55] 🚀 启动 (refresh-heatmap)
[2026-03-27 10:05:00] [reconcile] ⚠️ Worker #58 卡死 1800s，终止
[2026-03-27 10:05:01] [retry] Worker #58 第 2 次重试，等待 20s
```

**改动：** `log()` 函数增加可选 tag 参数：
```bash
log() {
  local tag="${LOG_TAG:-scheduler}"
  echo "[$(timestamp)] [${tag}] $*"
}
```

**汇总日志：** 调度器事件（启动/完成/失败/stall/取消）同时写入 `logs/orchestrator.log`，方便整体回溯。

### 6.3 verbose 模式的成本追踪

从 stream-json 输出提取 `total_cost_usd` 和 `duration_ms`，追加到 worker 状态中。`status.py` 汇总时累加展示总成本。

## 7. 迁移策略

**渐进式迁移，不一次性全改：**

### Phase 1：基础设施
- 创建 `loop/lib/` 目录结构
- 实现 `config.py`（YAML 加载 + shell 导出 + prompt 渲染）
- 实现 `state.py`（替代所有 `python3 -c "import json..."` 状态读写）
- 创建 `workflow.yaml` + `prompts/*.md`
- dev-loop.sh 改为调用模块（保持行为不变）

### Phase 2：可靠性
- 实现 `reconcile.py`（stall 检测 + 取消 + 孤儿回收）
- 实现 `retry.py`（指数退避）
- 修改 worktree 清理策略（失败时保留）
- dev-loop.sh 集成新的 reconciliation 和 retry 逻辑

### Phase 3：可观测性 + 收尾
- 实现 `status.py` + `status.sh`
- 提取 `inbox.py` 和 `backlog.py`（从内联代码迁移）
- 日志格式升级（tag + 汇总日志）
- 清理 dev-loop.sh 中已迁移的内联 Python 代码

每个 Phase 独立可用，Phase 1 完成后 dev-loop.sh 行为不变但结构更好，Phase 2 加入可靠性，Phase 3 补齐可观测性。

## 8. 依赖

- Python 3.9+（macOS 自带）
- PyYAML（`pip3 install pyyaml` 或 `brew install pyyaml`，macOS Sonoma+ 需要手动安装）
- 无其他外部依赖

## 9. 测试策略

Python 模块使用 `pytest` 或 `python3 -m unittest` 测试：
- `config.py`：YAML 解析、热重载（mtime mock）、prompt 渲染、未知变量报错
- `state.py`：读写 + 锁竞争
- `reconcile.py`：stall 检测阈值、取消逻辑、孤儿回收
- `retry.py`：退避公式边界值

Bash 层不写单元测试，通过现有的手动验证 + CI 门禁覆盖。
