# Round 1 — 架构师 + PM 评审

## 架构师评审

### 认可点
- **渐进式 Bash→Python 迁移策略合理**：保留 fallback 的做法降低了迁移风险，避免 big-bang 重写
- **配置解耦设计清晰**：将项目特定配置与框架默认值分离，`openniuma init` 交互生成，这是正确的方向
- **Prompt 模板标准化（Phase 4.1）有前瞻性**：用 frontmatter 定义元数据 + 本地覆盖 + 社区安装，这个设计兼顾了灵活性和可控性

### 问题
- **[P1]** dev-loop.sh 重写放在 Phase 0 但标注"最大工程量"，实际执行时会阻塞整个 Phase 0 — 1,700 行 Bash 重写不是 1-2 周能完成的事情，会拖垮整个时间线
- **[P1]** 缺少 `workflow.yaml` schema 版本化机制 — 方案提到 1.0.0 时锁定 schema，但没有设计 schema 迁移路径。用户升级 openNiuMa 时旧 workflow.yaml 怎么办？没有 migration 策略会导致升级恐惧
- **[P1]** prompts/ 放在包外部（顶层目录）而非 `src/openniuma/` 内部 — pip install 后 prompts 文件不会被包含在安装包中，用户拿不到内置 prompt 模板
- **[P2]** 目录结构过度设计 — `src/openniuma/orchestrator/` 拆了 loop.py / worker.py / worktree.py 三个文件，但 dev-loop.sh 是高度耦合的（worker 生命周期 = worktree 生命周期 = 循环迭代），强行拆分可能增加复杂度而非降低
- **[P2]** 没有考虑 Claude Code 之外的 AI Agent 支持 — 方案全文绑定 Claude Code，但编排器的核心逻辑（worktree 管理、状态机、失败重试）与具体 AI Agent 无关。缺少 Agent 抽象层会限制项目的受众

### 建议
- dev-loop.sh 重写应从 Phase 0 移到 Phase 1.5 或 Phase 2，Phase 0 只做最小解耦：Python CLI 壳 + Bash 核心不变
- 增加 `workflow.yaml` 的 `schema_version` 字段 + `openniuma migrate` 命令
- prompts/ 应放在 `src/openniuma/prompts/` 内，通过 `importlib.resources` 访问
- 预留 Agent 抽象接口（即使首版只实现 Claude Code adapter），在 workflow.yaml 中加 `agent.provider: claude-code`

---

## PM 评审

### 认可点
- **差异化定位清晰**："Claude Code = 程序员，openNiuMa = 项目经理"——这个比喻精准，易于传播
- **发布里程碑表（Phase 5.1）务实**：从内部验证到正式发布有明确的渐进节奏
- **"15 分钟上手"作为 Phase 2 目标**：把用户体验量化为时间指标是对的

### 问题
- **[P1]** 缺少核心用户画像 — 方案假设"用户是开发者"但没有细分。单人开发者？Tech Lead 管理团队？开源项目维护者？不同用户的痛点和入口完全不同，这会影响 README 的叙事和 Phase 2 文档的重心
- **[P1]** 竞品分析过于简化 — 只对比了 Claude Code 和 Codex CLI。实际上竞品还包括 GitHub Copilot Workspace、Devin、SWE-agent、aider 等。缺少对这些工具的差异化分析会让定位模糊
- **[P2]** Phase 0-5 跨度太大，缺少成功指标 — 每个 Phase 有"目标"但没有可衡量的成功标准。比如 Phase 2 "15 分钟上手"怎么验证？Phase 5 "3+ 个项目验证"谁来验证？
- **[P2]** 社区建设策略（Phase 5.2）缺乏具体的种子用户获取计划 — "视频演示"和"GitHub Discussions"是工具，不是策略。第一批 10 个用户从哪来？
- **[P3]** 示例项目（Phase 5.2）说提供 Node/Python/Go 三个，但 openNiuMa 自己只在 POI（Node）项目验证过，其他技术栈的 example 缺乏真实验证

### 建议
- 定义 2-3 个核心 Persona，并标注优先级（建议首要 Persona：使用 Claude Code 的个人开发者）
- 竞品分析扩展为专门章节，重点分析 Devin / SWE-agent / aider 的差异
- 每个 Phase 增加 "Done = ..." 定义，如 Phase 2: "Done = 3 个非作者用户独立按 README 完成首次任务入队"
- 种子用户计划：先在 Claude Code Discord / Hacker News / V2EX 发技术文章引流

---

## 方案修订 v2

基于 Round 1 评审，以下问题必须处理：

### P1 修复

1. **dev-loop.sh 重写时机调整**
   - Phase 0 改为"最小解耦"：Python CLI 壳 + 配置解耦 + 仓库拆分，Bash 核心保持不变
   - 新增 Phase 1.5 "核心重写"：dev-loop.sh → Python，独立阶段，不阻塞分发和文档
   - 修改 Next Steps 第 8 步的优先级

2. **workflow.yaml schema 版本化**
   ```yaml
   schema_version: 1          # 新增必填字段
   project:
     name: "My Project"
     ...
   ```
   - `openniuma migrate` 命令：读取旧 schema，自动升级到最新版本
   - schema 变更记录在 CHANGELOG 中

3. **prompts 打包进 Python 包**
   ```
   src/openniuma/
     prompts/                  # 移入包内
       _common-rules.md
       fast-track.md
       ...
   ```
   - 通过 `importlib.resources` 读取内置 prompt
   - 用户可在项目根目录 `.openniuma/prompts/` 覆盖

4. **核心用户画像**
   - **Primary**: 使用 Claude Code 的个人开发者（1-3 人团队，想把重复性开发任务自动化）
   - **Secondary**: Tech Lead（管理 5-10 人团队，想让 AI 处理 backlog 中低优先级任务）
   - **Tertiary**: 开源项目维护者（用 openNiuMa 处理 good-first-issue 类 PR）

5. **竞品分析扩展**
   新增专门章节，覆盖 Devin / SWE-agent / aider / Copilot Workspace：
   | 维度 | openNiuMa | Devin | SWE-agent | aider |
   |------|-----------|-------|-----------|-------|
   | 本地/云 | 本地 | 云 | 本地 | 本地 |
   | 并行 | 5 workers | 1 | 1 | 1 |
   | 透明度 | 完全透明（git worktree） | 黑盒 | 部分 | 透明 |
   | 生命周期 | 设计→PR | 需求→部署 | Issue→PR | 对话→commit |
   | 定价 | 免费(自带 API key) | $500/月 | 免费 | 免费 |

### P2 处理

- **目录结构**：采纳。orchestrator/ 暂不拆分，先用单文件 `orchestrator.py`，复杂后再拆
- **Agent 抽象层**：采纳。在 workflow.yaml 中增加 `agent.provider: claude-code`，预留接口但首版不做多 Agent 支持
- **成功指标**：采纳。每个 Phase 增加 "Done Criteria"
- **种子用户计划**：采纳。加入 Phase 5

### P3 处理
- **示例项目验证**：合理担忧，但 Phase 5 时再解决。标注为"需在发布前用真实项目验证"
