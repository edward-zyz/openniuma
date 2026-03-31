# Fix List: Copilot Co-creation

**Source:** `openniuma/reviews/2-copilot-cocreation-review.md`
**Priority:** Critical → Major（按顺序修复）

---

## Critical Fixes (必须修复)

### Fix 1: 移动端 Copilot 入口
- 在移动端 TopBar 或底部区域添加 Copilot 入口按钮
- 点击后设置 `showMobileCopilot = true`
- 确保 MobileCopilotPage 可正常打开和关闭

### Fix 2: SSE 流式读取器清理
- `sendCopilotMessage` 中用 try-finally 包裹 stream 读取循环
- finally 中调用 `reader.cancel()`
- 接受外部传入的 `AbortSignal`，fetch 时传递

### Fix 3: useCopilot hook 卸载清理
- 在 hook 内维护 `AbortController` ref
- `sendMessage` 每次调用时创建新 controller，旧的 abort
- useEffect cleanup 时 abort 当前 controller

---

## Major Fixes (应该修复)

### Fix 4: Admin feedback 详情 workspace 鉴权
- `GET /feedbacks/:id` 增加 `feedback.workspaceId` 与当前用户 workspace 的校验
- 返回 403 如果不匹配

### Fix 5: Prompt 版本更新事务化
- `updateStage()` 方法使用 `pool.connect()` + `BEGIN` + `SELECT ... FOR UPDATE` + `COMMIT`
- 确保版本递增是原子操作

### Fix 6: SSE 网关错误处理
- JSON 解析失败时 log warning 而非静默忽略
- fetch AI 网关时添加 `AbortSignal.timeout(30000)`
- 检查 `res.writableEnded` 后再 `res.end()`

### Fix 7: 移动端触控区域 & 文字大小
- ChatView "结束对话" 按钮添加 `min-h-[44px]`
- 移动端交互元素确保 ≥ 44px 触控区域
- `text-xs` 在移动端改为 `text-sm`

### Fix 8: 硬编码颜色替换为 design tokens
- `ChatBubble.tsx`: `bg-blue-500` → `bg-primary`，`bg-gray-100` → `bg-muted`
- `RouteSelector.tsx`: `text-blue-500` → `text-primary`，`text-violet-500` → `text-accent`（或对应 token）
- `SessionList.tsx`: `bg-blue-50 text-blue-600` → `bg-primary/10 text-primary`
