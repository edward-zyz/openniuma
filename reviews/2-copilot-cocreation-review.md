# Code Review: Copilot Co-creation Feature

**Branch:** `feat/copilot-cocreation` (10 commits)
**Reviewer:** Claude (automated)
**Date:** 2026-03-28
**Verdict:** ❌ FAIL — 3 Critical + 5 Major issues

---

## 硬性门禁

| Check | Result |
|-------|--------|
| `npm run lint` | ✅ 0 errors (95 warnings, 均为存量代码) |
| `npm test` | ✅ 306 tests passed (49 files) |
| `npm run build` | ✅ 前后端构建成功 |
| `npx tsc --noEmit -p frontend` | ✅ 类型检查通过 |
| `: any` 新增 | ✅ diff 中无新增 any |
| 硬编码颜色 | ⚠️ 存量 AuthPage 代码，非本次新增 |

---

## Critical Issues

### C1. 移动端入口缺失 — Copilot 在移动端不可达
- **位置:** `App.tsx`, `usePoiStore.ts`
- **问题:** `showMobileCopilot` 状态已创建但**无任何 UI 触发器**将其设为 true。PC 端有 MessageCircle 按钮（PointsPanel）和 Training 入口（WorkspaceSettingsModal），移动端完全没有对应入口。
- **违反:** CLAUDE.md — "每个面向用户的新功能必须同时实现 PC 端和移动端"
- **修复:** 在移动端 TopBar 或底部工具栏添加 Copilot 入口按钮

### C2. SSE 流式读取器未清理 — 内存泄漏
- **位置:** `frontend/src/services/api.ts:1491-1537` (`sendCopilotMessage`)
- **问题:** `ReadableStreamDefaultReader` 在 error/early-exit 路径不会 cancel。用户中途关闭面板时 fetch 继续消费字节。
- **修复:** 使用 try-finally + `reader.cancel()`；传入 AbortController signal

### C3. 组件卸载时流式状态未清理
- **位置:** `frontend/src/hooks/useCopilot.ts`
- **问题:** `sendMessage` 无 AbortController，组件卸载后回调仍然触发 setState，可能导致状态损坏。
- **修复:** hook 内维护 AbortController ref，useEffect cleanup 时 abort

---

## Major Issues

### M1. Admin 反馈详情缺少 workspace 鉴权
- **位置:** `backend/src/routes/adminCopilotRoutes.ts` GET `/feedbacks/:id`
- **问题:** 通过 ID 获取任意 feedback，未校验 workspace 归属。跨工作空间数据泄漏风险。

### M2. Prompt 版本更新存在竞态条件
- **位置:** `backend/src/storage/copilotPromptRepository.ts` `updateStage()`
- **问题:** 读当前版本 → 标记旧版本 inactive → 插入新版本，三步非原子。并发更新可产生重复 active 版本。
- **修复:** 使用事务 + `SELECT ... FOR UPDATE`

### M3. SSE 网关错误处理不完整
- **位置:** `backend/src/services/copilotService.ts` `chat()` 方法
- **问题:** `catch { }` 静默吞掉 JSON 解析错误；AI 网关超时无 AbortSignal；连接异常后 `res.end()` 可能重复调用。

### M4. 移动端触控区域不足 44px
- **位置:** `ChatView.tsx` "结束对话" 按钮 `py-1.5`，远低于 44px 最小触控区域。
- **违反:** CLAUDE.md — "移动端触控区域 ≥ 44px"

### M5. 硬编码 Tailwind 颜色 — 未使用 design tokens
- **位置:** `ChatBubble.tsx` (`bg-blue-500`, `bg-gray-100`)、`RouteSelector.tsx` (`text-blue-500`, `text-violet-500`)、`SessionList.tsx` (`bg-blue-50`)
- **违反:** CLAUDE.md — "颜色取自 tokens.json / Tailwind theme，禁止硬编码颜色值"

---

## Minor Issues

- **m1.** `LIMIT/OFFSET` 使用字符串插值而非参数化 (`copilotFeedbackRepository.ts`)
- **m2.** Feedback `updateStatus()` 无 `updated_at` / `updated_by` 审计字段
- **m3.** Prompt 模板注入用户名未转义 `{{` / `}}` (`copilotService.ts:buildSystemPrompt`)
- **m4.** `CopilotPanel` 固定 `w-[480px]`，小屏幕溢出视口
- **m5.** Admin 子组件无独立 error state 展示（API 失败时空白）
- **m6.** `RouteSelector` 未使用 `cn()` 合并条件 class
- **m7.** 移动端部分文字 `text-xs` (12px)，低于 16px 最低要求

---

## 测试覆盖度

| 层级 | 覆盖率 | 备注 |
|------|--------|------|
| DB Migration | ✅ 充分 | 4 表 + seed 数据验证 |
| Repository CRUD | ✅ 充分 | 4 个 repo 全部 CRUD 已测 |
| Service Layer | ⚠️ 部分 | session 管理已测；`chat()` SSE 流式和 `extractFeedback()` 未测 |
| HTTP Routes | ❌ 缺失 | 用户端 + Admin 端 0 个集成测试 |
| Frontend | ❌ 缺失 | 无任何 copilot 组件测试 |

---

## 判定

**❌ FAIL** — 存在 3 个 Critical 和 5 个 Major 问题，需进入 FIX 阶段。
