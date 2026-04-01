# 优化过程摘要

## 配置
- 问题：openNiuMa 开源升级策略方案专家团评审优化
- 机制：专家团评审 (panel)
- 轮次：实际 3 轮 / 上限 3 轮
- 收敛：跑满 3 轮（每轮均有 P1 问题需修复）

## 轮次轨迹

### Round 1 — 架构师 + PM
- 评审角色：架构师、PM
- P0/P1 问题：
  - dev-loop.sh 重写不应放在 Phase 0（会阻塞整个时间线）
  - 缺少 workflow.yaml schema 版本化
  - prompts/ 放在包外部，pip install 后拿不到
  - 缺少核心用户画像
  - 竞品分析不够（缺 Devin/SWE-agent/aider）
- 关键修改：Phase 0 缩为最小解耦，Bash→Python 移到 Phase 1.5；prompts 移入 Python 包内；新增 3 级用户画像 + 扩展竞品矩阵

### Round 2 — 终端用户 + SRE
- 评审角色：终端用户、SRE
- P0/P1 问题：
  - FTUE（首次体验）路径缺失
  - `openniuma start` 默认静默，用户不知道发生了什么
  - 缺少升级回滚策略（state.json 无备份）
  - 日志/可观测性在开源方案中完全缺失
- 关键修改：设计完整 init 交互流程；start 改为默认前台模式；增加 state 备份 + 版本校验；新增 `openniuma logs` 子命令 + JSON lines 日志格式；三级优雅停机策略

### Round 3 — 商业分析师 + 安全工程师
- 评审角色：商业分析师、安全工程师
- P0/P1 问题：
  - Prompt 注入风险（任务描述可操控 AI）
  - Hooks 执行安全（等同于 CI 供应链攻击向量）
- 关键修改：任务描述 sanitization + _common-rules 防注入规则；hooks 首次执行需用户确认 + hash 记录；runtime 目录 chmod 700

## 最终决策清单

### 采纳
1. Phase 0 缩为最小解耦（Python CLI 壳 + 配置解耦），Bash 核心保持不变
2. 新增 Phase 1.5 "核心重写"（dev-loop.sh → Python）
3. prompts/ 移入 `src/openniuma/prompts/`，通过 `importlib.resources` 访问
4. workflow.yaml 增加 `schema_version` + `openniuma migrate` 命令
5. 定义 3 级用户画像（个人开发者 → Tech Lead → 开源维护者）
6. 竞品分析扩展（vs Devin / SWE-agent / aider / Copilot Workspace）
7. `openniuma init` 完整交互流程 + 技术栈自动检测
8. `openniuma start` 默认前台模式 + `--detach` 进后台
9. state.json 版本校验 + 自动备份机制
10. `openniuma logs` 子命令 + JSON lines 结构化日志
11. 三级优雅停机（stop / stop --now / stop --force）
12. complexity 自动推断 + 中英双语支持
13. Prompt 安全：sanitization + _common-rules 防注入规则
14. Hooks 安全：首次执行确认 + hash 记录
15. runtime 目录 chmod 700
16. 成本估算：`openniuma stats --cost` + 文档参考表
17. "vs Claude Code worktree mode" 差异叙事
18. 最小治理模型（2 Maintainer + RFC 流程）
19. 每个 Phase 增加 Done Criteria
20. Agent 抽象层预留（`agent.provider` 字段）

### 已考虑但未采纳
1. **Go 语言重写** — Python 足够，编排器瓶颈不在 CLI 启动速度
2. **npm 包装分发** — 目标用户非特定生态，pip/pipx 更通用
3. **orchestrator/ 过早拆分** — 先用单文件，复杂后再拆
4. **多 Agent 支持（首版）** — 预留接口但不实现，避免分散精力
5. **健康检查端点** — Future，等有用户集成需求再做
6. **Linux /proc 环境变量泄漏** — macOS 为主要目标，Linux 支持时再评估
