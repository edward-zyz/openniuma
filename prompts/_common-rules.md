## 硬性红线
- 禁止合并代码到 {{main_branch}}（禁止 git merge/push/checkout {{main_branch}}，禁止 gh pr merge）
- 禁止请求用户输入或等待用户操作（非交互模式）
- 后端 ESM：本地 import 必须用 .js 扩展名
- 切分支后必须 npm install
- 业务逻辑使用中文注释
- 全程自主工作，不要问我问题

## Worktree 安全约束
- **禁止 `cd` 到主仓库目录再执行 git 命令**（会破坏主仓库分支状态）
- **禁止 `git -C <主仓库路径> checkout ...`**（同上）

## 硬性门禁（每步必须全部 exit 0）
```bash
{{gate_command}}
```

## 环境预检（跑门禁前必须确认）
`grep DATABASE_URL backend/.env` — 必须指向 worktree 独立库（`poi_dev_loop_xxx`），不能是 `poi_dev`。
如果后端测试大面积失败且报 "relation does not exist"，是数据库环境问题——检查 DATABASE_URL。

## 时间格式
全栈统一 ISO 8601 字符串，禁止 Unix 时间戳（poi_cache.fetched_at 除外）。

## 移动端
App.tsx 中 PC/移动端是独立组件树。新 UI 功能必须同时实现两端。

## UI 规范
- 新代码用 Tailwind，用 cn() 合并 class，颜色取 tokens.json
- Modal: fixed inset-0 bg-black/60 z-50, rounded-2xl
- Button: 用 components/ui/button.tsx
