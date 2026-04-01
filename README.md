# openNiuMa

> Claude Code writes code. openNiuMa orchestrates Claude Code writing code.

[English](README.md) | [中文](README.zh.md)

## Why openNiuMa?

You have a backlog of tasks. Claude Code can handle them — but one at a time, and only while you're watching.

openNiuMa changes that:

- **5 parallel workers**, each in an isolated git worktree
- **Full development lifecycle**: design → implement → test → code review → merge → PR
- **Smart failure recovery**: 6 failure types, each with its own retry strategy
- **Runs unattended**: background daemon, auto-recovery from stalls and crashes

Think of it as Kubernetes for AI coding tasks. Claude Code `--worktree` is `docker run`. openNiuMa is the orchestrator.

## Quick Start

```bash
# Install
pipx install openniuma

# Initialize in your project
cd your-project
openniuma init

# Queue a task
openniuma add "Implement user login" --complexity 中

# Start the orchestrator
openniuma start
```

## How It Works

```
                    ┌─────────────┐
                    │  Backlog    │
                    │  (queue)    │
                    └──────┬──────┘
                           │
                    ┌──────▼──────┐
                    │ Orchestrator │
                    │ (scheduler)  │
                    └──┬───┬───┬──┘
                       │   │   │
              ┌────────┘   │   └────────┐
              ▼            ▼            ▼
        ┌──────────┐ ┌──────────┐ ┌──────────┐
        │ Worker 1 │ │ Worker 2 │ │ Worker 3 │
        │ worktree │ │ worktree │ │ worktree │
        └──────────┘ └──────────┘ └──────────┘
```

Each worker:
1. Claims a task from the queue
2. Creates an isolated git worktree
3. Runs through phases: DESIGN → IMPLEMENT → VERIFY → MERGE
4. Creates a PR when done
5. Cleans up and grabs the next task

## Task Complexity

| Complexity | Phases | Estimated Cost (Opus) |
|-----------|--------|----------------------|
| Low (低) | FAST_TRACK → VERIFY → MERGE | ~$0.75 |
| Medium (中) | DESIGN_IMPLEMENT → VERIFY → MERGE | ~$2.25 |
| High (高) | DESIGN → IMPLEMENT → VERIFY → MERGE | ~$4.50 |

## Configuration

`workflow.yaml` in your project root:

```yaml
schema_version: 1

project:
  name: "My Project"
  main_branch: main
  gate_command: "npm test"

workers:
  max_concurrent: 3

models:
  default: opus
```

## Commands

| Command | Description |
|---------|-------------|
| `openniuma init` | Initialize in current project |
| `openniuma add <desc> -c 低\|中\|高` | Queue a task |
| `openniuma start` | Start orchestrator (foreground) |
| `openniuma start -d` | Start in background |
| `openniuma status` | View task status |
| `openniuma dashboard` | TUI dashboard |
| `openniuma doctor` | Check environment |
| `openniuma stop` | Graceful shutdown |
| `openniuma cancel <id>` | Cancel a task |

## Requirements

- Python >= 3.10
- Git
- [Claude Code](https://claude.ai/code) CLI

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

[MIT](LICENSE)
