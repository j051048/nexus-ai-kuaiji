# 前端部署更新与静态资源恢复

## 问题

前端发布后，浏览器可能继续运行旧版 `index-*.js`。旧代码会尝试加载带旧哈希的
`monthly-*.css` 或懒加载 chunk；这些文件已随新部署替换，于是出现：

```text
Unable to preload CSS for /assets/monthly-*.css
```

这是旧客户端与旧静态资源不再匹配，不是账务数据损坏或登录权限失效。

## 防线

1. `src/preload-recovery.ts` 监听 Vite 的 `vite:preloadError` 和未处理的动态导入错误；
2. 首次命中时，向当前 URL 添加 `_deploy_reload` 时间戳并执行一次无缓存重载；
3. 15 秒内重复失败不进入重载循环，交给 ErrorBoundary 显示可操作错误页；
4. `AppErrorBoundary` 同时作为根 React 错误边界和 React Router 的 `errorElement`，
   不再暴露默认技术堆栈；
5. 成功加载后清除恢复参数，保持工作区 URL 干净；
6. Vercel 对 `/assets/*` 使用 immutable 长缓存，对入口 HTML 和前端路由使用
   `no-cache, must-revalidate`。

## 回归

- `src/preload-recovery.test.ts`：识别 CSS/chunk 失效，同时不误判业务校验错误；
- `e2e/deploy-resource-recovery.spec.ts`：模拟 `vite:preloadError`，确认发生一次带时间戳的
  导航请求，并在新页面启动后清理恢复参数。
