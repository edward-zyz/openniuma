# PM-1 评审：用户体验与产品设计

> 评审对象：openNiuMa 合并升级设计（Symphony + v4 merged-design）
> 评审视角：资深产品经理，8 年 B2D 产品经验，专注开发者工具和 DevOps 产品
> 日期：2026-03-27

---

## P0 必须解决

### P0-1: 首次运行体验存在"信心裂谷"——用户无法验证 init.sh 探测结果的正确性

**问题描述：** init.sh 的三层探测策略（确定性探测 -> AI 生成 -> 默认值）在技术上很巧妙，但从用户体验角度看有一个致命缺陷：用户无法判断自动生成的配置是否正确。`detect.py` 从 package.json scripts 拼装 gate_command，从 .env.example 推断数据库配置，从 CI 文件提取测试命令——这些探测结果可能 90% 正确，但那 10% 的错误（比如漏掉了一个关键的 typecheck 步骤，或者数据库连接字符串的端口不对）会导致任务在运行几十分钟后才失败。用户此时的感受是"工具不靠谱"，而非"我的配置有问题"。

**用户影响：** 自动生成给用户一种"不需要检查"的错觉。当自动探测出错时，排查成本极高——用户需要理解 workflow.yaml 的每个字段含义才能定位问题。首次使用的信任就此崩塌。

**建议方案：**
1. init.sh 完成后，必须执行一次"dry-run 验证"：运行 gate_command 确认能通过，运行 after_create hook 确认能执行，验证不通过时直接提示具体哪里有问题而非静默完成。
2. 输出一个结构化的"探测结果确认表"，让用户一眼看到探测了什么、用了什么值、来源是什么（CI 文件/package.json/默认值），标记置信度。置信度低的项用黄色高亮，提示用户确认。
3. 增加 `init.sh --dry-run` 选项，只探测并输出结果，不写入文件。让谨慎型用户先看再决定。

### P0-2: 7 个独立脚本的认知负担远超"拷贝即用"的产品承诺

**问题描述：** 产品定位是"拷贝即用"，但 Phase 0-4 的实际交付是 7 个独立脚本（dev-loop.sh、dashboard.sh、stats.sh、notify.sh、add-task.sh、status.sh、generate-progress.sh），外加 workflow.yaml、prompts/ 目录、stats.json、.env 等配置/数据文件。用户打开 loop/ 目录看到 15+ 个文件和目录，第一反应不是"简单"而是"好多东西"。

更具体的问题是：想看进度有三种方式（dashboard.sh 彩色看板、status.sh 纯文本、generate-progress.sh Mermaid 报告），功能高度重叠但使用场景区分不明确。用户的核心问题始终只有一个："现在啥情况？"——却需要在三个命令里选。

**用户影响：** 开发者工具的核心竞争力是"让复杂变简单"。7 个脚本 + 区分不清的功能定位，传递的信号恰恰是"这个工具本身就很复杂"。这直接违背了产品定位。

**建议方案：**
1. 从 Phase 0 开始就提供统一入口 `loop.sh`（20 行路由脚本），支持 `loop.sh start/status/dashboard/stats/add/stop`。独立脚本保留但降级为"内部实现细节"，文档中只推荐统一入口。
2. 合并 status.sh 和 dashboard.sh 为一个命令：检测 TTY 时自动用彩色，pipe 时自动用纯文本（与 git、ls 等标准 CLI 行为一致）。减少一个用户需要了解的概念。
3. generate-progress.sh 不应作为用户直接使用的命令，而是 dev-loop.sh 内部自动调用的辅助功能。用户想看进度就用 status/dashboard，想分享就 `loop.sh status --format markdown`。

### P0-3: add-task.sh 的"一句话入队"不足以支撑有质量的任务分配

**问题描述：** `bash loop/add-task.sh "支持自定义热力图半径"` 看起来很便捷，但产生的任务 .md 几乎是空的——只有标题和一个空的"需求描述"节。这样的任务文件被 Claude 拿到后，缺少足够的上下文来做出好的技术决策。设计文档中的示例任务（热力图刷新、评分不一致调试、移动训练入口等）都是需要具体约束条件的真实工程任务，一句话描述远远不够。

更重要的是，设计中缺少对"任务复杂度如何影响路径选择"的机制。用户给了一句话，系统如何决定走 FAST_TRACK 还是 DESIGN_IMPLEMENT？如果完全依赖 Claude 在运行时判断，那这个判断的质量很大程度上取决于任务描述的充分程度——而 add-task.sh 偏偏生成了最简描述。

**用户影响：** 便捷的入队方式产生低质量的任务输入，低质量输入导致低质量的开发输出，用户认为工具能力不行。这是一个负向飞轮。

**建议方案：**
1. `add-task.sh` 应根据 `--complexity` 生成分级模板：低复杂度(标题即可)、中等(需求+验收标准)、高(需求+技术约束+涉及模块+验收标准+非功能需求)。默认 complexity=medium。
2. 增加 `--ai` 模式：给一句话后自动调用 claude 分析项目代码库，补充完整的任务描述、预估复杂度、推荐路径、涉及文件。这能将输入质量提升一个数量级。
3. `--from-issue` 模式要利用 GitHub Issue 的 body/labels/comments 全部内容，而非只取 title。Issue 中往往有丰富的讨论和上下文。

---

## P1 应该解决

### P1-1: 通知系统的"推"模式缺少"静默时段"和"去重"机制

**问题描述：** 通知设计有三个渠道（macOS、飞书、Bell）和四个级别（debug/info/warn/critical），看起来完整。但缺少两个在实际使用中必不可少的能力：

(a) 静默时段：用户晚上 11 点启动一批任务跑过夜，凌晨 3 点任务陆续完成，每完成一个就发一条飞书通知，手机响个不停。
(b) 去重/聚合：一个任务如果 gate 失败后重试 3 次才成功，会产生 3 条 warn + 1 条 info = 4 条通知。如果 5 个任务同时重试，就是 20 条。

**用户影响：** 通知疲劳。飞书消息密集到用户直接屏蔽群聊，此后真正的 critical 事件也看不到了。这是通知系统最常见的死因。

**建议方案：**
1. workflow.yaml 增加 `notify.quiet_hours: "23:00-08:00"`，静默时段内只推 critical，info/warn 缓存到早上 8 点一次性发送摘要。
2. 增加聚合窗口：同一任务 10 分钟内的多条 warn 合并为一条（"#57 连续 3 次 gate 失败，正在重试"），而非每次失败都发。
3. 飞书通知默认级别改为 `warn`。info 级别只走 macOS 本地通知和终端 Bell。全部完成的最终摘要不受级别过滤，始终发送。

### P1-2: 失败恢复中"上下文耗尽"的断点续传缺少具体机制

**问题描述：** 失败分类表中 `context`（上下文耗尽）的策略是"清空进度，断点续传新会话"，但设计文档没有说明"断点"具体指什么、如何让新会话知道前一个会话做到哪里了。`implement_progress` 中有 `current_chunk` 和 `current_task` 和 `last_commit_sha`，但这些信息如何传递给新的 Claude session？新 session 怎么知道"chunk 1-3 已经做完了，从 chunk 4 开始"？

这不是小事——上下文耗尽意味着任务本身就很大，需要多轮 session 完成。如果断点续传机制不可靠，大任务就永远完不成。

**用户影响：** 大任务（高复杂度、多 chunk）的完成率直接受影响。用户会发现小任务跑得很顺、大任务总是卡在中间，最终被迫手动拆分任务。

**建议方案：**
1. 明确"断点续传"的 prompt 模板：新 session 收到的 prompt 中应包含(a)已完成 chunk 的 git log/diff 摘要、(b)当前 chunk 进度、(c)剩余 chunk 列表、(d)"请从 chunk N 继续"的明确指令。
2. 断点续传不应"清空 implement_progress"，而应保留 current_chunk 以上的进度标记，只清空当前 chunk 的中间态。
3. 增加一个安全阀：如果同一个 chunk 连续 2 次上下文耗尽，说明这个 chunk 本身太大，通知用户手动拆分。

### P1-3: workflow.yaml 配置项缺少分组和"常用 vs 高级"的视觉区隔

**问题描述：** 完整 workflow.yaml 有 9 个顶级节（project、hooks、polling、workers、retry、failure、worktree、prompts、notify），30+ 配置项。所有配置项平铺在同一层级，没有视觉上的"重要性"区分。用户打开文件后无法快速识别"哪些是我需要关注的、哪些是默认就好"。

**用户影响：** 配置焦虑。新用户面对 30+ 项配置，要么不敢动（即使某些确实需要调整），要么花 30 分钟研究每一项（浪费时间）。

**建议方案：**
1. init.sh 生成的 workflow.yaml 分为两部分：上半部"项目配置"（project + hooks + notify），下半部"引擎配置"（其余所有），中间用 `# ====== 高级配置（通常不需要修改）======` 分隔。
2. 每个配置项旁加一行内联注释说明合理范围和默认值含义，例如 `stall_timeout_sec: 1800  # 建议 900-3600，单位秒，worker 日志超时即判定卡死`。
3. 后续 Phase 5 提供 `niuma config edit` 交互式配置编辑（类似 git config --edit 但更友好），不要求用户直接编辑 YAML。

### P1-4: Dashboard 在标准终端高度下存在溢出风险

**问题描述：** Dashboard 渲染了 8 个区域（Header + Progress + Task List + Pipeline + Checkpoint + Workers + Recent Sessions + Stats）。以 4 个任务 + 2 个 worker 的中等场景估算，渲染输出大约 50-65 行。标准终端高度 40-50 行。watch 模式下 clear + 重绘，超出部分直接看不到。

**用户影响：** 用户打开 dashboard -w 后发现底部被截断，关键信息（Stats、Workers 状态）看不到，需要禁用 watch 模式单次渲染后手动滚屏。失去了实时监控的价值。

**建议方案：**
1. dashboard.sh 启动时检测终端高度 `tput lines`，如果行数不足，自动降级为 compact 模式（只渲染 Header + Progress + 告警/异常 + 活跃 Workers）。
2. 支持 `--compact` / `--full` 手动切换。默认 compact，`--full` 时不限制。
3. 告警信息（失败、blocked、stall）无论模式都置顶显示，用红色/黄色高亮。正常运行时这个区域只显示一行 "All systems operational"。

---

## P2 可以改进

### P2-1: Mermaid 进度报告（generate-progress.sh）缺少明确的消费场景和分发路径

**问题描述：** PROGRESS.md 生成了丰富的 Mermaid 图表（任务状态图、阶段流水线、Session 甘特图），但这些图表的消费路径很模糊。文档中提到"飞书/GitLab 分享"，但飞书消息不直接渲染 Mermaid，需要贴到飞书文档的 Mermaid block 中；GitHub PR 描述可以渲染但需要手动复制粘贴。没有自动化的"生成 -> 分发 -> 展示"闭环。

**用户影响：** PROGRESS.md 大概率成为 .gitignore 中一个永远没人看的文件。开发资源投入产出比低。

**建议方案：**
1. 如果消费场景是 GitHub，增加 `--update-pr` 选项，自动通过 `gh` CLI 更新 PR body 中的进度区域（用 HTML comment 标记区域边界）。
2. 如果消费场景是飞书，在 notify.py 的全部完成通知中嵌入文本版进度摘要，不依赖 Mermaid。
3. 将此功能优先级下调到 Phase 4 或 Phase 5，先做 dashboard 和 stats 就足够满足可观测性需求。

### P2-2: 可移植性声称支持 6 种技术栈但缺少验证

**问题描述：** detect.py 为 Node.js、Go、Rust、Python、Ruby、Java 都定义了探测规则，但目前只在 POI（Node.js monorepo）项目上经过实际验证。Go 项目的 `go test ./... && go vet ./...` 是否足够？Rust 项目 `cargo fetch` 在 worktree 中能否正常工作（workspace 依赖可能有路径问题）？Python 项目的虚拟环境如何处理？这些都未经测试。

**用户影响：** 用户在非 Node.js 项目上尝试后发现各种问题，"拷贝即用"变成"拷贝后调两天配置"，声誉受损。

**建议方案：**
1. Phase 0 完成后，在 3 个不同技术栈的开源项目上做端到端测试：一个 Go 项目、一个 Python 项目、一个 Rust 项目。记录需要的手动调整步骤。
2. detect.py 的探测结果中标注置信度（high/medium/low），低置信度时提示用户"建议手动检查此配置项"。
3. 文档中诚实地标注"经过验证的技术栈"和"理论支持的技术栈"，而非把 6 种技术栈等同宣传。

### P2-3: stats.json 单文件存储在并行模式下的可靠性隐患

**问题描述：** 所有运行数据集中在 stats.json 一个文件中。并行模式下 5 个 worker 可能同时触发 stats.py 写入。虽然 state.py 有文件锁机制，但文档没有明确 stats.py 是否使用相同的锁保护。此外，长期运行（100+ 任务、500+ sessions）后 JSON 文件膨胀，每次读写都要解析整个文件。

**用户影响：** 低概率数据丢失（写冲突），高概率性能下降（大文件解析慢导致 dashboard 刷新卡顿）。

**建议方案：**
1. 文档中明确 stats.py 必须使用与 state.py 相同的文件锁（或 stats.json 专用锁），并在 lib/ 的测试中覆盖并发写入场景。
2. 增加数据归档机制：`stats.py archive --before 7d` 将 7 天前的数据移到 stats.archive.json，保持主文件精简。
3. 长远看，考虑 SQLite 替代 JSON（Python 标准库自带 sqlite3）。SQLite 天然支持并发读写，且查询能力远强于 JSON 遍历。

### P2-4: PyYAML 外部依赖与"零额外依赖"的产品承诺矛盾

**问题描述：** 设计文档多处强调"零额外依赖"（bash 4+, jq 1.7+, python3 3.8+ 均为 macOS 自带），但 workflow.yaml 解析依赖 PyYAML，这不是 macOS 标准库。init.sh 中的 `pip3 install pyyaml -q` 在以下场景会失败：(a) 企业网络需要代理，(b) 系统 Python 被 IT 锁定权限，(c) 用户使用 pyenv/conda 管理多版本 Python。

**用户影响：** "零依赖"的承诺在第一步就破产。对于注重开发环境纯净性的开发者（Go/Rust 社区尤其明显），被要求安装一个 Python 包是减分项。

**建议方案：**
1. 编写一个 200 行以内的简易 YAML parser（只需要支持 workflow.yaml 用到的子集：标量、映射、多行字符串，不需要锚点/别名/flow style）。将其作为 lib/yaml_lite.py 内置。
2. 运行时优先尝试 `import yaml`，失败则 fallback 到 yaml_lite.py。对用户完全透明。
3. 如果不想维护自己的 parser，至少将 PyYAML 的 .py 文件直接 vendor 到 lib/ 中（PyYAML MIT 许可），避免 pip install。

### P2-5: Phase 5 品牌化改名的时机选择存在矛盾

**问题描述：** Phase 5 计划将 loop/ 目录改名为 openniuma/，6 个脚本统一为 niuma.sh 子命令。但设计同时强调"可移植性"和"拷贝即用"——这意味着在 Phase 5 之前已经有用户把 loop/ 拷贝到自己的项目中使用了。Phase 5 的改名对这些早期用户来说是破坏性变更（目录路径变了、脚本名变了、crontab/alias 全部失效）。

**用户影响：** 早期采用者承受了最高的迁移成本，这恰恰是最不应该伤害的用户群体。

**建议方案：**
1. 如果确定要改名，从 Phase 0 开始对外文档就用 openNiuMa 品牌，但技术实现暂时保留 loop/ 目录名。Phase 5 改名时提供 `niuma migrate` 一键迁移脚本。
2. 或者反过来：不改名。loop/ 作为技术目录名，openNiuMa 作为品牌名，两者可以共存（就像 Docker 的技术实现叫 containerd/runc 但品牌叫 Docker）。不是所有东西都需要统一命名。
3. 如果一定要统一，现在就做，不要等到 Phase 5。改名的成本与用户基数成正比，越早改代价越小。

---

## 总体评价

### 优势

1. **痛点识别精准且排序合理。** 可观测性、数据沉淀、通知、失败恢复、入队便利性——这五个方向切中了 AI 自治编排器从"能跑"到"好用"的关键差距。优先顺序也符合用户价值曲线（先能看到、再能量化、然后才是自动化恢复）。

2. **Symphony + v4 的合并决策有说服力。** 每个维度的取舍都有技术理由，不是简单折中。特别是"v4 的 6 类失败分类 >> Symphony 的统一重试"和"Symphony 的模块化架构 >> v4 的内联 Python"这两个决策非常正确。

3. **可移植性设计的完成度出乎意料。** 引擎/配置分离、init.sh 三层探测、workflow.yaml hooks、detect.py 6 种技术栈——从设计层面已经是一个可以独立发布的开发者工具了。特别是 hooks 机制（after_create/before_remove）足够灵活，能覆盖各种项目的特殊需求。

4. **数据流总览图清晰。** 从 add-task.sh -> inbox -> dev-loop.sh -> stats.json/notify.sh -> dashboard.sh 的完整数据流一目了然。这说明架构思考是系统性的，不是拼凑。

### 风险

1. **MVP 范围过大。** Phase 0 已经包含了 12 个新文件、workflow.yaml schema、prompts 模板化、init.sh + detect.py。如果把这些全做完才算 Phase 0 交付，周期容易失控。建议将 Phase 0 进一步拆分：0a = lib/ 模块提取（纯重构，不加新功能），0b = workflow.yaml + prompts 模板化，0c = init.sh + detect.py。

2. **缺少"dogfooding 计划"。** 设计基于 POI 项目的使用经验，但"可移植性"的核心假设——"拷贝到另一个项目也能跑"——没有验证计划。建议在 Phase 0 完成后，至少在 2 个不同技术栈的项目上做完整的端到端测试。

3. **对用户反馈循环的考虑不足。** 设计文档详尽地描述了每个模块"做什么"和"怎么做"，但没有提到"怎么知道做对了"。建议每个 Phase 完成后设定 2-3 个可量化的成功指标（例如：init.sh 在 3 种技术栈上零手动修改成功率、dashboard 首屏信息覆盖率、通知打开率等）。

### 产品建议

1. **立即建立统一 CLI 入口。** 不要等到 Phase 5。从 Phase 0 开始就提供 `loop.sh <子命令>` 作为唯一推荐的用户交互方式。这是所有后续体验优化的基础——用户只需要记住一个命令。

2. **通知应比 Dashboard 更早交付。** 对于"放任务跑过夜"的核心场景，推送通知 >> 需要主动打开的 Dashboard。建议将 notify 模块提前到 Phase 1（与 dashboard 同期或更早），让"无人值守"场景尽快可用。

3. **为"大任务拆分"提供产品化的解决方案。** 当前设计假设用户会自行将大需求拆成合适粒度的子任务放入 inbox，但这本身就是一个有门槛的操作。建议在 add-task.sh 的 `--ai` 模式中加入"自动拆分"能力：用户给一个大需求描述，AI 分析后建议拆分为 N 个子任务，用户确认后批量入队。这能极大降低使用门槛。

4. **品牌化时机应前置到首次对外发布前。** 如果有公开发布计划，loop -> openNiuMa 的改名必须在此之前完成。早期的用户认知一旦建立就很难改变。建议 Phase 0 对外文档就使用 openNiuMa 品牌，技术目录名可以暂缓。

5. **考虑提供"观察者模式"的 Web Dashboard。** 当前所有可观测性工具都是终端内工具，这限制了团队协作场景。如果未来定位从个人工具走向团队工具，一个只读的 Web Dashboard（哪怕只是 `python3 -m http.server` 托管一个静态 HTML + 定时刷新 status.json）会极大扩展产品的适用范围。这不需要在 Phase 0-4 做，但应该在架构上预留空间（比如 stats.py/status.py 都支持 JSON 输出格式）。
