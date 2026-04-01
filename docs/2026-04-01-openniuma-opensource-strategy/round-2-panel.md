# Round 2 — 终端用户 + SRE 评审

> 基于 Round 1 修订后的 v2 方案进行评审。

## 终端用户评审

### 认可点
- **`openniuma doctor` 设计贴心**：新手最怕环境问题，一个命令诊断所有前置依赖 + `--fix` 自动修复，大幅降低上手门槛
- **Round 1 新增的用户画像让叙事更清晰**：Primary Persona（个人开发者用 Claude Code）定位准确
- **`openniuma add "描述" --complexity 中` 的 UX 直觉**：任务入队只需一行命令 + 自然语言描述，零学习成本

### 问题
- **[P1]** 首次体验路径 (FTUE) 缺失 — 用户 `pip install openniuma` 后的下一步是什么？`openniuma init` 是交互式的，但方案没有设计交互流程。用户需要回答哪些问题？如果用户不知道 gate_command 该填什么怎么办？缺少引导会导致 init 阶段流失
- **[P1]** 任务执行过程对用户不透明 — `openniuma start` 之后用户看到什么？方案有 TUI dashboard，但没有设计"默认输出"。如果用户不加 `dashboard`，只是 `openniuma start`，终端是空白的还是有进度流？新手不会主动去找 dashboard 命令
- **[P2]** `--complexity 低|中|高` 对新用户是认知负担 — 用户怎么判断一个任务是"低"还是"中"？没有给出判断标准或自动推断机制。如果判断错了（标了"低"实际是"高"），后果是什么？
- **[P2]** 错误消息设计未提及 — 当 `gate_command` 失败、Claude Code CLI 未安装、API key 过期等场景，用户看到的是 Python traceback 还是友好提示？开源工具的口碑常常取决于错误体验
- **[P3]** 中英混合的命令体验 — CLI 命令是英文 (`openniuma add`)，但 `--complexity` 的值是中文（低/中/高）。这在纯英文终端里体验割裂。建议同时支持 `low/medium/high`

### 建议
- 设计 `openniuma init` 的交互流程：(1) 检测项目类型（Node/Python/Go）→ 自动推荐 gate_command (2) 询问主分支名 (3) 生成 workflow.yaml + 打印 "下一步" 提示
- `openniuma start` 默认应有简洁的实时输出（类似 docker-compose up），而非静默。加 `--detach` 才进入后台模式
- 提供 complexity 自动推断：如果用户不指定，openniuma 根据描述长度 / 关键词（如"重构"、"迁移"）自动判断，并在输出中提示 "检测为中复杂度，可用 --complexity 覆盖"
- 增加"错误信息设计规范"章节：所有用户可见错误必须包含 (1) 发生了什么 (2) 可能原因 (3) 修复建议

---

## SRE 评审

### 认可点
- **原子状态管理（fcntl + fsync + replace）**：这是生产级的做法，多 worker 并发下不会丢数据
- **失败分类 + 分类重试**：6 类失败各有重试策略，不是简单的 retry 3 次，这比大多数 CI 系统做得好
- **stall detection + orphan recovery**：30 分钟无响应自动终止 + 进程崩溃自动 requeue，覆盖了最常见的编排器故障模式

### 问题
- **[P1]** 缺少升级回滚策略 — 用户 `pip install --upgrade openniuma` 后，如果新版本有 bug（比如 state.json schema 变了），怎么回滚？state.json 没有备份机制，.openniuma-runtime 目录没有版本标记。升级导致数据损坏 = 丢失所有进行中任务
- **[P1]** 日志和可观测性在开源方案中缺失 — 原始方案提到了 `.openniuma-runtime/logs/`，但开源文档中完全没有涉及日志格式、日志级别、日志轮转、结构化日志等。调试问题时用户只能翻原始 log 文件，没有 `openniuma logs --task 5 --tail 50` 这样的命令
- **[P2]** 多项目同时使用 openNiuMa 的隔离性未讨论 — 如果用户在项目 A 和项目 B 同时跑 openNiuMa，runtime 目录、worker PID、端口是否冲突？当前设计 `.openniuma-runtime` 在项目根目录，理论上隔离，但 `openniuma status` 等全局命令不知道该看哪个项目
- **[P2]** 没有优雅停机设计 — `openniuma stop` 目前是发 STOP 信号文件，但方案没说 worker 收到信号后的行为：是立即终止（丢失进行中工作）？还是完成当前 phase 后退出？还是完成当前任务后退出？
- **[P3]** 健康检查端点缺失 — 对于长时间运行的编排器，应该有类似 `/healthz` 的检查机制，方便与监控系统集成

### 建议
- 增加 `openniuma backup` / 自动备份机制：每次升级前自动备份 `.openniuma-runtime/state.json`
- state.json 增加 `_niuma_version` 字段，启动时校验版本兼容性
- 增加 `openniuma logs` 子命令：支持 `--task`, `--worker`, `--tail`, `--follow`, `--level`
- 日志格式采用 JSON lines（结构化日志），方便 jq / Grafana Loki 消费
- 优雅停机策略：`openniuma stop` = 完成当前 phase 后退出；`openniuma stop --now` = 完成当前 AI 调用后退出；`openniuma stop --force` = 立即终止

---

## 方案修订 v3

### P1 修复

1. **首次体验路径 (FTUE) 设计**
   `openniuma init` 交互流程：
   ```
   $ openniuma init
   Detected: Node.js project (package.json found)
   
   ? Main branch: (master) ▸ Enter to confirm
   ? Gate command: (npm test) ▸ Enter to confirm  # 根据项目类型自动推荐
   ? Max workers: (3) ▸ Enter to confirm
   
   Created: workflow.yaml
   
   Next steps:
     1. openniuma add "你的第一个任务" --complexity 低
     2. openniuma start
     3. openniuma dashboard -w    # 实时查看进度
   ```

2. **`openniuma start` 默认前台模式**
   ```
   $ openniuma start
   openNiuMa v0.1.0 | 3 workers | polling every 60s
   ─────────────────────────────────────────────
   [14:30:01] Task #1 "实现登录" → DESIGN (worker-1)
   [14:30:02] Task #2 "修复样式" → FAST_TRACK (worker-2)
   [14:32:15] Task #2 "修复样式" → done ✓
   [14:35:00] Task #1 "实现登录" → IMPLEMENT (worker-1)
   
   Press Ctrl+C for graceful shutdown, or run `openniuma dashboard` for full TUI
   ```
   加 `--detach` / `-d` 进入后台模式。

3. **升级回滚机制**
   - state.json 增加 `_niuma_version: "0.1.0"` 字段
   - 启动时版本不匹配 → 提示 `openniuma migrate` 或 `openniuma rollback`
   - `pip install openniuma` 触发 post-install hook 自动备份当前 state
   - `.openniuma-runtime/backups/state-{version}-{timestamp}.json` 保留最近 5 个备份

4. **日志子命令**
   ```bash
   openniuma logs                       # 所有 worker 最近输出
   openniuma logs --task 5              # 指定任务的完整日志
   openniuma logs --task 5 --tail 50    # 最后 50 行
   openniuma logs --task 5 --follow     # 实时跟踪
   openniuma logs --level error         # 只看错误
   ```
   日志格式：JSON lines（结构化），`openniuma logs` 命令默认渲染为人类可读格式。

### P2 处理

- **complexity 自动推断**：采纳。不指定时自动推断 + 提示用户确认
- **错误信息设计规范**：采纳。增加到 CONTRIBUTING.md 中作为开发规范
- **中英双语 complexity 值**：采纳。`低/中/高` 和 `low/medium/high` 都支持
- **多项目隔离**：当前设计已足够（runtime 在项目根目录下），`openniuma status` 只看当前目录。明确文档化这一行为
- **优雅停机**：采纳 SRE 建议的三级策略
  - `openniuma stop` → 完成当前 phase 后退出
  - `openniuma stop --now` → 完成当前 AI 调用后退出
  - `openniuma stop --force` → 立即终止

### P3 处理
- **健康检查**：记录为 Future，在有用户提出集成需求时再实现
