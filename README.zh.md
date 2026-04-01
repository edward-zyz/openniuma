# openNiuMa

> Claude Code 是写代码的 AI。openNiuMa 是管理 AI 写代码的编排器。

[English](README.md) | [中文](README.zh.md)

## 为什么用 openNiuMa？

你有一堆待办任务。Claude Code 能处理——但一次只能做一个，而且你得盯着。

openNiuMa 改变了这一切：

- **5 个并行 Worker**，每个运行在独立的 git worktree
- **完整开发生命周期**：设计 → 实现 → 测试 → Code Review → 合并 → PR
- **智能失败恢复**：6 种失败类型，各有专属重试策略
- **无人值守运行**：后台守护进程，自动从停滞和崩溃中恢复

类比：Claude Code `--worktree` 是 `docker run`。openNiuMa 是 Kubernetes。

## 快速开始

```bash
# 安装
pipx install openniuma

# 在你的项目中初始化
cd your-project
openniuma init

# 入队一个任务
openniuma add "实现用户登录" --complexity 中

# 启动编排器
openniuma start
```

## 工作原理

```
                    ┌─────────────┐
                    │  任务队列    │
                    │  (backlog)  │
                    └──────┬──────┘
                           │
                    ┌──────▼──────┐
                    │   编排器     │
                    │ (scheduler) │
                    └──┬───┬───┬──┘
                       │   │   │
              ┌────────┘   │   └────────┐
              ▼            ▼            ▼
        ┌──────────┐ ┌──────────┐ ┌──────────┐
        │ Worker 1 │ │ Worker 2 │ │ Worker 3 │
        │ worktree │ │ worktree │ │ worktree │
        └──────────┘ └──────────┘ └──────────┘
```

## 任务复杂度

| 复杂度 | 阶段 | 预估成本 (Opus) |
|--------|------|----------------|
| 低 | FAST_TRACK → VERIFY → MERGE | ~$0.75 |
| 中 | DESIGN_IMPLEMENT → VERIFY → MERGE | ~$2.25 |
| 高 | DESIGN → IMPLEMENT → VERIFY → MERGE | ~$4.50 |

## 配置

项目根目录的 `workflow.yaml`：

```yaml
schema_version: 1

project:
  name: "我的项目"
  main_branch: main
  gate_command: "npm test"

workers:
  max_concurrent: 3

models:
  default: opus
```

## 命令

| 命令 | 说明 |
|------|------|
| `openniuma init` | 在当前项目初始化 |
| `openniuma add <描述> -c 低\|中\|高` | 入队任务 |
| `openniuma start` | 启动编排器（前台） |
| `openniuma start -d` | 后台启动 |
| `openniuma status` | 查看任务状态 |
| `openniuma dashboard` | TUI 看板 |
| `openniuma doctor` | 环境诊断 |
| `openniuma stop` | 优雅停机 |
| `openniuma cancel <id>` | 取消任务 |

## 环境要求

- Python >= 3.10
- Git
- [Claude Code](https://claude.ai/code) CLI

## 贡献

见 [CONTRIBUTING.md](CONTRIBUTING.md)。

## 许可

[MIT](LICENSE)
