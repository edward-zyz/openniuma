# Loop v4 升级设计

> **状态**: 待实现
> **日期**: 2026-03-27
> **方案**: Shell 为主 + 内联 Python 片段（方案 C）
> **关联**: loop/autonomous-dev-loop.md, loop/dev-loop.sh

---

## 背景

Loop 系统经过 v1→v3 迭代，核心编排（多 phase 状态机、worktree 隔离、多 worker 并行）已趋稳定。但在可观测性、容错能力、数据沉淀、通知机制、入队便利性方面存在明显短板：

| 痛点 | 现状 | 影响 |
|------|------|------|
| 可观测性差 | 只能翻 JSON/日志 | 无法一眼掌握进度，异步协作困难 |
| 运行数据没沉淀 | 每轮跑完无统计 | 无法量化优化效果 |
| 无异步通知 | 任务完成/失败只写日志 | 无人值守时错过关键事件 |
| 失败恢复粗暴 | 连续 3 次失败就停 | 不区分失败类型，无降级/跳过 |
| 任务入队不便 | 手动写 inbox/ markdown | 格式门槛高，容易出错 |

## 设计原则

- **与现有风格一致**：Shell 编排 + python3 内联，零额外依赖
- **零侵入集成**：新工具通过明确接口与 dev-loop.sh 对接，不重构现有逻辑
- **渐进式落地**：5 个模块按依赖顺序独立交付，每个可单独使用

## 文件清单

```
loop/
├── dev-loop.sh              # 现有，需改动（埋点 + 通知 + 失败恢复）
├── dashboard.sh             # 新增：终端实时看板
├── generate-progress.sh     # 新增：Mermaid 进度报告生成器
├── stats.sh                 # 新增：统计查看命令
├── stats.json               # 新增：运行数据（.gitignore）
├── notify.sh                # 新增：统一通知入口
├── add-task.sh              # 新增：任务快捷入队 CLI
├── .env                     # 新增：通知配置（.gitignore）
├── PROGRESS.md              # 生成产物（.gitignore）
└── ...（现有文件不变）
```

## 落地优先级

| 顺序 | 模块 | 依赖 | 交付物 |
|------|------|------|--------|
| 1 | 可视化 | 无 | dashboard.sh, generate-progress.sh |
| 2 | 运行数据沉淀 | 无（可视化可选消费） | stats.sh, stats.json, dev-loop.sh 埋点 |
| 3 | 异步通知 | 无（统计数据可选增强） | notify.sh, .env |
| 4 | 智能失败恢复 | 统计数据（可选） | dev-loop.sh 改造 |
| 5 | 任务快捷入队 | 无 | add-task.sh |

---

## 模块 1：可视化

### 1.1 dashboard.sh — 终端实时看板

**用法：**

```bash
bash loop/dashboard.sh           # 单次渲染
bash loop/dashboard.sh -w        # 每 5s 自动刷新
bash loop/dashboard.sh -w 10     # 每 10s 刷新
```

**渲染区域：**

```
┌─ Dev Loop Dashboard ──────────────────────────────┐
│  Header: 分支 / 阶段(emoji) / 当前任务 / 更新时间  │
├────────────────────────────────────────────────────┤
│  Progress: ██████████░░░░░░░░░░  2/4 (50%)        │
├────────────────────────────────────────────────────┤
│  Task List: 表格，每行一个任务                      │
│    #55 刷新热力图        ✅ DONE    低              │
│    #56 评分不一致调试     ✅ DONE    中              │
│    #57 移动训练入口       🔄 ACTIVE  低              │
│    #58 首页关键词搜索     ⏳ PENDING 中              │
├────────────────────────────────────────────────────┤
│  Pipeline: 当前任务的阶段流水线                      │
│    ⚡FAST → [🔨IMPL] → 🔍VERIFY → 🔀MERGE         │
├────────────────────────────────────────────────────┤
│  Checkpoint: chunk/task/commit 进度                  │
│  Retry: verify_attempts / merge_fix_attempts        │
├────────────────────────────────────────────────────┤
│  Recent Sessions: 最近 5 个 session 摘要             │
│  Recent Reviews: 最近 3 个审查结论                   │
├────────────────────────────────────────────────────┤
│  Stats: 💰 $8.45 | ⏱ 3.1h/任务（有 stats.json 时） │
└────────────────────────────────────────────────────┘
```

**并行模式扩展：** 检测到 `loop/workers/*/pid` 时增加 Workers 区域：

```
├── Workers ────────────────────────────────────────┤
│  Worker #55  🔨 IMPLEMENT  PID:12345  运行 23m    │
│  Worker #57  🔍 VERIFY     PID:12347  运行 8m     │
│  Worker #58  ⏳ 等待依赖 (#55)                     │
│  容量: 2/5 active                                  │
```

**颜色方案：**

| 元素 | 颜色 |
|------|------|
| 已完成任务 / 已过阶段 | 绿色 |
| 进行中任务 / 当前阶段 | 黄色加粗 |
| 待处理任务 / 未到阶段 | 灰色 |
| 阻塞任务 | 红色 |

**阶段图标：**

| Phase | Icon | Phase | Icon |
|-------|------|-------|------|
| INIT | 🚀 | FIX | 🔧 |
| DESIGN | 📐 | MERGE | 🔀 |
| DESIGN_IMPLEMENT | 📐🔨 | MERGE_FIX | 🩹 |
| IMPLEMENT | 🔨 | FINALIZE | 📦 |
| VERIFY | 🔍 | FAST_TRACK | ⚡ |

**实现方式：** jq 解析 loop-state.json + find/awk 扫描 logs/ 和 reviews/，ANSI 转义码着色。

### 1.2 generate-progress.sh — Mermaid 进度报告

**用法：**

```bash
bash loop/generate-progress.sh       # → loop/PROGRESS.md
```

**输出结构：**

| 章节 | 内容 | 格式 |
|------|------|------|
| 概览 | 分支、阶段、任务、进度条 | Markdown 表格 |
| 任务状态图 | done/active/pending/blocked 四色节点 | Mermaid graph LR |
| 阶段流水线 | 当前任务的 phase 进度 | Mermaid graph LR |
| 实施进度 | chunk/task/commit 细节 | Markdown 表格 |
| 任务清单 | 全量任务列表 | Markdown 表格 |
| Session 按日汇总 | 日期 x 阶段 计数矩阵 | Markdown 表格 |
| Session 甘特时间线 | 按日+阶段去重聚合 | Mermaid gantt |
| 审查记录 | 从 reviews/*.md 提取 Verdict | Markdown 表格 |
| 运行统计 | 耗时/成本/通过率（有 stats.json 时） | Markdown 表格 |

### 1.3 与 dev-loop.sh 集成

在 `parallel_main()` 和串行主循环的每轮 phase 结束后：

```bash
bash loop/generate-progress.sh 2>/dev/null &  # 后台生成，不阻塞
```

---

## 模块 2：运行数据沉淀

### 2.1 数据结构 — stats.json

```jsonc
{
  "sessions": [
    {
      "task_id": 55,
      "task_name": "刷新热力图",
      "phase": "FAST_TRACK",
      "started_at": "2026-03-26T14:20:00.000Z",
      "ended_at": "2026-03-26T14:43:22.000Z",
      "duration_sec": 1402,
      "exit_code": 0,
      "cost_usd": null,
      "worker_id": null,
      "attempt": 1
    }
  ],
  "tasks": [
    {
      "id": 55,
      "name": "刷新热力图",
      "complexity": "低",
      "path": "FAST_TRACK",
      "total_sessions": 3,
      "total_duration_sec": 4200,
      "total_cost_usd": 1.23,
      "verify_attempts": 1,
      "verify_pass_first_try": true,
      "merge_fix_attempts": 0,
      "started_at": "2026-03-26T14:20:00.000Z",
      "completed_at": "2026-03-26T15:30:00.000Z"
    }
  ]
}
```

### 2.2 采集点（dev-loop.sh 埋点）

| 时机 | 采集内容 | 实现方式 |
|------|----------|----------|
| Claude 会话开始前 | `started_at` | python3 内联追加 sessions[] |
| Claude 会话结束后 | `ended_at`, `duration_sec`, `exit_code` | python3 内联更新最后一条 |
| verbose 模式 result 事件 | `cost_usd` | 从 stream-json 解析 `total_cost_usd` |
| `sync_worker_result()` | `worker_id` | 并行模式记录 |
| `mark_task_done()` | 汇总到 tasks[] | 聚合该任务所有 session 数据 |

### 2.3 stats.sh — 统计查看命令

```bash
bash loop/stats.sh                # 全量统计摘要
bash loop/stats.sh --task 55      # 单任务详情
bash loop/stats.sh --cost         # 按成本排序
```

**全量摘要输出：**

```
📊 Dev Loop 运行统计
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
总任务: 4 (完成 2, 进行中 1, 待处理 1)
总 Session: 18  |  总耗时: 12.3h  |  总成本: $8.45

平均每任务: 3.1h, 4.5 sessions, $2.11

VERIFY 通过率: 首次 60% (3/5), 最终 100% (5/5)

按复杂度:
  低: 1.8h/任务, $1.20  (2 个)
  中: 4.2h/任务, $3.10  (2 个)

按阶段耗时占比:
  DESIGN_IMPLEMENT  45% ████████░░░░░░░░
  VERIFY            25% █████░░░░░░░░░░░
  MERGE             15% ███░░░░░░░░░░░░░
  FIX               15% ███░░░░░░░░░░░░░
```

---

## 模块 3：异步通知

### 3.1 notify.sh — 统一通知入口

```bash
bash loop/notify.sh --level info --title "任务完成" --body "✅ #55 刷新热力图 (1.8h, $1.20)"
```

### 3.2 通知触发时机

| 事件 | 级别 | 消息 |
|------|------|------|
| 任务完成 | info | `✅ #55 刷新热力图 完成 (1.8h, $1.20)` |
| 任务阻塞 | warn | `🚫 #58 首页搜索 被阻塞 (依赖 #55)` |
| 会话失败 | warn | `⚠️ #57 IMPLEMENT 失败 (exit 1), 重试 2/3` |
| 连续失败停止 | critical | `🛑 Loop 停止: #57 连续 3 次失败` |
| 全部完成 | info | `🎉 4 个任务全部完成! 12.3h, $8.45` |
| Worker 启停 | debug | `🚀 Worker #55 启动` / `退出 (exit 0)` |

### 3.3 通知渠道（三层）

**层 1：macOS 系统通知（默认开启，零配置）**

```bash
osascript -e 'display notification "✅ #55 完成" with title "Dev Loop"'
```

**层 2：飞书 Webhook（可选，配置 loop/.env）**

```bash
# loop/.env
LOOP_FEISHU_WEBHOOK=https://open.feishu.cn/open-apis/bot/v2/hook/xxx
LOOP_NOTIFY_LEVEL=info   # debug|info|warn|critical
```

通过 `curl` 发送飞书自定义机器人卡片消息。critical 级别附带最近 5 行错误日志。

**层 3：终端 Bell（始终）**

```bash
printf '\a'
```

### 3.4 集成方式

dev-loop.sh 中关键位置后台调用：

```bash
bash loop/notify.sh --level info ... &  # 不阻塞主流程
```

---

## 模块 4：智能失败恢复

### 4.1 失败分类 + 策略矩阵

Claude 会话结束后（exit≠0），python3 内联分析 session log 尾部 50 行，判定失败类型：

| 失败类型 | 判定关键词 | 策略 | 最大重试 |
|----------|-----------|------|---------|
| 门禁失败 | `npm run lint/test/build` exit≠0 | 重试，FIX 自动修复 | 3 |
| 上下文耗尽 | `context window`, `token limit`, `max turns` | 清空进度，断点续传新会话 | 1 |
| 权限阻塞 | `permission denied`, `dangerously-skip` | 修复参数后重试 | 1 |
| Git 冲突 | `CONFLICT`, `merge conflict` | 重试 MERGE，注入冲突提示 | 2 |
| 网络/API 超时 | `ETIMEDOUT`, `ECONNREFUSED`, `rate limit` | sleep 60s 后重试 | 2 |
| 未知错误 | 以上都不匹配 | 标记 blocked + 通知 + 跳过 | 0 |

### 4.2 核心流程

```
会话失败 (exit≠0)
  │
  ├─ python3 分析 log → 判定 failure_type
  │
  ├─ retry_count < max_retries[failure_type] ?
  │   ├── 是 → sleep backoff → 重试（prompt 注入失败上下文）
  │   └── 否 → 降级处理
  │
  └─ 降级处理：
      ├── 上下文耗尽 → 清空 implement_progress, 新会话断点续传
      ├── 可跳过（无下游依赖/SKIP_ON_UNKNOWN=true）
      │     → blocked + notify(warn) + 继续下一任务
      └── 不可跳过（有下游依赖）
            → notify(critical) + 暂停 loop
```

### 4.3 重试时 Prompt 注入

```
⚠️ 上次尝试失败 (第 {attempt}/{max_retries} 次)。
失败类型: {failure_type}
错误摘要: {session log 最后 10 行}
请避免相同错误，调整策略后重试。
```

### 4.4 退避策略

| 次数 | 等待 |
|------|------|
| 第 1 次 | 立即 |
| 第 2 次 | 30s |
| 第 3 次 | 60s |
| 网络超时 | 60s（固定） |

### 4.5 配置项（dev-loop.sh 顶部）

```bash
MAX_RETRIES_GATE=3        # 门禁失败最大重试
MAX_RETRIES_NETWORK=2     # 网络错误最大重试
MAX_RETRIES_CONTEXT=1     # 上下文耗尽最大重试
SKIP_ON_UNKNOWN=true      # 未知错误是否跳过（false=暂停）
```

---

## 模块 5：任务快捷入队

### 5.1 add-task.sh — CLI 快捷入队

```bash
# 一句话入队
bash loop/add-task.sh "支持自定义热力图半径"

# 指定类型和优先级
bash loop/add-task.sh "支持自定义热力图半径" --type feat --priority high

# 从 GitHub Issue 导入
bash loop/add-task.sh --from-issue 42

# 批量导入
bash loop/add-task.sh --batch tasks.txt

# 交互模式（无参数）
bash loop/add-task.sh
```

### 5.2 生成逻辑

```
输入: "支持自定义热力图半径"
  │
  ├─ ID: 扫描 tasks/ 最大 ID + 1 → 59
  ├─ slug: python3 生成 → custom-heatmap-radius
  ├─ 文件名: 59-custom-heatmap-radius_03-27_15-30.md
  ├─ 写入 inbox/:
  │     ---
  │     created_at: "2026-03-27 15:30"
  │     type: feature
  │     priority: medium
  │     status: open
  │     ---
  │     # 支持自定义热力图半径
  │
  │     ## 需求描述
  │     支持自定义热力图半径
  │
  └─ 输出: ✅ 任务 #59 已入队
```

### 5.3 GitHub Issue 导入

通过 `gh issue view <id> --json title,body,labels` 拉取，自动映射 labels 到 type 字段。

### 5.4 与 dev-loop.sh 衔接

零侵入：add-task.sh 只写 inbox/，现有 `process_inbox()` 每轮自动扫描并入队。

---

## 数据流总览

```
                    ┌─────────────┐
                    │ add-task.sh │ ← 用户/GH Issue
                    └──────┬──────┘
                           │ 写入
                           ▼
                      inbox/*.md
                           │ process_inbox()
                           ▼
┌──────────────────────────────────────────────────┐
│                  dev-loop.sh                      │
│                                                   │
│  ┌─────────┐    ┌─────────────┐    ┌──────────┐ │
│  │ 状态机  │───▶│ Claude 会话 │───▶│ 结果处理 │ │
│  └────┬────┘    └──────┬──────┘    └────┬─────┘ │
│       │                │                │        │
│       │         ┌──────▼──────┐  ┌──────▼─────┐ │
│       │         │  stats.json │  │ notify.sh  │ │
│       │         │  (埋点写入)  │  │ (事件推送) │ │
│       │         └──────┬──────┘  └──────┬─────┘ │
│       │                │                │        │
│  ┌────▼────────────────▼────┐    ┌──────▼─────┐ │
│  │    loop-state.json       │    │ 飞书/macOS │ │
│  └────┬─────────────────────┘    │ /终端 Bell │ │
│       │                          └────────────┘ │
└───────┼──────────────────────────────────────────┘
        │ 读取
        ▼
┌───────────────┐    ┌────────────────────┐
│ dashboard.sh  │    │ generate-progress  │
│ (终端实时)    │    │ → PROGRESS.md      │
└───────────────┘    └────────────────────┘
        │                     │
        ▼                     ▼
   终端彩色输出         飞书/GitLab 分享
```

## 技术约束

- **依赖**: bash 4+, jq 1.7+, python3 3.8+（均为 macOS 自带）
- **可选依赖**: gh CLI（GitHub Issue 导入）, curl（飞书通知）
- **兼容性**: macOS (zsh/bash), Linux
- **错误处理**: 所有脚本 `set -euo pipefail`
- **.gitignore 新增**: `loop/stats.json`, `loop/PROGRESS.md`, `loop/.env`
