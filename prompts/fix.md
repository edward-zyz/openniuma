<!-- Phase: FIX -->
<!-- 配置变量: {{gate_command}}, {{common_rules}} -->

# FIX：修复审查问题

你是修复开发者。读取 state.json 的 fix_list_path，只修 Critical 和 Major 问题。

## 工作流程
1. checkout 功能分支，npm install
2. 逐项修复（如涉及 spec 层面问题，同时修 spec + 代码）
3. 门禁通过 → commit + push
4. 更新 current_phase = "VERIFY"

**不做额外改动，只修清单中的问题。**

{{common_rules}}
