/**
 * 衡账 PWA 轻量服务工作线程（财务安全：零业务数据缓存策略）。
 *
 * 设计原则：
 * 1. 严禁缓存任何 /api/* 响应、附件、身份或页面数据，避免多企业切账/撤权后残留陈旧财务信息。
 * 2. 所有请求默认实时直连网络，确保权限校验、凭证状态与报表版本与服务端强一致。
 * 3. 仅在彻底断网进行页面导航时，返回内置中文离线提示页，不伪造或展示历史金额。
 */

const OFFLINE_HTML = `<!doctype html>
<html lang="zh-CN">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover" />
    <meta name="theme-color" content="#183f35" />
    <title>衡账 · 网络已断开</title>
    <style>
      body {
        margin: 0;
        min-height: 100dvh;
        display: grid;
        place-items: center;
        padding: 24px;
        background: #f6f8f3;
        color: #1d2b24;
        font-family: -apple-system, BlinkMacSystemFont, "PingFang SC", "Microsoft YaHei", sans-serif;
      }
      main {
        width: min(100%, 400px);
        padding: 28px 24px;
        border: 1px solid #dce4d7;
        border-radius: 16px;
        background: #fffefa;
        box-shadow: 0 8px 28px rgba(24, 63, 53, 0.08);
        text-align: center;
      }
      .mark {
        display: inline-grid;
        place-items: center;
        width: 52px;
        height: 52px;
        margin-bottom: 16px;
        border-radius: 14px;
        background: #183f35;
        color: #ecf1dd;
        font-size: 20px;
        font-weight: 700;
      }
      h1 {
        margin: 0 0 10px;
        font-size: 19px;
      }
      p {
        margin: 0 0 20px;
        color: #536153;
        font-size: 14px;
        line-height: 1.7;
      }
      button {
        width: 100%;
        min-height: 44px;
        border: 0;
        border-radius: 10px;
        background: #183f35;
        color: #fffefa;
        font-size: 15px;
        font-weight: 600;
        cursor: pointer;
      }
    </style>
  </head>
  <body>
    <main>
      <div class="mark" aria-hidden="true">衡</div>
      <h1>当前网络已断开</h1>
      <p>为保障账务与权限状态准确，衡账不会在离线状态下展示旧财务数据。请检查手机网络后重新连接。</p>
      <button type="button" onclick="location.reload()">重新连接</button>
    </main>
  </body>
</html>`;

self.addEventListener('install', () => {
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    (async () => {
      if (typeof caches !== 'undefined') {
        const keys = await caches.keys();
        await Promise.all(keys.map((key) => caches.delete(key)));
      }
      await self.clients.claim();
    })(),
  );
});

self.addEventListener('fetch', (event) => {
  const { request } = event;
  if (request.method !== 'GET') return;
  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;
  if (url.pathname.startsWith('/api/')) return;
  if (request.mode !== 'navigate') return;

  event.respondWith(
    fetch(request).catch(
      () =>
        new Response(OFFLINE_HTML, {
          status: 503,
          headers: {
            'Content-Type': 'text/html; charset=utf-8',
            'Cache-Control': 'no-store',
          },
        }),
    ),
  );
});
