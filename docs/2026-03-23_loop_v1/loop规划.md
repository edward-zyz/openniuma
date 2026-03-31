# imCTO — Market Requirements Document (MRD)

> Version: v0.1 | Date: 2026-03-27 | Owner: Edward Zhang

---

## 1. 产品定义

**imCTO** 是一个开源的 AI 自治研发循环框架，通过 Shell 编排脚本驱动 Claude Code CLI，实现无人值守的 backlog 逐条交付。

一句话：**把你的 backlog 交给 AI，睡一觉起来收 MR。**

### 命名由来

"imCTO" = "I'm CTO" — 寓意每个开发者都能拥有一个 7x24 不休息的虚拟 CTO，帮你做架构设计、编码实现、代码审查、质量把关和合入管理。

---

## 2. 市场背景

### 2.1 行业趋势

AI 编码工具（Cursor、GitHub Copilot、Claude Code）已从"辅助补全"进入"自主编码"阶段。但当前的 AI 编码体验存在一个关键断层：

| 阶段 | 能力 | 现状 |
|------|------|------|
| 单次对话 | AI 写一个函数/修一个 bug | 成熟，多产品竞争 |
| 单功能交付 | AI 从需求到 MR 完成一个完整功能 | 零散实践，无标准化工具 |
| 批量交付 | AI 自动消化 backlog，逐条交付 | **空白地带** |

imCTO 填补的正是从"单功能交付"到"批量自治交付"的空白。

### 2.2 竞品分析

| 产品 | 定位 | 与 imCTO 的差异 |
|------|------|----------------|
| **Claude Code** | AI 编码 CLI 工具 | imCTO 的运行时引擎，不是竞品而是基座 |
| **Devin** | 全自治 AI 工程师 | 闭源 SaaS，按量付费，无法定制流程；imCTO 开源可控 |
| **OpenHands** | 开源 AI 软件工程师 | 侧重单任务执行；imCTO 侧重多任务编排和质量保障循环 |
| **SWE-Agent** | 学术研究导向 | 聚焦 issue 修复基准评测；imCTO 面向生产级持续交付 |
| **Cursor/Windsurf** | IDE 内 AI 辅助 | 交互式，依赖人在回路；imCTO 无人值守 |
| **GitHub Copilot Workspace** | 从 issue 到 PR | 绑定 GitHub 生态，流程固定；imCTO 平台无关，流程可编排 |

### 2.3 核心差异化

1. **流程可编排，不是黑盒** — 用户可以自定义 Phase 流转、门禁命令、prompt 模板
2. **角色隔离靠会话边界** — 设计者、开发者、审查者是不同的 AI 会话，天然无法"放水"
3. **质量靠自动化门禁而非 AI 自觉** — lint/test/build/typecheck 是硬性关卡
4. **状态持久化 + 断点续传** — 中断后从上次 checkpoint 恢复，不浪费已完成的工作
5. **开源 + 项目级定制** — 门禁命令、分支策略、prompt 模板全部随项目走

---

## 3. 目标用户

### 3.1 用户画像

| 画像 | 描述 | 痛点 | imCTO 价值 |
|------|------|------|-----------|
| **独立开发者 / 小团队 CTO** | 1-5 人团队，技术负责人兼写代码 | backlog 堆积，没时间做"不紧急但重要"的事 | 下班后让 imCTO 消化 backlog，早上收 MR |
| **开源项目维护者** | 维护有一定用户量的开源项目 | issue 堆积，contributor PR 质量参差 | 自动处理 good-first-issue，生成高质量 PR |
| **AI 工程实践探索者** | 对 AI 编码工作流感兴趣的技术人 | 想构建自治研发循环但不知从何下手 | 开箱即用的框架 + 详细的适配指南 |
| **企业研发效能团队** | 负责 DevOps / 研发效率提升 | 想引入 AI 但担心质量和安全 | 门禁机制保障质量，dev 分支隔离保障安全 |

### 3.2 使用场景

**场景 A：下班后批量交付**
> 周五下班前，把 5 个中等复杂度的 backlog 项放入 inbox，启动 imCTO。周一早上打开电脑，看到 3 个 MR 等待审核，1 个标记为 BLOCKED 需要人工介入，1 个正在 VERIFY 阶段。

**场景 B：技术债清理**
> 把 20 个"简单但没人愿意做"的技术债（升级依赖、添加类型注解、抽取公共组件）丢给 imCTO。低复杂度任务走 FAST_TRACK，一个晚上清理大半。

**场景 C：新项目适配**
> fork imCTO，修改 `loop/config.yaml` 中的门禁命令和分支策略，调整 prompt 模板中的项目上下文。30 分钟完成适配，开始消化 backlog。

---

## 4. 产品架构

### 4.1 核心概念

```
┌─────────────────────────────────────────────────────────┐
│                        imCTO                             │
│                                                          │
│   inbox/        backlog.md        loop-state.json        │
│   ┌─────┐      ┌──────────┐      ┌──────────────┐       │
│   │ .md │ ──── │ 任务队列  │ ──── │  状态机       │       │
│   │ .md │ 热插入│ 自动生成  │      │  断点续传     │       │
│   └─────┘      └──────────┘      └──────┬───────┘       │
│                                          │               │
│   ┌──────────────────────────────────────┘               │
│   │                                                      │
│   ▼         编排脚本 (dev-loop.sh)                        │
│   ┌─────────────────────────────────────┐                │
│   │  DESIGN → IMPLEMENT → VERIFY → MERGE │  ◄── Phase    │
│   │     │         │          │        │  │     状态机     │
│   │     ▼         ▼          ▼        ▼  │               │
│   │  Session   Session    Session  Session│  ◄── 会话     │
│   │  (架构师)  (开发者)   (审查者) (合入)  │     角色隔离  │
│   └─────────────────────────────────────┘                │
│              │                                           │
│              ▼                                           │
│   ┌─────────────────────┐                                │
│   │   Claude Code CLI    │  ◄── 运行时引擎                │
│   │   (claude -p)        │                                │
│   └─────────────────────┘                                │
│              │                                           │
│              ▼                                           │
│   ┌─────────────────────┐                                │
│   │   项目代码仓库        │                                │
│   │   Git + CI 门禁       │                                │
│   └─────────────────────┘                                │
└─────────────────────────────────────────────────────────┘
```

### 4.2 Phase 状态机

```
                        ┌── 低复杂度 ──→ FAST_TRACK ──┐
                        │                              │
INIT → 取任务 ──────────┤                              ├──→ VERIFY ──→ MERGE ──→ 下一个
                        │                              │       │
                        └── 中/高复杂度 → DESIGN → IMPLEMENT ──┘       │
                                                                      │
                                                       ◄── FIX ◄─ FAIL
                                                       ◄── MERGE_FIX ◄─ CI FAIL
```

| Phase | 角色 | 职责 | 产出 |
|-------|------|------|------|
| INIT | 编排器 | 创建 dev 集成分支，初始化状态 | dev 分支 |
| DESIGN | 架构师 | 读需求 → 探索代码 → 输出 spec + plan | spec + plan 文档 |
| IMPLEMENT | 开发者 | 按 plan 的 Chunk/Task/Step 执行 TDD 开发 | feature 分支 + 代码 |
| VERIFY | 审查者 | 需求覆盖审查 + 自动化门禁 + diff 审查 | review 报告 |
| FIX | 修复者 | 修复 VERIFY 发现的 Critical/Major 问题 | 修复提交 |
| MERGE | 合入者 | feature → dev 合并 + CI 验证 | dev 分支更新 |
| FINALIZE | 编排器 | 清理 + 创建汇总 MR | 待人工审核的 MR |
| FAST_TRACK | 全能 | 低复杂度任务单会话完成 DESIGN+IMPLEMENT | 代码 + 直接进入 VERIFY |

### 4.3 核心机制

#### 会话角色隔离

每个 Phase 启动一个全新的 Claude Code 会话（`claude -p`），天然隔离上下文：
- 架构师看不到实现细节 → 不会因实现难度妥协设计
- 审查者看不到开发过程 → 不会因"我写的"而放水
- 修复者只看到问题清单 → 聚焦修复而非争辩

#### 自动化质量门禁

每个 Phase 切换前必须通过项目定义的门禁命令（lint、test、typecheck、build），exit code 非 0 则阻塞流转。门禁命令由用户在配置中定义，imCTO 不做任何假设。

#### 状态持久化 + 断点续传

`loop-state.json` 记录完整的执行状态，包括：
- 当前 Phase 和任务 ID
- 功能分支名和 spec/plan 路径
- IMPLEMENT 阶段的 Chunk/Task 级别 checkpoint
- 重试计数和完成记录

任何中断（手动停止、进程崩溃、token 耗尽）后重新启动，自动从上次 checkpoint 恢复。

#### 热插入任务

运行期间随时向 `inbox/` 目录添加 `.md` 文件，编排器在每轮会话启动前自动扫描、入队、生成 backlog。创建 `inbox/STOP` 文件优雅停止循环。

---

## 5. MVP 范围

### 5.1 Phase 1: Core（v0.1）

**目标**：开源发布最小可用版本，验证核心价值。

| 功能 | 说明 | 优先级 |
|------|------|--------|
| 编排脚本 | `dev-loop.sh`，驱动完整 Phase 状态机 | P0 |
| 状态管理 | `loop-state.json` 读写 + 断点续传 | P0 |
| 任务队列 | inbox 热插入 + backlog 自动生成 | P0 |
| Phase prompt 模板 | DESIGN/IMPLEMENT/VERIFY/FIX/MERGE/FAST_TRACK | P0 |
| 项目适配指南 | 从零适配一个新项目的完整教程 | P0 |
| 配置文件 | `imcto.yaml` 定义门禁命令、分支策略、prompt 变量 | P0 |
| 终端 Dashboard | 实时查看循环进度和任务状态 | P1 |
| Progress 报告 | Markdown + Mermaid 进度报告自动生成 | P1 |
| 示例项目 | 一个 Node.js + Vue 项目的完整 imCTO 配置 | P1 |

### 5.2 Phase 2: Polish（v0.5）

| 功能 | 说明 |
|------|------|
| `imcto init` | 交互式初始化，生成项目配置 |
| `imcto run` | 启动循环（替代直接调 bash） |
| `imcto status` | 查看当前循环状态 |
| `imcto inject <file>` | 将任务文件注入 inbox |
| Prompt 模板文件化 | `prompts/{phase}.md`，用户可自定义覆盖 |
| 门禁分层 | 快速门禁（每 Task）+ 完整门禁（每 Phase 切换） |
| 上下文预读 | 编排器预读 spec/plan 内联到 prompt |
| 结构化事件日志 | `events.jsonl`，支持统计分析 |
| CI 平台集成 | GitLab CI / GitHub Actions pipeline 状态轮询 |

### 5.3 Phase 3: Scale（v1.0）

| 功能 | 说明 |
|------|------|
| 多 LLM 后端 | 支持 Claude Code、Codex CLI、Aider 等 |
| 有限并行 | 无依赖任务双槽位交替推进 |
| Web Dashboard | 浏览器查看循环进度、任务详情、审查报告 |
| 学习反馈闭环 | 从历史 review 提取高频问题模式，固化到 prompt |
| 团队协作 | 多人共享 backlog、分配任务优先级 |
| 插件系统 | 自定义 Phase、门禁、通知渠道 |

---

## 6. 技术方案概要

### 6.1 技术选型

| 层 | 选型 | 理由 |
|---|------|------|
| 编排器 | Bash（v0.1）→ Node.js/TypeScript（v0.5+） | Bash 零依赖快速验证；TS 版可测试、类型安全、跨平台 |
| 状态存储 | JSON 文件 | 零依赖，Git 友好，人可读 |
| AI 运行时 | Claude Code CLI (`claude -p`) | 当前最成熟的非交互 AI 编码 CLI |
| 配置 | YAML (`imcto.yaml`) | 人可读，社区广泛采用 |
| Prompt 模板 | Markdown + 变量插值 | 用户可直接编辑和版本控制 |
| 包管理 | npm（v0.5+ 发布为 CLI 包） | `npx imcto init` 即可开始 |

### 6.2 配置文件设计 (`imcto.yaml`)

```yaml
# imcto.yaml — 项目级配置
project:
  name: my-project
  description: "A brief description for AI context"

# AI 运行时
runtime:
  engine: claude-code       # claude-code | codex | aider
  model: claude-opus-4-6    # 可选
  max_tokens: 200000        # 单会话 token 预算

# 分支策略
branching:
  main: master              # 主分支名
  dev_prefix: dev/batch     # dev 集成分支前缀
  feature_prefix: feature/  # 功能分支前缀
  fix_prefix: fix/          # 修复分支前缀

# 质量门禁
gates:
  # 快速门禁 (每个 Task commit 后)
  fast:
    - npm run lint
    - npm test -- --passWithNoTests

  # 完整门禁 (Phase 切换时)
  full:
    - npm run lint
    - npm run lint:layers
    - npm test -- --passWithNoTests
    - cd web && npm run build

  # 环境同步 (切分支后)
  setup:
    - npm install
    - cd web && npm install

# 重试策略
retry:
  max_verify_attempts: 3
  max_merge_fix_attempts: 3

# Prompt 上下文
context:
  # 注入到所有 prompt 的项目上下文
  include:
    - CLAUDE.md
    - docs/architecture/backend.md
  # 每个 prompt 预读文件的 token 预算
  token_budget: 3000

# 通知 (可选)
notify:
  on_complete: ""           # 全部完成时的通知命令
  on_blocked: ""            # 任务被阻塞时的通知命令
```

### 6.3 目录结构

```
my-project/
├── imcto.yaml              # 项目配置
├── .imcto/                 # imCTO 工作目录
│   ├── state.json          # 状态文件 (gitignored)
│   ├── inbox/              # 任务投递目录 (gitignored)
│   ├── tasks/              # 已入队任务 (git-tracked)
│   ├── backlog.md          # 自动生成的 backlog
│   ├── logs/               # 会话日志 (gitignored)
│   ├── reviews/            # 审查报告 (git-tracked)
│   ├── prompts/            # Prompt 模板 (可自定义覆盖)
│   └── progress.md         # 进度报告
└── ...
```

---

## 7. 成功指标

### 7.1 开源社区指标

| 指标 | 目标（发布后 3 个月） |
|------|---------------------|
| GitHub Stars | 1,000+ |
| 活跃 Forks（有实际适配的项目） | 50+ |
| Contributors | 10+ |
| 在不同技术栈项目中验证成功 | 5+ (Node.js, Python, Go, Rust, Java) |

### 7.2 产品质量指标

| 指标 | 目标 |
|------|------|
| 新项目适配时间 | < 30 分钟 |
| 低复杂度任务端到端成功率 | > 80% |
| 中复杂度任务端到端成功率 | > 50% |
| 断点续传成功率 | > 95% |
| 门禁误放行率（本地通过但 CI 失败） | < 5% |

### 7.3 用户体验指标

| 指标 | 目标 |
|------|------|
| 从 clone 到第一个 MR 的时间 | < 1 小时 |
| 文档覆盖率（每个 Phase 有教程） | 100% |
| 配置项有默认值的比例 | > 80% |

---

## 8. 风险与应对

| 风险 | 影响 | 概率 | 应对 |
|------|------|------|------|
| Claude Code CLI API 变更 | 编排脚本失效 | 中 | 抽象 AI 运行时接口，适配层隔离 |
| AI 生成代码质量不稳定 | 用户信任度下降 | 高 | 门禁机制兜底 + VERIFY 独立会话审查 |
| 用户项目门禁命令复杂 | 配置门槛高 | 中 | `imcto init` 自动检测 package.json / Makefile 生成默认配置 |
| Token 消耗成本 | 用户付费压力 | 中 | 门禁分层减少无效 token 消耗 + 上下文预读减少探索开销 |
| 竞品跟进（Devin/Copilot Workspace） | 市场份额被挤压 | 中 | 差异化定位：开源、可编排、项目级定制 |
| Shell 脚本跨平台兼容性 | Windows 用户无法使用 | 高 | v0.5 迁移到 Node.js CLI |

---

## 9. Go-to-Market 策略

### 9.1 发布节奏

| 时间 | 里程碑 | 内容 |
|------|--------|------|
| T+0 | v0.1-alpha | 核心编排脚本 + 适配指南 + 示例项目，GitHub 发布 |
| T+2w | v0.1 | 根据早期反馈修复，完善文档 |
| T+6w | v0.5-beta | Node.js CLI + `imcto init/run/status` + 门禁分层 |
| T+12w | v1.0 | 多 LLM 后端 + Web Dashboard + 插件系统 |

### 9.2 推广渠道

| 渠道 | 形式 |
|------|------|
| GitHub README + 演示视频 | 3 分钟 demo：从 inbox 到 MR 的完整流程 |
| Hacker News / Reddit | Show HN 帖子 |
| Twitter/X | 发布线程 + 实际跑通的截图/视频 |
| 中文技术社区 | 掘金/知乎专栏深度文章 |
| Claude Code 社区 | Discord / GitHub Discussions |
| 技术播客 | AI 编码工具相关播客嘉宾 |

### 9.3 开源策略

- **License**: MIT（最大程度降低使用门槛）
- **文档先行**: README、CONTRIBUTING.md、适配教程在代码之前完成
- **示例驱动**: 提供 3-5 个不同技术栈的适配示例
- **社区治理**: 从第一天起就接受 PR，维护 good-first-issue 标签

---

## 10. 从 Insight-Subs Loop 到 imCTO 的演进路径

当前 `loop/` 目录是 imCTO 的原型（codename: "Autonomous Dev Loop"），已在 Insight-Subs 项目中验证。从原型到开源产品的关键改造：

| 维度 | 当前原型 | imCTO 目标 |
|------|---------|-----------|
| 项目耦合 | 硬编码 Insight-Subs 的门禁命令和路径 | 通过 `imcto.yaml` 配置化 |
| Prompt | 内嵌在 Shell heredoc 中 | 独立 Markdown 模板文件 |
| 脚本语言 | 纯 Bash + 嵌入式 Python | v0.1 Bash，v0.5 迁移 Node.js |
| 安装方式 | 手动复制 `loop/` 目录 | `npx imcto init` |
| 文档 | 内部 Markdown | 完整的用户文档站点 |
| 测试 | 无 | Shell 脚本 + Node.js 集成测试 |
| CI | 无 | GitHub Actions 自测 |
| 适配性 | 仅 Node.js + Vue 3 项目 | 语言/框架无关 |
| 品牌 | 无 | imCTO + Logo + Landing Page |

### 已验证的核心价值

从 Insight-Subs 的实际运行数据：

- **3 天完成 3 个中等复杂度功能**（UI 优化、指标升级、市场重构）
- **自动化审查发现并修复 4 Critical + 12 Major 问题**（无人工介入）
- **32 个 Session 自动编排**，平均每个功能 ~10 个 Session
- **VERIFY 独立审查有效**：2 次 PASS，0 次误放行
- **热插入机制有效**：运行期间追加任务无需停止循环

---

## 附录 A：名称备选

| 名称 | 寓意 | 可用性 |
|------|------|--------|
| **imCTO** (推荐) | "I'm CTO"，每个人都能有 AI CTO | 需确认 npm/GitHub |
| AutoShip | 自动交付 | 语义直接 |
| LoopDev | 循环开发 | 简洁 |
| BacklogBot | backlog 机器人 | 功能导向 |
| ShipWhileYouSleep | 你睡觉它交付 | 太长，但适合 tagline |

**Tagline 建议**: "Ship while you sleep." / "你睡觉，它交付。"

## 附录 B：术语表

| 术语 | 定义 |
|------|------|
| Phase | imCTO 状态机中的一个阶段（DESIGN/IMPLEMENT/VERIFY 等） |
| Session | 一次独立的 AI CLI 会话调用，对应一个 Phase |
| Gate / 门禁 | Phase 切换前必须通过的自动化检查（lint/test/build） |
| inbox | 用户投递新任务的目录，编排器自动扫描入队 |
| backlog | 从 state 自动生成的任务列表文档 |
| FAST_TRACK | 低复杂度任务的快速通道，单 Session 完成 |
| dev 分支 | 本轮循环的集成缓冲区，所有 feature 先合入 dev |
| 断点续传 | 中断后从 loop-state.json 恢复，继续上次的 Task |
