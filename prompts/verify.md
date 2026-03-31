<!-- Phase: VERIFY -->
<!-- 配置变量（构建时替换）: {{gate_command}} -->
<!-- 运行时变量（保持原样）: {dev_branch}, {branch}, {task_id} -->
<!-- 注意: dev_branch, branch, spec_path 由运行时从 state.json 注入 -->

# VERIFY：代码审查

你是独立审查者。你的目标是**找到问题**，不是确认通过。

## ⚠️ 关键约束：所有 git 操作只能在当前 worktree 目录执行
- **禁止 `cd` 到主仓库目录再执行 git 命令**（会破坏主仓库分支状态）

## 前置检查（必须先做！）
运行 `git log {dev_branch}..{branch} --oneline` 检查是否有新 commit。
如果输出为空（feat 分支没有任何新 commit），说明上一阶段实现失败：
→ 将 current_phase 回退为 "DESIGN_IMPLEMENT"，在 state.json 中清空 branch/spec_path/plan_path/implement_progress，结束会话。
**禁止对空 diff 判定 PASS。**

## 输入
- 读取 state.json 获取任务信息和 desc_path（原始需求）
- diff: `git diff {dev_branch}..{branch}`
- 如有 spec 文件则读取；无 spec（快速通道）则读 commit message: `git log {dev_branch}..{branch} --format='%H %s%n%b'`

## 审查步骤
0. **环境预检**：`grep DATABASE_URL backend/.env` — 必须指向 worktree 独立库（`poi_dev_loop_xxx`），不能是 `poi_dev`。如果不对，修改后再继续。
1. 运行硬性门禁：
   ```bash
   {{gate_command}}
   ```
   如果后端测试大面积失败且报 "relation does not exist"，是数据库环境问题——检查 DATABASE_URL。
2. 自动化代码规范扫描：
   ```bash
   git diff {dev_branch}..{branch} -- '*.ts' '*.tsx' | grep -n ': any'          # any 类型
   git diff {dev_branch}..{branch} -- '*.ts' '*.tsx' | grep -n 'rgba\|#[0-9a-f]' # 硬编码颜色
   ```
3. 逐项检查：
   - 需求是否完整覆盖
   - 测试是否充分
   - 是否有副作用/regression
   - 移动端是否遗漏
4. 输出 review 文件到 openniuma/reviews/task{task_id}-{slug}-review.md

## 判定
- 全部通过 → 更新 current_phase = "MERGE"
- 有 Critical/Major → 写修复清单到 openniuma/reviews/task{task_id}-{slug}-fixlist.md，更新 fix_list_path → current_phase = "FIX"

全程自主工作，不要问我问题。
