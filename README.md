# openNiuMa

**把任务扔进去，AI 自己写完、审查、合并、建 PR。**

openNiuMa 是一个 AI 自治研发编排器。你描述要做什么，它驱动 Claude Code 在独立的 git worktree 里完成设计 → 实现 → 代码审查 → 合并 → 创建 PR 的全流程——最多 5 个任务并行跑，你去干别的事。

```
你:  bash openniuma/openniuma.sh add "支持深色模式" --complexity 中
你:  bash openniuma/openniuma.sh add "修复移动端侧边栏对齐" --complexity 低
你:  bash openniuma/openniuma.sh add "重构评分模型，支持权重配置" --complexity 高
你:  bash openniuma/openniuma.sh start --workers=3

⏳ 去喝杯咖啡...

🔔 [macOS 通知] 任务 #1 支持深色模式 完成 — PR #42 已创建
🔔 [macOS 通知] 任务 #2 修复移动端侧边栏对齐 完成 — PR #43 已创建
```

---

## 它解决什么问题

写代码只是开发工作的一部分。剩下的——读需求、出设计、实现、自测、代码审查、处理冲突、跑 CI、建 PR——每一步都要人盯着。

openNiuMa 把这些机械性流程交给 Claude Code 自动执行。你只需要：

1. 描述任务（一句话或一个 `.md` 文件）
2. 设定复杂度（低/中/高）
3. 启动编排器

它会按任务复杂度选择合适的执行路径，失败了自动重试，卡死了自动回收，全程不需要你盯着。

---

## 工作原理

### 三条执行路径（按复杂度自动选择）

```
低复杂度  ─→  一个 session 搞定全部  ─→  审查  ─→  合并  ─→  PR
中复杂度  ─→  设计+实现合并执行      ─→  审查  ─→  合并  ─→  PR
高复杂度  ─→  设计  ─→  分步实现     ─→  审查（可能触发修复循环）─→  合并  ─→  PR
```

### 并行 Worker 架构

```
你的任务 (inbox/)
      │
      ▼
  调度循环 (每 60s 扫描)
      │
  ┌───┼───┐
  ▼   ▼   ▼
 W1  W2  W3     每个 Worker = 独立进程组 + 独立 git worktree
  │   │   │
  ▼   ▼   ▼
Claude Claude Claude   各自独立跑，互不干扰
```

每个 Worker 在独立的 git worktree 中运行，有独立的文件系统，甚至可以配置独立的数据库——并行开发不会互相污染。

### 内置可靠性

- **失败自动分类**：区分 lint/test 失败、网络超时、Git 冲突、上下文耗尽等 6 种类型，分别用不同策略重试
- **Stall 检测**：Worker 超过 30 分钟没有日志更新，自动终止并重新入队
- **孤儿任务回收**：进程意外崩溃后，任务自动回到待执行状态
- **原子状态管理**：所有状态读写使用文件锁 + 原子替换，不会出现状态损坏

---

## 快速开始

### 前置依赖

| 依赖 | 版本要求 |
|------|---------|
| Python | >= 3.9 |
| PyYAML | 最新 |
| claude CLI | 最新（已登录） |

```bash
pip3 install pyyaml
```

### 安装到你的项目

```bash
# 拷贝引擎目录到你的项目
cp -r /path/to/openniuma /your/project/openniuma

cd /your/project

# 自动探测技术栈，生成配置（支持 Node/Go/Rust/Python/Ruby）
bash openniuma/init.sh
```

`init.sh` 会自动：
- 识别你的技术栈和测试命令
- 生成 `openniuma/workflow.yaml` 配置
- 用 AI 分析你的项目规范，生成 prompt 注入规则
- 更新 `.gitignore`

### 第一个任务

```bash
# 一句话入队
bash openniuma/openniuma.sh add "修复登录页 UI 对齐问题" --complexity 低

# 或者写 .md 文件放入 inbox/（支持更详细的描述）
cat > openniuma/inbox/my-task.md <<'EOF'
---
name: 支持自定义热力图半径
complexity: 中
---
用户可以在设置面板中调整热力图的聚合半径（默认 500m）。
需要前端滑块 + 后端参数传递 + 重新聚合计算。
EOF

# 启动（3 个 Worker 并行）
bash openniuma/openniuma.sh start --workers=3

# 另开一个终端看进度
bash openniuma/openniuma.sh dashboard -w
```

---

## CLI 速查

```bash
# 启动 / 停止
bash openniuma/openniuma.sh start                # 默认 5 个 Worker
bash openniuma/openniuma.sh start --workers=3    # 指定并发数
bash openniuma/openniuma.sh stop                 # 优雅停止

# 任务管理
bash openniuma/openniuma.sh add "任务描述"              # 一句话入队（默认复杂度：中）
bash openniuma/openniuma.sh add "任务描述" --complexity 高
bash openniuma/openniuma.sh cancel 55                   # 取消任务 #55

# 状态查看
bash openniuma/openniuma.sh status               # 文本格式
bash openniuma/openniuma.sh dashboard -w         # 终端实时看板（每 5s 刷新）
bash openniuma/openniuma.sh stats                # 运行统计摘要
bash openniuma/openniuma.sh stats --task 55      # 单任务详情
```

---

## 配置（workflow.yaml）

初始化后的最小配置，按你的项目修改：

```yaml
project:
  name: "My Project"
  main_branch: master
  dev_branch_prefix: "dev/batch"
  feat_branch_prefix: "feat"
  # CI 门禁：这些命令全部通过才算完成
  gate_command: |
    npm run lint && npm test && npm run build

hooks:
  # worktree 创建后执行（独立环境初始化）
  after_create: |
    npm install
  # worktree 删除前执行（清理）
  before_remove: |
    echo "cleanup"
```

### 支持的技术栈（init.sh 自动探测）

| 技术栈 | 探测文件 | 默认 Gate 命令 |
|--------|---------|--------------|
| Node.js | `package.json` | 从 scripts 拼装 lint + test + build |
| Go | `go.mod` | `go test ./... && go vet ./...` |
| Rust | `Cargo.toml` | `cargo test && cargo clippy` |
| Python | `pyproject.toml` | `pytest && ruff check .` |
| Ruby | `Gemfile` | `bundle exec rspec && rubocop` |

---

## 任务格式

```markdown
---
name: 重构评分模型，支持权重配置       # 必填
complexity: 高                        # 低 / 中（默认）/ 高
depends_on: [1, 3]                   # 依赖的任务 ID（可选）
---

详细描述：用户可以在管理面板为每个评分维度设置权重（0-100），
权重之和自动归一化。需要数据库 migration + API 变更 + 前端配置页面。
```

**复杂度选择参考：**
- **低**：改样式、修小 bug、加配置项（≤5 个文件，无数据库/API 变更）
- **中**（默认）：新增功能、重构局部逻辑
- **高**：涉及数据库 migration + API 变更 + 多页面联动

---

## 通知

默认开启 macOS 系统通知和终端 Bell。可选接入飞书 Webhook：

```yaml
notify:
  feishu_webhook: "https://open.feishu.cn/open-apis/bot/v2/hook/xxx"
  quiet_hours: "23:00-08:00"   # 静默时段（非 critical 通知不发送）
```

---

## 项目结构

```
openniuma/
├── openniuma.sh          # 统一 CLI 入口
├── dev-loop.sh           # 核心编排循环
├── workflow.yaml         # 项目配置
├── init.sh               # 初始化脚本
├── lib/                  # Python 核心模块
│   ├── config.py         # 配置加载 + prompt 模板渲染
│   ├── state.py          # 任务状态管理（原子读写）
│   ├── failure.py        # 失败自动分类（6 种类型）
│   ├── retry.py          # 退避重试策略
│   ├── reconcile.py      # Stall 检测 + 孤儿回收
│   ├── notify.py         # 多渠道通知 + 抑制
│   ├── stats.py          # 运行数据采集
│   └── detect.py         # 技术栈自动探测
├── prompts/              # Phase Prompt 模板（可自定义）
│   ├── _common-rules.md  # 注入到所有 prompt 的项目规范
│   ├── fast-track.md     # 低复杂度一次性完成
│   ├── design-implement.md
│   ├── design.md / implement.md / verify.md / fix.md
│   ├── merge.md / merge-fix.md
│   └── finalize.md / ci-fix.md
├── inbox/                # 热插入任务（.gitignore）
├── tasks/                # 已入队任务（git tracked）
├── reviews/              # 审查记录
└── backlog.md            # 自动生成的任务看板
```

---

## 自定义 Prompt

编辑 `openniuma/prompts/_common-rules.md`，内容会注入到所有 Phase 的 prompt 中。适合放：

- 项目编码规范（命名、时间格式、样式框架等）
- 硬性红线（禁止的操作）
- CI 门禁说明
- 移动端适配要求

修改即时生效，下一个 session 自动使用新 prompt。

---

## 故障排查

**编排器启动后不执行任务**
```bash
ls openniuma/inbox/STOP 2>/dev/null && echo "存在 STOP 信号，删除后重启"
python3 openniuma/lib/state.py ready openniuma/state.json
```

**Worker 持续失败**
```bash
ls -lt openniuma/logs/ | head -5          # 最近的 session 日志
bash openniuma/openniuma.sh stats         # 查看失败分布
```

**PyYAML 安装失败（PEP 668）**
```bash
pip3 install --user pyyaml
# 或
python3 -m venv .venv && .venv/bin/pip install pyyaml
```

---

## 测试

```bash
python3 -m unittest discover -s openniuma -p "test_*.py" -v
```

---

## 局限性与适用边界

**一句话总结：系统擅长执行，不擅长判断。** 凡是需要澄清、协商、探索、决策的地方，都是人还没法完全退出的地方。

### 任务描述质量是上限

系统没有澄清机制——任务入队后直接开跑，AI 不会停下来问"你说的 X 是指 A 还是 B"。任务描述模糊，产出就会偏差，而 VERIFY 阶段的 AI 也不知道你真正想要什么，同样发现不了这类偏差。

复杂度档位（低/中/高）由人判断，判断错误会直接导致执行路径选错。

### 并行任务之间存在协调盲区

Worker 在独立 worktree 里跑，任务之间看不到彼此。`depends_on` 只解决执行顺序，解决不了接口设计协商——5 个任务并行时，可能各自引入不同风格的抽象、对同一问题做出不同的设计决策。merge 冲突在高并发下是系统性问题，不是偶发。

### AI 审查 AI 存在同质化盲区

VERIFY 阶段是 Claude 审查 Claude 写的代码，两者共享同样的认知模式。代码层面的问题（测试覆盖、edge case）能发现，但"这个方向本身就错了"这类问题很难被识别。

### 大任务会触达上下文天花板

高复杂度任务的 IMPLEMENT 阶段需要读 spec + 理解存量代码 + 写实现，上下文消耗极快。系统对上下文耗尽的处理是"清空进度重试"，但真正大的任务重试也跑不完，会陷入循环。DESIGN 阶段的 spec 质量是另一个放大器：设计缺陷会被 IMPLEMENT 忠实地实现出来。

### 项目规范决定成功率上限

成功率高度依赖三个条件：

| 条件 | 缺失时的退化 |
|------|------------|
| **规范清晰**（`_common-rules.md`） | AI 靠猜风格决策，产出不一致，VERIFY 也没有基准可以对照 |
| **CI 门禁完备**（`gate_command`） | 任务"通过"了但实际有问题，系统不知道，会建一个有 bug 的 PR |
| **工具全链路开放**（claude 能跑什么命令） | AI 遇到问题只能靠重试，没有自我诊断能力 |

`_common-rules.md` 需要人来维护，项目规范更新后如果没有同步更新规则，AI 会持续产出旧风格的代码，且没有任何机制会提醒你。

### 不适合探索性工作

系统假设任务是"想清楚了去执行的"。需要先调研、原型验证、架构实验、技术选型的工作不应该走这套流程——在还没想清楚的时候入队，只会更快地跑向错误的方向。

### 成本不透明

多个 Worker 并行跑高复杂度任务，Claude API 费用可能远超预期。系统目前没有成本估算或用量上限控制。

---

## License

MIT
