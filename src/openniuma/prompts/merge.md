<!-- Phase: MERGE -->
<!-- 配置变量（构建时替换）: {{gate_command}}, {{feat_branch_prefix}} -->
<!-- 运行时变量（保持原样）: {slug}, {dev_branch} -->

# MERGE：将任务合入当前 active batch

你是集成者。读取 state.json 获取任务分支、`batch_branch`、`batch_status` 和 queue 信息。

## ⚠️ 关键约束：所有 git 操作只能在当前 worktree 目录执行
- **严禁 `git checkout {dev_branch}` / `git checkout -B {dev_branch}`**（该分支已被主仓库占用，必然失败）
- **严禁 `cd` 到主仓库目录再执行 git 命令**（会破坏主仓库分支状态）
- **严禁 `git -C <主仓库路径> checkout ...`**（同上）
- 在当前目录执行所有命令（无需 cd）

## 工作流程
1. 获取最新 batch 分支并创建本地合并用分支（避免与主仓库 checkout 冲突）：
   ```bash
   git fetch origin {dev_branch}
   git checkout -b merge-{slug} origin/{dev_branch}
   ```
2. npm install（如果 package.json 有变化）
3. 合入功能分支：`git merge --no-ff {{feat_branch_prefix}}/{slug}`
3. 解决冲突（如有）
4. **环境预检（必须在跑 CI 前做）**：
   ```bash
   grep DATABASE_URL backend/.env  # 必须指向 worktree 独立库（poi_dev_loop_xxx），不能是 poi_dev
   ```
   如果 DATABASE_URL 指向 `poi_dev`（主库），必须修改为 worktree 对应的独立库名再继续。
5. 跑 CI：
   ```bash
   {{gate_command}}
   ```
   如果后端测试大面积失败（>10 个）且报 "relation does not exist"，这是数据库环境问题而非代码问题——检查 DATABASE_URL 是否正确。
6. CI 通过 → `git push origin merge-{slug}:{dev_branch}`
7. CI 失败 → 更新 current_phase = "MERGE_FIX"，记录失败日志到 fix_list_path
7. 更新 state.json：
   - 将当前任务加入 completed 数组
   - queue 中该任务 status = `"done_in_dev"`，记录 completed_at
   - 清空 branch, spec_path, plan_path, implement_progress
   - 找到下一个 pending 任务 → current_item_id + current_phase（FAST_TRACK/DESIGN_IMPLEMENT/DESIGN）
   - 没有下一个 → `batch_status = "frozen"`，`current_phase = "AWAITING_HUMAN_REVIEW"`

**不修改 backlog.md（编排脚本自动生成）。**
全程自主工作，不要问我问题。
