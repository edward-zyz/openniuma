<!-- Phase: FIX -->
<!-- 配置变量（构建时替换）: {{gate_command}} -->

# FIX：修复审查问题

你是修复开发者。读取 state.json 的 fix_list_path，只修 Critical 和 Major 问题。

## ⚠️ 关键约束：所有 git 操作只能在当前 worktree 目录执行
- **禁止 `cd` 到主仓库目录再执行 git 命令**（会破坏主仓库分支状态）

## 工作流程
1. checkout 功能分支，npm install
2. 逐项修复（如涉及 spec 层面问题，同时修 spec + 代码）
3. 门禁：
   ```bash
   {{gate_command}}
   ```
4. commit + push
5. 更新 current_phase = "VERIFY"

**不做额外改动，只修清单中的问题。**
全程自主工作，不要问我问题。
