# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- Initial project structure
- Python CLI entry point (`openniuma` command)
- Core modules: state, config, failure classification, retry, reconcile
- Built-in prompt templates for all development phases
- `openniuma init` — interactive project setup
- `openniuma add` — queue new tasks
- `openniuma start` — launch orchestrator (Bash engine)
- `openniuma status` — view task status
- `openniuma doctor` — environment diagnostics
- `openniuma dashboard` — Textual TUI dashboard
- Tech stack auto-detection (Node/Python/Go/Rust/Ruby)
- Multi-channel notifications (macOS, Bell, Feishu)
