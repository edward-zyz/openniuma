# openNiuMa 合并升级设计 — 评审结论

> 评审日期：2026-03-27
> 评审方案：`2026-03-27-loop-v4-merged-design.md`
> 评审团：3 QA + 3 PM + 3 Dev，共 9 位虚拟评审员并行独立评审

---

## 一、总体判断

**方案方向正确，合并策略优秀，但实现细节存在系统性风险。**

9 位评审员的共识：Symphony 的基础设施质量 + v4 的用户体验增强，合并逻辑清晰，失败分类（6 类）是真正的差异化能力。核心风险不在"做什么"，而在"怎么做"——并发安全、跨平台兼容、进程模型效率三个问题如果不在编码前修正，会导致多 worker 并行模式下的系统性故障。

**综合评分：7.5/10**（架构设计 9/10，实现细节 6/10，产品策略 7/10）

---

## 二、必须修改项（Must Fix）

以下 8 项在 3+ 位评审员的报告中独立出现，是跨角色共识的高危问题。

### MF-1: 并发安全 — state.py / stats.json 的原子读写

| 发现者 | QA-1 S-1, QA-2 S-1/S-2, Dev-1 S2, Dev-2 S1/S2 |
|--------|------------------------------------------------|
| 严重度 | **致命** — 多 worker 模式下必现 |

**问题：** loop-state.json 和 stats.json 是 5 个 worker + 1 个调度器 + reconcile.py 共同读写的共享状态文件。设计只说"文件锁"，未定义具体方案。更严重的是，现有 dev-loop.sh 用 mkdir 锁，新 state.py 用 Python flock，两套锁互不感知，迁移期间必然出现 lost update。

**修改要求：**
1. **统一锁机制**：所有 loop-state.json 操作走 state.py，废弃 Bash mkdir 锁。Phase 1 必须同步迁移，不允许两套锁并存
2. **原子读写**：独立 .lock 文件 + fcntl.flock + 临时文件写入 + os.replace 原子替换 + fsync
3. **锁超时恢复**：锁文件记录 PID，超时后检查 PID 存活性，死进程自动回收锁
4. **stats.json 同理**：复用相同的 JsonFileStore 锁模式

---

### MF-2: 跨平台兼容 — 消除 macOS-only 倾向

| 发现者 | QA-3 S-01/S-02/S-03/S-04, Dev-3 S3/M5 |
|--------|----------------------------------------|
| 严重度 | **严重** — Linux/WSL/Docker 环境直接不可用 |

**问题清单：**

| macOS-only 代码 | Linux 行为 | 修改方案 |
|-----------------|-----------|---------|
| `sed -i ''` | 不识别空参数，行为错误 | 用 Python 替代或跨平台封装 |
| `cp -Rc`（APFS clone） | 无 `-c` 选项，直接报错 | 检测 platform 后降级为 `cp -R` |
| `timeout` 命令 | macOS 不自带（需 coreutils） | 用 Python `subprocess.timeout` 替代 |
| "Python 3.9 macOS 自带" | macOS 13+ 已不自带 Python | 明确为前置依赖，init.sh 检查版本 |
| PyYAML `pip3 install` | PEP 668 环境被拒绝 | 支持 pipx / venv / 内置 JSON 降级 |

**修改要求：** 新增 `lib/compat.py` 平台兼容层，所有 OS 相关操作走此模块，CI 增加 Linux 矩阵测试。

---

### MF-3: 进程模型 — bash→python 调用效率

| 发现者 | Dev-1 S1, Dev-2 S3, Dev-3 M1 |
|--------|------------------------------|
| 严重度 | **严重** — 每轮调度循环累积 400-960ms 开销 |

**问题：** 每次 `python3 loop/lib/xxx.py` 调用都是独立进程启动（~80-120ms/次），热路径上每轮 5-8 次调用。config.py 的"热重载"在独立进程模型下完全失效——每次都是冷启动，没有"上次有效配置"的概念。

**修改要求：**
1. **config.py 引入磁盘缓存**：解析结果写入 `.cache/workflow.json`（JSON 解析比 YAML 快 10x），配置出错时降级到缓存
2. **合并调用频次**：dev-loop.sh 每轮开头一次性 `eval "$(python3 config.py export-env)"`，后续直接用 shell 变量
3. **stats.py 改用 JSON stdin** 替代 12 个位置参数，避免 Bash 传参出错

---

### MF-4: 进程管理 — kill 无法覆盖进程树

| 发现者 | QA-2 S-3, QA-1 S-2, Dev-3 M2 |
|--------|------------------------------|
| 严重度 | **严重** — stall 检测后产生 orphan 进程 |

**问题：** stall 检测和取消操作通过 `kill PID` 终止 worker，但 claude CLI 可能 fork 子进程（node → subprocess），单个 PID kill 无法覆盖整个进程树，留下 orphan 进程占用资源。同时，kill 是异步的——被 kill 的 worker 可能在退出前完成任务并写入 state，reconcile 随后重置为 pending，导致任务重复执行。

**修改要求：**
1. worker 启动时用 `setsid` 创建独立进程组，kill 时 `kill -- -$PGID` 杀整个组
2. kill 后 waitpid 确认退出，再修改 state
3. state 写入增加版本号/时间戳，reconcile 只在版本未变时才重置

---

### MF-5: 通知抑制 — 防止通知轰炸

| 发现者 | PM-1 P1-1, PM-3 P0-3, QA-1 M-6 |
|--------|----------------------------------|
| 严重度 | **高** — 5 worker 并行场景下必然通知疲劳 |

**问题：** 通知按事件触发无去重。一个任务 gate 失败 3 次 = 3 条 warn + 1 条 critical。5 个 worker 同时网络异常 = 10+ 条通知。用户关掉通知后 critical 也收不到。

**修改要求：**
1. 同一 task_id + failure_type 在 N 分钟内合并为一条："#57 gate 失败 x3"
2. 非 critical 通知默认 5 分钟聚合一次
3. workflow.yaml 增加 `notify.suppress_window_sec` 和 `notify.quiet_hours`
4. 飞书渠道增加速率限制器

---

### MF-6: Shell 安全 — hook 执行和路径校验

| 发现者 | Dev-3 S1/S2 |
|--------|-------------|
| 严重度 | **严重** — 注入风险 + 数据丢失风险 |

**问题：** `bash -c "$hook"` 执行从 YAML 读取的多行脚本，特殊字符经 Python stdout → bash 变量 → bash -c 的链路可能被二次展开。`cleanup_worktree` 中 `rm -rf "$wt_path"` 未做路径安全校验。

**修改要求：**
1. hook 内容写入临时文件后 `bash "$hook_file"` 执行，避免引号嵌套
2. `rm -rf` 前校验路径在预期 base_dir 下：`[[ "$wt_path" == "${WORKTREE_BASE_DIR}/"* ]]`

---

### MF-7: 失败分类准确性

| 发现者 | QA-1 S-3, PM-3 P1-2, Dev-1 M1, Dev-2 M1 |
|--------|------------------------------------------|
| 严重度 | **高** — 误判导致错误的重试策略 |

**问题：** failure.py 仅扫描 session log 尾部 50 行做正则匹配，存在多重缺陷：关键错误可能在 50 行之外、关键词出现在正常输出中（如代码注释中的 "CONFLICT"）、多种失败类型共存时无优先级、Claude CLI 输出格式可能变化。

**修改要求：**
1. 扫描范围扩大到 200 行，或从尾部向上搜索到第一个错误标记
2. 分层匹配：exit code → 错误上下文行 → 关键词，增加 confidence 字段
3. 明确优先级：network > context > permission > conflict > gate > unknown
4. 低置信度走 unknown 路径，不猜测
5. 每种类型至少 5 正例 + 3 反例测试用例

---

### MF-8: 降级流程 — "暂停 loop" 需明确恢复路径

| 发现者 | QA-1 S-4 |
|--------|-----------|
| 严重度 | **高** — 无人值守场景下编排器可能无限停转 |

**问题：** 有下游依赖的任务失败达上限后"暂停 loop"，但未定义恢复机制。无人值守时可能停转数小时，其他独立任务也被阻塞。

**修改要求：**
1. 暂停只阻塞有依赖关系的任务链，独立任务继续
2. 定义恢复机制：`inbox/RESUME` 信号 + 可配置的超时自动恢复
3. 暂停期间 reconcile 和 inbox 扫描必须继续运行

---

## 三、应该修改项（Should Fix）

### SF-1: stats.json 数据轮转
**PM-3 P0-1** — 无限追加的 JSON 文件会膨胀到 MB 级。增加 `max_sessions` 配置，超出自动归档。或用 SQLite 替代。

### SF-2: 结构化日志增加关联字段
**PM-3 P0-2** — 日志缺 session_id/task_id，多 worker 并行时无法关联分析。持久化日志改用 JSONL 格式。

### SF-3: 统一 CLI 入口提前到 Phase 0
**PM-1 P0-2, Dev-1 M3, Dev-3 建议** — 7 个独立脚本认知负担大。从 Phase 0 提供 `loop.sh start/status/dashboard/stats/add/stop` 路由脚本（20 行）。

### SF-4: init.sh 增加 dry-run 验证
**PM-1 P0-1** — 自动探测结果用户无法验证。增加 `--dry-run`，运行 gate_command 确认能通过，输出置信度标注。

### SF-5: add-task.sh 分级模板
**PM-1 P0-3** — 一句话入队产生低质量任务。按 complexity 生成不同详细程度的模板，支持 `--ai` 自动补充。

### SF-6: init.sh JSON 解析合并
**Dev-3 S3, Dev-1 M6** — 6 次 `python3 -c "import json,sys; print(...)"` 管道解析同一 JSON 极其脆弱。合并为一次 `eval "$(python3 detect.py --shell-vars)"` 。

### SF-7: cost_usd 采集方案
**PM-3 P1-1** — 字段设计为 null 但无采集源。明确 Claude CLI 是否输出 token 用量，不能采集则从默认输出中移除。

### SF-8: workflow.yaml schema 校验
**QA-1 M-1, Dev-3 建议** — 热重载只检查 YAML 语法，不校验语义（如 `stall_timeout_sec: "abc"`）。config.py 增加 schema 验证。

---

## 四、可以改进项（Nice to Have）

| # | 来源 | 建议 |
|---|------|------|
| 1 | PM-2 P0-2 | 压缩 6 Phase 为 3 Phase（架构/可靠性/体验），每个 1-2 周 |
| 2 | PM-2 P0-3 | detect.py 过度工程化，考虑简化为交互式模板 + 少量自动探测 |
| 3 | PM-2 P1-4 | "牛马"命名有文化风险和 SEO 问题，品牌化决策延后 |
| 4 | PM-1 P1-2 | 断点续传的具体 prompt 传递机制需补充设计 |
| 5 | PM-3 P2 | Dashboard 布局可定制、数据导出、外部可视化集成 |
| 6 | Dev-1 M2 | YAML 中存储多行 hook 调试困难，支持 `@file:` 引用外部脚本 |
| 7 | Dev-2 M5 | retry.py 增加 jitter 防止多 worker 同时重试（thundering herd） |
| 8 | Dev-2 建议 | 统一使用 dataclass 定义 Python 模块间的数据契约 |
| 9 | QA-3 建议 | 增加 `--dry-run` 模式，init.sh 全流程可预览 |
| 10 | QA-2 建议 | 集成测试覆盖多 worker 并发场景，不能只靠单元测试 |

---

## 五、评审员一致认可的设计亮点

1. **失败分类 6 类差异化** — 所有评审员认为这是核心差异化能力，远优于 Symphony 的统一重试
2. **引擎/配置分离架构** — workflow.yaml + hooks 的设计思路清晰，实现可移植性的正确路径
3. **配置热重载理念** — 虽然实现需修改，但"不停机改配置"的设计意图是对的
4. **Worktree 保留复用** — 失败时保留 worktree 减少重建开销，是成熟工程实践
5. **合并策略本身** — Symphony 取基础设施，v4 取用户体验，取舍判断准确

---

## 六、修改优先级矩阵

```
          影响范围
          高 ┃ MF-1 并发安全    MF-4 进程树管理
            ┃ MF-2 跨平台      MF-3 进程模型效率
            ┃
          中 ┃ MF-5 通知抑制    MF-7 失败分类准确性
            ┃ MF-6 Shell安全   MF-8 暂停恢复
            ┃ SF-3 统一CLI     SF-1 数据轮转
            ┃
          低 ┃ SF-4 dry-run    SF-7 cost采集
            ┃ SF-5 任务模板    SF-8 schema校验
            ┗━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
              编码前必须修改    编码中处理    后续迭代
```

---

## 七、建议的行动计划

### 编码前（设计修订）
1. 补充 state.py 并发安全方案（MF-1），明确锁机制、原子写入、死锁恢复
2. 补充进程组管理方案（MF-4），明确 kill 策略和 state 一致性保证
3. 修订 config.py 热重载设计（MF-3），引入磁盘缓存 + export-env 模式
4. 新增 `lib/compat.py` 平台兼容层规格（MF-2）
5. 修订 failure.py 分层匹配 + 置信度设计（MF-7）
6. 修订降级流程，明确部分暂停 + 恢复路径（MF-8）

### Phase 1 同步实现
- hook 临时文件执行 + rm -rf 安全校验（MF-6）
- 通知抑制/聚合（MF-5）
- 统一 CLI 入口（SF-3）
- init.sh JSON 解析合并（SF-6）

### 后续迭代
- 结构化日志 JSONL（SF-2）
- stats.json 轮转或 SQLite 迁移（SF-1）
- schema 校验（SF-8）
- 集成测试（QA-2 建议）

---

## 附录：评审员清单与报告索引

| 编号 | 角色 | 评审重点 | 问题数 | 文件 |
|------|------|---------|--------|------|
| QA-1 | 测试工程师 | 可测试性、边界条件、错误处理 | 4🔴 7🟡 6🟢 | `QA-1-测试工程师.md` |
| QA-2 | 并发安全专家 | 文件锁竞态、进程管理、跨语言调用 | 4🔴 7🟡 6🟢 | `QA-2-并发安全专家.md` |
| QA-3 | 可移植性专家 | 跨平台兼容、init.sh 鲁棒性 | 4🔴 8🟡 8🟢 | `QA-3-可移植性专家.md` |
| PM-1 | 产品经理 | 用户体验、上手门槛、信息架构 | 3P0 4P1 5P2 | `PM-1-产品经理.md` |
| PM-2 | 产品架构师 | 产品定位、竞品差异、MVP 范围 | 3P0 4P1 6P2 | `PM-2-产品架构师.md` |
| PM-3 | 可观测性 PM | 可观测性完备性、通知、数据价值 | 3P0 5P1 5P2 | `PM-3-可观测性产品经理.md` |
| Dev-1 | 架构开发 | 模块职责、bash↔python 效率 | 3🔴 6🟡 | `Dev-1-架构开发.md` |
| Dev-2 | Python 开发 | 模块质量、错误处理、性能 | 3🔴 6🟡 6🟢 | `Dev-2-Python开发.md` |
| Dev-3 | Shell 开发 | Bash 质量、git worktree、CLI | 4🔴 8🟡 6🟢 | `Dev-3-Shell开发.md` |

**总计：120+ 条发现，其中 Must Fix 8 项、Should Fix 8 项、Nice to Have 10 项。**
