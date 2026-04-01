## 硬性红线
- 禁止合并代码到 {{main_branch}}（禁止 git merge/push/checkout {{main_branch}}，禁止 gh pr merge）
- 禁止请求用户输入或等待用户操作（非交互模式）
- 后端 ESM：本地 import 必须用 .js 扩展名
- 切分支后必须 npm install
- 业务逻辑使用中文注释

## 硬性门禁（每个 Task 完成后必须全部 exit 0）
```bash
{{gate_command}}
```

## 时间格式
全栈统一 ISO 8601 字符串，禁止 Unix 时间戳（poi_cache.fetched_at 除外）。

## 移动端
App.tsx 中 PC/移动端是独立组件树。新 UI 功能必须同时实现两端。

## UI 规范
- 新代码用 Tailwind，用 cn() 合并 class，颜色取 tokens.json
- Modal: fixed inset-0 bg-black/60 z-50, rounded-2xl
- Button: 用 components/ui/button.tsx
