# Vercel 配置验证 · 2026-10-13

部署拓扑已由用户选择：Vercel 仅运行静态前端和 API 网关，会计后端、数据库、私有附件及常驻 worker 在持久服务器。服务名与公开范围仍待用户最终确认，没有连接 Vercel 账号或部署到云端。

| 验证 | 结果 |
| --- | --- |
| 官方 vercel.json Schema 的配置字段校验与 CLI validator | 通过：app / api_gateway，顶层有序 service routes，零 bindings |
| 网关请求、响应、鉴权、二进制、大小、超时及跳转边界 | 82 项通过，3.08 秒 |
| 前端全量单元 | 30 文件、261 项通过 |
| TypeScript 和生产构建 | 通过；主入口约 496 kB，新增限制逻辑共用 |
| 官方 Vercel CLI 63.1.2 本地多服务 `dev -L` | 真实启动 Vite 与 Python 3.12.13 FastAPI 网关，识别两个服务 |
| 真实浏览器＋隔离外部会计服务 | 页面、登录／角色切换、API 401 JSON、no-store、3 MiB 超限提前拦截、真实 multipart 上传、手机布局通过 |

实际会计服务使用单独 SQLite 合成数据库与私有目录，仅绑定本机回环地址；本轮没有使用真实客户、生产环境、公网后端、数据库凭据或模型供应商。网关没有导入会计模块，也没有连接数据库或启动后台 worker。

本地联调发现并修复：顶层 rewrites 会进入 CLI 的 filesystem 阶段，仓库根目录 TSX／HTML 被作为原始静态文件返回，前端白屏。按官方 routes 配置改为先分派服务，再由 Vite 处理页面与模块，已用 CLI 路由转换、官方配置校验和真实浏览器复验。[官方路由说明](https://vercel.com/docs/project-configuration/vercel-json#routes)

验证文件与产物：`nexus_backend/tests/test_vercel_gateway.py`、`src/transport-limits.test.ts`、`src/api.test.ts`、`.codex/vercel-browser-smoke.mjs`、`artifacts/vercel-gateway-upload-desktop.png`、`artifacts/vercel-gateway-upload-mobile.png`。临时虚拟环境、Vercel 本地状态和合成私有原件已排除提交及上传。

实际公网会计后端 HTTPS 地址、Vercel 云端构建／域名、生产登录与来源白名单尚待配置和验证。大于 4 MiB 的原件／导出不能通过网关下载；前端 3 MiB 文件限制只在网关 metadata 存在时启用，原后端数据与直连上限保持。详见[部署说明](VERCEL_DEPLOYMENT.md)。
