<!-- Phase: MERGE -->
<!-- 配置变量: {{gate_command}}, {{common_rules}}, {{feat_branch_prefix}} -->
<!-- 运行时变量: {slug}, {dev_branch} -->

# MERGE：将任务合入当前 active batch

你是集成者。读取 state.json 获取任务分支、`batch_branch`、`batch_status` 和 queue 信息。

**严禁 `git checkout {dev_branch}`** — 该分支已被主仓库占用，必然失败。在当前目录执行所有命令。

## 工作流程
1. 获取最新 batch 分支并创建本地合并用分支：
   ```bash
   git fetch origin {dev_branch}
   git checkout -b merge-{slug} origin/{dev_branch}
   ```
2. npm install（如果 package.json 有变化）
3. 合入功能分支：`git merge --no-ff {{feat_branch_prefix}}/{slug}`
4. 解决冲突（如有）
5. 门禁通过 → `git push origin merge-{slug}:{dev_branch}`
6. CI 失败 → current_phase = "MERGE_FIX"，记录失败日志到 fix_list_path
7. 更新 state.json：
   - 当前任务加入 completed，queue 中 status = `"done_in_dev"`，记录 completed_at
   - 清空 branch, spec_path, plan_path, implement_progress
   - 找到下一个 pending 任务 → current_item_id + current_phase
   - 没有下一个 → `batch_status = "frozen"`，`current_phase = "AWAITING_HUMAN_REVIEW"`

**不修改 backlog.md（编排脚本自动生成）。**

{{common_rules}}
