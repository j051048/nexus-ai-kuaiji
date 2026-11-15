# 批次 M 只读盘点（2026-11-12，未启用）

## 结论

M 批次**未执行**。`docs/TAXI_DEEPSEEK_BATCH_M_20261110.md` 明确：只有用户单独说“现在执行 M 批”或等义指令才可改依赖。用户当前要求完成 F–M，但前序 J/K/L 未收口，且 M 要求 I–L 必需本地门槛通过；不满足启动条件。

本轮**未**修改 `package.json`、`package-lock.json`、`node_modules`、React 版本、Vite/TypeScript/测试框架。

## 当前依赖

- react / react-dom：**18.3.1**
- @types/react / @types/react-dom：**18.3.23 / 18.3.7**
- react-router-dom：^7.18.4
- vite：7.3.6；@vitejs/plugin-react-swc：3.11.0
- vitest：4.1.0；Playwright 1.58.2；TypeScript 5.8.3
- 入口已使用 `createRoot`、`StrictMode`、现代 JSX。

## 启动 M 前必须完成

1. 修通 K 的财务核验→交接→复核→导出链，并让 J/L 的必需本地门槛同候选通过。
2. 冻结升级前候选与基线（JS/CSS、首屏、关键交互、浏览器）。
3. 读取执行时 React 19 稳定版本、官方升级指南、peerDependencies 与安全公告；不得用 canary/RC。
4. 保持后端/迁移/SQL/权限/复核边界不变；不引入 Server Components/Actions/Compiler。
5. 逐项跑 typecheck、单测、lint、build、budget、dev/preview 核心浏览器与 PG16/17 子集；不得用 `--force`/`--legacy-peer-deps` 或放宽门禁。

## 风险预判（未验证）

- 类型包需升级到 React 19 对应版本；现有 `@types/react` 18 不能直接验证 19 API。
- `react-router-dom` 7、lucide-react 0.462、SWC 插件与 Vitest 的 React 19 兼容性需按实际安装树验证。
- `useRef` 必填初值、ref 清理函数、`ReactElement` 类型、错误边界与 StrictMode 双挂载可能暴露真实兼容问题。
- 现有 `skipLibCheck` 保留，不得借此隐藏新增类型错误。

## 状态

- 外部验收：不适用。
- 依赖变更：无。
- 候选：沿用前端 `2156/6400426…`。
