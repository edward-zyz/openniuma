# PM-3 评审：可观测性与数据策略

> 评审人：PM-3（可观测性产品经理）
> 评审对象：openNiuMa 合并升级设计 — 可观测性、通知、数据沉淀部分
> 日期：2026-03-27

---

## P0 必须解决

### P0-1: stats.json 缺乏数据轮转和容量控制

**问题描述：** stats.json 是一个持续追加的 JSON 文件，sessions 和 tasks 数组会无限增长。跑几十个 batch（每个 batch 几十个任务）后，文件可能膨胀到数 MB，导致每次读写都要完整加载/序列化整个文件，影响调度循环的响应时间。

**数据/运维影响：**
- 文件读写延迟随数据量线性增长，最终拖慢调度 tick
- Python 端 `json.load()` 大文件的内存开销
- 没有归档机制，历史数据和当前运行数据混在一起，查询效率低

**建议方案：**
1. 引入 `max_sessions` 配置（默认 1000），超出后自动归档到 `stats-archive-{date}.json`
2. 当前 stats.json 只保留最近 N 个 batch 的数据
3. `stats.py summary` 支持 `--all` 参数合并归档数据查询
4. 或者直接用 SQLite 替代 JSON 文件（项目已有 `SQLITE_DB_PATH` 配置），天然支持增量写入和高效查询

### P0-2: 结构化日志缺少关键字段，不可关联分析

**问题描述：** 当前日志格式 `[时间] [模块] emoji 消息` 是人类友好的，但缺少机器可解析的关键字段。没有 trace_id / session_id / task_id 贯穿一个任务的完整生命周期，无法做跨 worker 的关联分析。

**数据/运维影响：**
- 多 worker 并行时，日志交叉在 orchestrator.log 里，无法按任务过滤
- 无法回答"任务 #55 经历了哪些事件"这类基本问题
- 后续如果想接入 ELK/Loki 等日志系统，缺乏结构化字段

**建议方案：**
1. 日志格式改为 JSON Lines（`.jsonl`），每行一个 JSON 对象：
   ```json
   {"ts":"2026-03-27T10:00:00Z","module":"worker","task_id":55,"session_id":"s-001","level":"info","msg":"启动 refresh-heatmap"}
   ```
2. 保留人类可读的终端输出（emoji 格式），但持久化日志用 JSONL
3. 每个 session 启动时生成唯一 `session_id`，贯穿该 session 的所有日志
4. `status.py` 和 `dashboard.sh` 可以基于 task_id 过滤日志

### P0-3: 通知无去重和抑制机制

**问题描述：** 通知系统按事件触发，但没有设计去重/抑制策略。一个任务如果反复 gate 失败 3 次，会连续发 3 条 warn + 1 条 critical，短时间内产生 4 条通知轰炸。如果 5 个 worker 同时遇到网络问题，会产生 10+ 条通知。

**数据/运维影响：**
- 通知疲劳（alert fatigue）——用户很快会关掉通知或忽略所有通知
- 飞书 Webhook 有速率限制，密集发送可能被限流丢消息
- 真正重要的 critical 通知淹没在大量 warn 中

**建议方案：**
1. 引入通知抑制窗口：同一 task_id + 同一 failure_type 在 N 分钟内只发一次，后续聚合为"#57 gate 失败 x3"
2. 引入通知摘要模式：非 critical 通知默认每 5 分钟聚合一次批量发送
3. 飞书渠道增加速率限制器：最多每分钟 N 条，超出排队
4. 在 workflow.yaml 的 notify 节增加 `suppress_window_sec: 300` 和 `digest_interval_sec: 300`

---

## P1 应该解决

### P1-1: cost_usd 字段设计为 null 但无采集方案

**问题描述：** stats.json 中 `cost_usd` 字段出现在 session 和 task 两个层级，但示例值为 `null`。设计文档没有说明成本数据的采集来源。Claude CLI 目前是否输出 token 用量或成本信息？如果不能自动采集，这个字段永远是 null，数据沉淀就失去了成本分析的价值。

**数据/运维影响：**
- stats.sh 展示的成本数据（`$8.45`、`$2.11/任务`）可能全部是虚假数据或 null
- 用户基于虚假成本做优化决策会产生误导
- 没有成本数据，无法回答"复杂度高的任务是否值得拆分"这类关键问题

**建议方案：**
1. 明确成本数据来源：如果 Claude CLI 输出 token 使用量，解析 session log 提取；如果不输出，标注为"未来支持"
2. 退而求其次：记录 token 数量（input_tokens / output_tokens）而非直接记录美元金额，汇率可以后期计算
3. 如果确实无法采集，将 cost_usd 从 stats.sh 默认输出中移除，避免显示 `$0.00` 误导用户
4. 在 workflow.yaml 增加 `cost_per_1k_tokens` 配置，允许用户按模型手动设定

### P1-2: 失败分类（failure.py）只分析尾部 50 行，覆盖面不足

**问题描述：** 失败分类只扫描 session log 最后 50 行，但某些失败模式的关键信息可能出现在日志中部。例如 context window 超限的警告可能在中段就出现了，但 agent 继续尝试了很多轮后才最终退出，最后 50 行可能只是退出信息。

**数据/运维影响：**
- 分类准确率直接影响重试策略的有效性
- 误分类为 `unknown` 会导致不必要的跳过或暂停
- 失败类型分布数据不准确，无法指导系统优化

**建议方案：**
1. 分两段扫描：尾部 50 行（高优先级）+ 全文关键词检索（低优先级），尾部匹配优先
2. 对于 `context` 类型特别处理：全文搜索 `context window` / `token limit`，因为这类错误不一定出现在尾部
3. 增加分类置信度字段 `confidence: high|medium|low`，低置信度的分类在通知中标注，提醒人工确认
4. 记录用于分类的原始匹配行，方便事后审计分类准确性

### P1-3: Dashboard 缺少错误率趋势和健康度指标

**问题描述：** Dashboard 展示的是当前快照（任务列表、worker 状态、容量），但缺少趋势指标。运维产品的核心是"快速发现问题"，需要让用户一眼看出系统是否健康。

**数据/运维影响：**
- 用户需要多次查看并人工对比才能发现退化趋势
- "平均 session 时间在增加"或"gate 失败率在升高"这类信号被忽略
- 缺少健康度总结，用户需要看完所有区域才能判断当前状态

**建议方案：**
1. Dashboard Header 区域增加健康度信号灯：
   - 绿：无失败，所有 worker 正常
   - 黄：有重试但未达上限
   - 红：有任务 blocked 或 loop 暂停
2. Stats 区域增加最近 N 个 session 的成功率（如 `最近 10 session: 7/10 成功`）
3. 可选：增加 Recent Failures 区域，展示最近 3 次失败的类型和任务

### P1-4: Mermaid 甘特图在大量任务时可读性差

**问题描述：** Session 甘特图在任务数量多、并行度高时，Mermaid 渲染会变得非常拥挤，无法有效传递信息。50 个任务 * 平均 4 session = 200 条甘特条，人眼无法解读。

**数据/运维影响：**
- PROGRESS.md 变成摆设，团队成员不看
- 甘特图渲染可能超出 Mermaid/GitHub 的渲染限制
- 无法有效向利益相关者展示进度

**建议方案：**
1. 甘特图只展示当前 batch 最近 N 个任务（默认 10），历史任务折叠
2. 增加"按任务汇总"视图：每个任务一行，长度 = 总耗时，颜色 = 最终状态
3. 增加文字摘要版（对 CI/Slack/飞书 等纯文本场景更友好）
4. 提供 `--full` 参数在需要时生成完整甘特图

### P1-5: 日志分散，缺乏集中检索能力

**问题描述：** 每个 worker 有独立 session log，汇总到 orchestrator.log。但是没有提供按条件检索日志的工具。用户排查问题时需要手动 grep 多个文件。

**数据/运维影响：**
- 排障效率低，尤其在并行 5 worker 的场景下
- 没有日志关联能力，无法快速从一个告警定位到相关 session log

**建议方案：**
1. `status.sh --logs <task_id>` 自动定位并输出该任务所有相关 session log
2. `status.sh --errors` 汇总所有 session 中 exit_code != 0 的日志尾部
3. orchestrator.log 中记录每个 session 对应的 log 文件路径，方便交叉引用
4. 长期考虑：日志写入 SQLite 的 `logs` 表，支持结构化查询

---

## P2 可以改进

### P2-1: 通知渠道扩展性不足

**问题描述：** 当前固定支持 macOS / 飞书 / Bell 三种渠道，硬编码在 notify.py 中。不同团队可能需要 Slack、钉钉、Telegram、邮件等渠道。

**数据/运维影响：**
- 新渠道需要修改 notify.py 源码，违背可移植性设计目标
- 飞书在海外团队不适用

**建议方案：**
1. 通知渠道采用插件机制：`notify.py` 定义 `Channel` 接口，每种渠道一个实现文件
2. workflow.yaml 中渠道配置改为数组：
   ```yaml
   notify:
     channels:
       - type: macos
         enabled: true
       - type: webhook
         url: "https://..."
         template: "feishu"  # 或 slack / discord
   ```
3. 内置提供 macOS + generic webhook（支持自定义 payload template），覆盖大多数场景

### P2-2: stats.json 缺少 batch 维度的聚合

**问题描述：** 数据只有 session 和 task 两个维度，没有 batch（一次 dev-loop 运行）的概念。无法回答"第 3 个 batch 比第 2 个 batch 效率提高了多少"。

**数据/运维影响：**
- 无法跨批次对比效率趋势
- 无法衡量配置调整（如 max_concurrent 从 3 改到 5）的效果
- 缺少批次粒度的成本和时间汇总

**建议方案：**
1. stats.json 增加 `batches[]` 数组，每次 `dev-loop.sh` 启动时创建一条 batch 记录
2. 记录字段：batch_id, started_at, ended_at, task_count, session_count, total_cost, config_snapshot（记录关键配置如 max_concurrent）
3. session 和 task 记录增加 `batch_id` 字段关联
4. stats.sh 增加 `--compare` 模式：对比两个 batch 的效率指标

### P2-3: 状态查看缺少历史回放能力

**问题描述：** loop-state.json 是实时覆写的单文件，无法回看"10 分钟前系统是什么状态"。排障时经常需要知道问题发生时的上下文。

**数据/运维影响：**
- 事后分析困难：问题发生时的状态已经被覆写
- 无法复现间歇性问题的触发条件

**建议方案：**
1. 每次 loop-state.json 变更时，追加一条快照到 `logs/state-history.jsonl`（JSONL 格式，每行一条）
2. 包含时间戳和变更原因（如 `"reason": "task #55 completed"`）
3. 提供 `status.sh --at "2026-03-27T10:00:00"` 查看历史快照
4. state-history.jsonl 同样需要轮转策略，建议保留最近 7 天

### P2-4: Dashboard 缺少自定义布局能力

**问题描述：** Dashboard 的区域布局是固定的（Header / Progress / Task List / Pipeline / Workers / Sessions / Stats）。不同用户关注点不同：运维人员关注 Workers 和 Failures，开发者关注 Task List 和 Pipeline。

**数据/运维影响：**
- 小终端窗口显示不全，用户看不到关心的区域
- 信息密度高但无法按需裁剪

**建议方案：**
1. 支持 `dashboard.sh --sections workers,tasks,stats` 自定义显示哪些区域
2. 支持 `dashboard.sh --compact` 精简模式（只显示 Progress + Workers + 最近失败）
3. 在 workflow.yaml 中支持 `dashboard.default_sections` 配置

### P2-5: 缺少数据导出和可视化集成

**问题描述：** stats.json 的数据只能通过 stats.sh 命令行查看。没有提供导出为 CSV、与外部可视化工具（Grafana、Google Sheets）集成的能力。

**数据/运维影响：**
- 数据沉淀了但分析手段有限
- 无法做自定义的多维分析
- 无法生成给管理层的可视化报告

**建议方案：**
1. `stats.sh --format csv` 导出 CSV（session 维度和 task 维度各一个文件）
2. `stats.sh --format json` 已有，确保输出是标准的、可被 jq 处理的格式
3. 长期：提供一个简单的 HTML 报告生成器（基于 Chart.js 或内嵌 SVG），`stats.sh --html > report.html`

---

## 总体评价

**评分：7.5/10 — 框架完整，细节需打磨。**

**亮点：**
- 可观测性分层设计合理：Dashboard（实时）、status.sh（脚本友好）、stats.json（数据沉淀）、PROGRESS.md（分享）、通知（异步）覆盖了主要使用场景
- 失败分类 + 差异化重试策略是一个很好的设计，从"盲目重试"升级到"理解失败原因后有针对性地重试"
- 通知的三层渠道 + 级别分级符合实际使用模式
- stats.json 的数据模型（session + task 两个维度）能支撑大部分分析需求

**主要风险：**
1. **数据可靠性**：stats.json 无轮转、cost_usd 无采集源、failure 分类准确性未验证——这些会导致数据沉淀的价值大打折扣。数据驱动优化的前提是数据本身可信。
2. **通知疲劳**：缺乏抑制/聚合机制，多 worker 并行场景下通知轰炸几乎是必然的。这会直接伤害用户对通知系统的信任。
3. **日志可关联性**：当前日志设计在单 worker 场景够用，但方案明确目标是 5 worker 并行，此时缺乏 session_id / task_id 的结构化日志会严重阻碍排障。

**建议优先级：** P0-3（通知抑制） > P0-2（结构化日志） > P0-1（数据轮转） > P1-1（成本采集） > P1-3（健康度指标）。前三个如果不在 V1 解决，系统可用但用户体验会在规模化使用后快速退化。
