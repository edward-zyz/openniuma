# PM-2 评审：产品架构与 GTM 策略

> 评审对象：openNiuMa AI 自治研发编排器升级设计（合并版）
> 评审角色：资深产品经理，聚焦产品定位清晰度、竞品差异化、迁移计划合理性、MVP 范围控制
> 评审日期：2026-03-27

---

## P0 必须解决

### P0-1: 产品身份分裂 —— 内部工具还是开源产品？

**问题描述：** 整篇设计文档存在两种人格的激烈冲突。一方面，大量细节仍然以 POI 项目为第一视角（数据库连接字符串、APFS clone 优化、npm workspaces 的 monorepo 处理），这是典型的内部工具写法。另一方面，init.sh 的 6 种技术栈探测、detect.py 的 100+ 行通用逻辑、Phase 5 的品牌化改名，又在追求一个通用开源工具的愿景。

这两种产品形态的核心需求是对立的：
- **内部工具**：功能完备优先，接受粗糙的上手体验，配置可以手动微调。
- **开源产品**：Time-to-Value 优先（5 分钟内跑通 hello world），需要优秀的文档、友好的错误信息、社区互动设计。

当前设计两头都想抓，结果是 6 个 Phase、12 个 Python 模块、6 个 Shell 脚本、10+ 个 prompt 模板 —— 复杂度远超单人或小团队的有效维护能力。

**战略影响：** 定位模糊导致"功能很多但没人用"的困境。内部用户觉得品牌化改名是无用噪音，外部用户觉得上手门槛太高。开源项目的成败从来不取决于功能数量，而取决于前 5 分钟体验和核心价值主张的锐利程度。

**建议方案：**
1. 明确分阶段定位：Phase 0-4 的唯一目标是"让 POI 项目的 dev-loop 更可靠、更好用"。不为通用性增加任何复杂度。
2. 可移植性作为"配置外置"的自然副产品获得，而不是主动为 6 种技术栈做适配工程。
3. Phase 5 的品牌化和开源准备，只在有真实外部需求信号时才启动。

---

### P0-2: MVP 范围严重膨胀 —— 6 Phase = 没有 MVP

**问题描述：** 迁移策略包含 6 个 Phase，涉及约 20 个新文件。逐项清点：

| 类别 | 数量 | 明细 |
|------|------|------|
| Python 模块 | 12 | config, state, inbox, backlog, reconcile, retry, failure, stats, notify, status, detect, test_* |
| Shell 脚本 | 6 | dashboard, stats, notify, add-task, generate-progress, status |
| 配置/模板 | 11+ | workflow.yaml schema + 10 prompt 模板 |
| 大规模重构 | 1 | dev-loop.sh 从内联 Python 迁移到 lib/ 调用 |
| 品牌化改名 | 50+ | 所有文件路径和引用 |

这是一个 Full Product Vision，不是 Minimum Viable Product。对于单人维护的内部工具，一次性规划这么多改造，几乎必然出现以下情况：
- Phase 1-2 做完后发现架构假设有误，需要返工 Phase 0
- Phase 3-4 的优先级在实际使用中被推翻（比如发现 dashboard 其实用不上，真正痛的是 notify）
- Phase 5 因为前置工作量永远排不上

更关键的是，迁移期间系统处于"新旧架构并存"的混沌态 —— 部分逻辑在 dev-loop.sh 内联 Python 中，部分在 lib/*.py 中，部分在 Shell 脚本中。这种状态下调试和维护的成本是正常的 2-3 倍。

**战略影响：** 规划越大，"半成品"状态持续时间越长。用户（包括自己）在迁移期间体验最差。

**建议方案：** 压缩到 3 个 Phase，每个 Phase 1-2 周内完成，每个 Phase 结束后系统完全可用：

| Phase | 内容 | 核心价值 | 时间 |
|-------|------|---------|------|
| A: 架构 | config.py + state.py + workflow.yaml + prompt 模板化 + dev-loop.sh 接入 | 消除内联 Python，配置外置 | 1 周 |
| B: 可靠性 | failure.py + retry.py + reconcile.py + worktree 保留 | 失败自愈，减少人工干预 | 1 周 |
| C: 体验 | 从 dashboard/stats/notify/add-task 中选 2 个最痛的 | 按需扩展 | 1 周 |

Phase 5 品牌化不纳入迁移计划。改名是一天的工作量，需要时再做。

---

### P0-3: detect.py 过度工程化 —— ROI 极低，风险高

**问题描述：** detect.py 是 100+ 行的技术栈探测模块，支持 6 种语言，能解析 GitHub Actions YAML、检测 monorepo 结构、推断数据库配置。然后 init.sh 还叠加了 claude CLI 的 AI 生成层。

ROI 分析：
- **投入：** 100+ 行 Python + init.sh 集成 + 6 种技术栈的测试覆盖 + claude CLI 集成 + 降级逻辑
- **产出：** 省去用户手动填写 workflow.yaml 的 5 分钟
- **风险极高：** CI YAML 解析是出了名的脆弱（GitHub Actions 的 matrix、reusable workflows、composite actions 都会让解析器出错）。错误的自动探测比不探测更糟 —— 用户以为配好了，跑了半小时才发现 gate 命令不对。

detect.py 的 `_parse_github_actions` 函数会提取所有包含 "test/lint/build/check/tsc" 关键词的 `run` 步骤，然后用 `&&` 拼接。但实际的 CI 配置远比这复杂：
- 有些 step 依赖特定的 matrix 变量
- 有些 step 需要先启动服务（数据库、Redis）
- 有些 step 的命令是 `make ci-test`，看不到底层命令

**战略影响：** 这是典型的 feature creep —— "看起来很酷但实际用户不需要"。需要 openNiuMa 的用户是有一定技术能力的开发者，完全有能力手动填写一份 YAML。自动探测的"魔法"反而让人不信任。

**建议方案：**
1. 砍掉 detect.py 和 init.sh 的 AI 生成流程
2. 提供 4 份 workflow.yaml 模板（node.yaml、go.yaml、python.yaml、rust.yaml），每份带详细注释
3. init.sh 简化为 20 行：创建目录结构 + 交互式选择模板 + 拷贝到位 + 更新 .gitignore
4. 节省的开发时间投入 workflow.yaml 注释质量 —— 好的注释比自动探测能帮助更多用户

---

## P1 应该解决

### P1-1: 竞品差异化没有提炼为产品 Messaging

**问题描述：** 设计文档引用了 OpenAI Symphony 作为技术参考，但完全没有系统性地定义 openNiuMa 在 AI coding 工具谱系中的位置。当前市场格局：

| 工具 | 模式 | 差异 |
|------|------|------|
| Claude Code Agent tool | 单任务 agent | 内置于 Claude，无需额外工具 |
| Cursor / Windsurf | IDE 内 AI 辅助 | GUI 交互，适合单任务 |
| Devin | 全自治 AI SDE | 端到端商业产品 |
| OpenHands / SWE-agent | 开源 AI coding agent | Python 生态，学术倾向 |
| aider | CLI AI pair programming | 成熟社区，单任务模式 |

从设计文档中能提炼的差异化是：批量任务编排 + worktree 隔离并行 + git 工作流深度集成 + 无人值守自治循环。但这些卖点散落在技术细节中，没有被提炼为清晰的一句话。

**战略影响：** 开源项目 README 第一段决定 90% 的人是否继续往下看。如果不能一句话说清"和 X 有什么不同"，潜在用户不会尝试。

**建议方案：**
1. 提炼一句话定位："给它一个 backlog，它并行处理每个任务 —— 独立分支、独立测试、自动 PR、无人值守。"
2. 明确"我们不做什么"：不做 IDE 集成、不做单任务交互、不做 agent 框架 —— 我们是 agent 之上的调度层
3. 对标时强调"batch + parallel + unattended"三个关键词

---

### P1-2: 通知层硬编码飞书 —— 限制可扩展性

**问题描述：** 通知设计了三层：macOS、飞书 Webhook、终端 Bell。飞书是中国特有的 IM。如果未来开源，海外用户需要 Slack/Discord/Teams；国内用户也可能用钉钉或企业微信。

**战略影响：** 不影响当前功能，但硬编码特定服务商的通知协议会让后续扩展需要重构。

**建议方案：**
1. notify.py 抽象为通用 webhook 接口：`url` + `payload_template`
2. 飞书只是一种预置的 payload 模板，与 Slack 等并列
3. 但这是 P1，不阻塞交付。Phase B/C 先做 macOS + Bell 就够了

---

### P1-3: Bash + Python 双语言架构的维护成本被系统性低估

**问题描述：** "Bash 主编排 + Python lib/ 业务逻辑"在技术上合理，但从长期维护角度看有结构性问题：

1. **贡献者门槛翻倍：** 需同时熟悉 Bash 和 Python，且理解 `eval "$(python3 ...)"` 的跨语言调用约定
2. **调试成本高：** 错误可能出在 Bash 层、Python 层、或两者的交互边界（变量转义、退出码传递、编码问题等）
3. **测试盲区：** 设计已承认"Bash 脚本不写单元测试"，但编排核心逻辑（进程管理、信号处理、worktree 操作）全在 Bash 中

实际上 dev-loop.sh 的核心循环 —— 轮询 inbox、调度 worker、处理结果 —— 用 Python 的 `subprocess` + `signal` + `asyncio` 完全能胜任，而且可测试。

**战略影响：** 如果开源，双语言架构会显著降低社区贡献意愿。如果纯内部使用，可以接受但应标注为"过渡架构"。

**建议方案：**
1. 短期（Phase A-C）：接受现状，先完成架构升级
2. 在设计文档中显式标注 dev-loop.sh 的 Bash 核心循环是"技术债/过渡方案"
3. 长期路线图中保留"Python 重写编排核心"的选项

---

### P1-4: "牛马" 命名的文化风险和 SEO 问题

**问题描述：** "牛马"在中文互联网语境中是"打工人/被压榨的劳动力"的自嘲用语。作为开源项目名：

- **国际社区：** "NiuMa" 无语义，无法联想产品功能
- **文化敏感度：** "把 AI 当牛马使"在 AI 伦理讨论日趋敏感的环境下可能引发争议
- **SEO 灾难：** 搜索 "niuma" 会出现大量无关的中文互联网内容，完全无法占领搜索词
- **专业度：** 在技术决策者面前介绍一个叫"牛马"的工具，可能影响严肃性

**战略影响：** 命名是开源项目最持久的决策。改名成本随时间指数增长（文档链接、安装脚本、用户心智、搜索排名）。

**建议方案：**
1. "牛马"可以作为中文社区的昵称/副标题，但正式名建议更中性
2. 考虑英文友好的名字（如 LoopForge、BatchPilot、DevHerd 等）
3. 但命名不阻塞交付 —— Phase 5 之前完全不需要做品牌决策。先把工具做好用。

---

## P2 可以改进

### P2-1: 缺少用户旅程定义

**问题描述：** 设计文档详尽描述了每个模块的技术实现，但缺少用户旅程（User Journey）的完整定义。三个关键场景未被串联：

1. **新用户上手：** 从拷贝 loop/ 到第一个任务完成的完整路径
2. **日常运行：** 启动 loop → 偶尔看 dashboard → 收通知 → 检查结果 → 处理异常
3. **排错场景：** 任务失败 → 看哪个日志 → 理解失败原因 → 重试还是手动修

每个模块单看合理，但用户面对 6 个 Shell 脚本 + dashboard + status + stats + logs 目录时，不知道该看哪个。

**建议方案：** 补充 3 个核心用户旅程流程图，标注每步对应的工具/命令。这能暴露功能之间的衔接缝隙。

---

### P2-2: stats.json 缺少 schema 版本号

**问题描述：** stats.json 没有 `version` 字段。随着迭代 schema 必然变化（已经发生了：新增 `failure_type` 字段）。没有版本号就无法做向后兼容的数据迁移，老数据会静默丢失或报错。

**建议方案：** 顶层加 `"schema_version": 1`，stats.py 加载时检查并迁移。5 行代码的事，值得做。

---

### P2-3: workflow.yaml 缺少配置校验

**问题描述：** workflow.yaml 是核心配置文件，但没有 schema 校验。用户拼写错误（`stall_timeotu_sec`）会被静默忽略并使用默认值。在调试"为什么 stall 检测不生效"时，这种静默失败极其痛苦。

**建议方案：** config.py 加载后对已知字段做白名单检查，遇到未知字段输出 warning。10 行代码，大幅提升调试体验。

---

### P2-4: 缺少迁移回滚策略

**问题描述：** 6 个 Phase 的迁移中如果某 Phase 引入 bug（如 reconcile.py 误杀正常 worker），没有快速回滚手段。设计假设每个 Phase 都能平滑落地。

**建议方案：**
1. 每个 Phase 完成后打 git tag
2. dev-loop.sh 支持环境变量禁用特定模块：`DISABLE_RECONCILE=1`，作为紧急逃生口
3. 回滚 = `git checkout <tag>` 即可回到上一个稳定版

---

### P2-5: Mermaid 进度报告的受众和分发渠道不明

**问题描述：** generate-progress.sh 生成 PROGRESS.md，含 Mermaid 图表、甘特图、Session 矩阵。但给谁看？
- 给自己看：dashboard.sh 已经是实时的，PROGRESS.md 增量价值有限
- 给团队看：需要推送到某处（飞书文档？GitHub Wiki？），但设计没提
- 给社区展示：需要截图/GIF，Mermaid 源码不直观

**建议方案：** 明确受众和分发渠道。如果没有明确场景，降低优先级或从 MVP 中砍掉。

---

### P2-6: claude CLI 作为硬依赖的供应商锁定风险

**问题描述：** claude CLI 是 openNiuMa 的核心运行时依赖 —— 不只是 init.sh 的 AI 生成（这层有降级），而是 dev-loop.sh 调度 worker 时直接调用 `claude` 命令执行编码任务。这意味着 openNiuMa 和 Anthropic 的 claude CLI 产品强绑定。

如果 Anthropic 改变 claude CLI 的接口、定价模型、或速率限制策略，openNiuMa 的核心功能会直接受影响。

**建议方案：** 这不阻塞当前开发，但在架构上预留 provider 抽象的可能性 —— 让 worker 调用的 AI CLI 工具通过 workflow.yaml 配置（默认 `claude`），未来可替换为其他工具。不需要现在实现，但 dev-loop.sh 中不要把 `claude` 硬编码在 20 个地方。

---

## 总体评价

### 优势

1. **架构方向完全正确：** 从内联 Python 迁到 lib/ 模块、配置外置、prompt 模板化 —— 这些是提升可维护性的教科书做法
2. **两版方案的合并策略优秀：** 从 Symphony 取基础设施和可靠性，从 v4 取用户体验和数据沉淀，合并表格清晰展示了每个维度的选型理由
3. **失败分类是真正的差异化能力：** 6 种失败类型 + 按类型差异化重试 + prompt 注入错误上下文 —— 这比"统一重试 3 次"有本质提升，值得作为核心卖点宣传
4. **Worktree 隔离并行的设计成熟：** 保留/复用策略合理，hooks 抽象让 worktree 生命周期管理与项目解耦

### 核心风险

1. **范围失控：** 6 Phase、20+ 新文件、50+ 改名路径。单人维护预计需要 3-6 个月。迁移期间新旧架构并存，维护成本倍增
2. **定位模糊：** 内部工具和开源产品的需求冲突会在每个设计决策中制造摩擦，降低交付速度
3. **过度工程化：** detect.py 的 6 栈探测、init.sh 的 AI 生成、Phase 5 的品牌化改名 —— 这些都是"nice to have"伪装成"must have"

### 建议行动优先级

| 优先级 | 行动 | 目的 |
|--------|------|------|
| 1 | 明确 Phase 0-4 只服务 POI 项目 | 消除定位分裂 |
| 2 | 6 Phase 压缩为 3 Phase，每个 1-2 周 | 缩短半成品状态 |
| 3 | 砍掉 detect.py，用模板替代 | 降低复杂度，提升可靠性 |
| 4 | Phase 5 品牌化延后到有外部需求时 | 避免过早优化 |
| 5 | 品牌命名另开讨论 | 避免阻塞技术交付 |

**一句话总结：** 设计的深度和技术完整度令人印象深刻，合并两版方案的判断力尤其出色。但最大的风险不是"设计不够好"，而是"想做的太多"。建议用"一个人两周内能交付什么"来反推每个 Phase 的范围 —— 一个两周内交付的可靠版本，远胜一个三个月后才完成的完美版本。
