# openNiuMa Dashboard TUI 设计

## 背景

当前 dashboard 只展示进度条、汇总计数和任务列表，信息量太小。需要细化为一个交互式 TUI，展示任务内部进度、健康状态、时间维度和实时日志。

## 方案选型

**选定：Python Textual**

理由：与现有 Python 编排层（state.py / stats.py / json_store.py）无缝集成，内置 CSS 布局系统和键盘交互，天然支持异步文件监听。

## 布局

```
┌─────────────────────────────────────────────────────────┐
│  openNiuMa Dashboard          ○ 0  ● 1  ✓ 1  ✗ 0      │  Header
├──────────────────────┬──────────────────────────────────┤
│  Task List (30%)     │  Task Detail (70%)               │
│                      │  ┌─ Info ──────────────────────┐ │
│  ● [2] VERIFY cop…  │  │ 阶段 / 进度 / 重试 / 分支   │ │
│  ✓ [3] 登录页面…     │  │ 耗时 / 最近commit / 失败原因 │ │
│                      │  └──────────────────────────────┘ │
│                      │  ┌─ Log ───────────────────────┐ │
│                      │  │ worker 实时日志 tail          │ │
│                      │  └──────────────────────────────┘ │
├─────────────────────────────────────────────────────────┤
│  [q] Quit  [↑↓/jk] Navigate  [r] Refresh  [l] Log     │  Footer
└─────────────────────────────────────────────────────────┘
```

- **左栏 Task List**：所有任务，状态图标+颜色+当前阶段标签，键盘 ↑↓ 切换选中
- **右栏 Info**：选中任务的结构化状态
- **右栏 Log**：选中任务的 worker 日志实时 tail
- **Header**：标题 + 四种状态汇总计数
- **Footer**：快捷键提示 + 整体进度条

## 数据映射

### Task List

| 展示 | 数据来源 |
|------|---------|
| 状态图标 + 颜色 | `state.json` → `queue[].status` |
| 任务 ID + 名称 | `state.json` → `queue[].id`, `queue[].name` |
| 当前阶段标签 | `workers/<id>/state.json` → `current_phase`（仅 in_progress） |

列表项格式：`● [2] VERIFY copilot cocreation plan`

### Info 面板

| 字段 | 数据来源 |
|------|---------|
| 当前阶段 | `workers/<id>/state.json` → `current_phase` |
| 实现进度 | `implement_progress.current_task` / `current_chunk`（仅 DESIGN_IMPLEMENT 阶段） |
| 重试次数 | `verify_attempts` / `merge_fix_attempts`，带上限如 `0/3` |
| 分支名 | `workers/<id>/state.json` → `branch` |
| 最近 commit | `implement_progress.last_commit_sha` + `last_committed_task` |
| 已用时间 | `stats.json` sessions 按 task_id 聚合 `duration_sec` |
| 最近失败 | `stats.json` 最近 `exit_code != 0` 的 session → `failure_type` |
| 完成时间 | `state.json` → `completed_at`（done 状态） |

### Log 面板

- 数据源：`logs/worker-<id>.log`
- 行为：异步 tail，新内容追加到底部，自动滚动
- done/pending 任务：显示日志最后 N 行或 "无活跃日志"

## 交互

| 按键 | 动作 |
|------|------|
| `↑`/`↓` 或 `k`/`j` | 切换选中任务 |
| `q` | 退出 |
| `r` | 手动强制刷新 |
| `l` | 切换日志面板显示/隐藏 |
| `/` | 按状态筛选任务 |

Dashboard 定位为只读监控，不做任何写操作。

## 刷新机制

- `state.json` + `workers/*/state.json`：文件系统 watch（`watchfiles`），变更即刷新
- 日志文件：异步 tail，每 500ms 检查新内容
- `stats.json`：与 state.json 一起 watch

## 技术实现

### 依赖

- `textual` — TUI 框架
- `watchfiles` — 文件变更监听

启动时自检依赖，缺失则自动 `pip install`。`init.sh` 中增加检测提示。

### 文件结构

```
openniuma/
├── tui/
│   ├── app.py              # Textual App 主入口
│   ├── widgets/
│   │   ├── task_list.py    # 左栏任务列表
│   │   ├── task_detail.py  # 右栏 Info 面板
│   │   └── log_viewer.py   # 右栏 Log 面板
│   └── styles.tcss         # Textual CSS
├── dashboard.sh             # 入口，调用 tui/app.py
```

### 数据层

直接 import 现有 `lib/state.py`、`lib/stats.py`、`lib/json_store.py`，不新建数据层。

### 入口兼容

`dashboard.sh` 默认启动 TUI，保留 `--format text/json` 走原有 `lib/status.py`。
