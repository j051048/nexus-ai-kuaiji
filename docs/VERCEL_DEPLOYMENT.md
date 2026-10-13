# Vercel 多服务配置

根目录 `vercel.json` 配置两个独立构建服务。名称及公开范围仍待用户确认；本轮没有连接 Vercel 账号、上传资料或执行云端部署。

| 服务 | 根目录 | 构建／入口 | 公开路径 |
| --- | --- | --- | --- |
| `app` | `.` | Vite，`npm run build`，输出 `dist` | `/` 及非 API 路径 |
| `nexus_backend` | `nexus_backend` | FastAPI，`app.main:app`，Python 3.12 | `/api`、`/api/*` |

API 规则在前，前端 catch-all 最后；Vercel 保留原始 `/api` 前缀，与后端路由及 `src/api.ts` 的同域请求一致。未知 API 路径留在后端返回错误，不能改写成前端 HTML。数据库迁移 SQL 必须包含在 Python 函数包中，因为运行服务会核对已应用迁移的完整校验和。单次函数设置 120 秒，以覆盖已有最长 90 秒模型请求及前后校验，仍以实际 Vercel 套餐限制为准。[官方路由](https://vercel.com/docs/services/routing)、[配置参考](https://vercel.com/docs/services/config-reference)

## 为什么当前没有 service binding

`app` 是纯静态 Vite SPA，请求来自用户浏览器，经同域 `/api/*` 公开路由进入后端。仓库没有前端服务端函数调用另一个 Vercel service，因此没有可读取 binding 的调用方。`ACCOUNTING_API_PROXY` 和 `127.0.0.1:8000` 只用于本地 Vite 开发代理，构建后的前端不依赖它们。

绑定仅在调用服务的 functions 运行时解析；不能设置成 `VITE_*` 或编译进浏览器，也不能在构建或 middleware 中读取。如果希望 `nexus_backend` 保持 internal，必须增加真正的服务端网关，将 binding 声明在该调用服务，并在运行时读取 Vercel 注入的 URL。这是另一种部署拓扑，需要用户确认，当前没有添加无效或未使用的绑定。[官方绑定说明](https://vercel.com/docs/services/bindings)

## 真实账务部署尚待解决

这份配置解决入口、独立构建和路由，**不代表当前完整会计后端已适合 Vercel Functions**：

- 当前附件、员工预填原件、中文导入和旧账迁移都使用持久私有文件目录。Vercel 实例临时文件系统不能作为共享原件库；把 `ACCOUNTING_ATTACHMENT_ROOT` 改成 `/tmp` 会丢失或无法读取原件。完整 API 部署前须统一改造私有对象存储，或者保留 API 与 worker 在持久服务器。
- OCR、迁移和会计对话 worker 是常驻进程。应继续独立部署并共用 PostgreSQL 与私有存储；不能在 Vercel API lifespan 中启动它们，也不能依赖实例 shutdown 完成任务。
- 当前单个附件上限 10 MB，Vercel Functions 请求／响应上限 4.5 MB。须改成经过归属与 SHA 校验的私有对象直传／读取，或统一收窄产品和 API 文件限制；仅修改 JSON 路由不会解决这个问题。

这些是对现有代码与平台限制的判断，不是在本轮偷偷改写存储或任务系统。[函数限制](https://vercel.com/docs/functions/limitations)、[Python 容器与持久状态说明](https://vercel.com/kb/guide/vercel-docker-python-apps)

## 环境与验证

真实后端仍须使用：`ACCOUNTING_AUTH_MODE=supabase`、受限 PostgreSQL `ACCOUNTING_DATABASE_URL`（显式 TLS）、新的 Supabase URL／公开 Auth key、明确 `ACCOUNTING_ALLOWED_HOSTS` 及 HTTPS `ACCOUNTING_ALLOWED_ORIGINS`。API 中关闭 `ACCOUNTING_QUEUE_EMBEDDED_WORKER`，管理员数据库及 worker 数据库身份不注入 API。数据库迁移在部署外显式执行，不能作为 Vercel buildCommand。完整参数见 `.env.accounting.example` 与 `docs/POSTGRES_SETUP.md`。

本地多服务联调使用当前 Vercel CLI：

```powershell
vercel dev -L
```

这是不连接 Vercel 账号的本地模式；绑定会由 CLI 自动生成（如以后添加真实调用方）。当前环境变量须以明确方式载入，不能复用旧平台 `.env`。普通 `npm run dev` 继续使用原来的本地开发代理。[官方 Services 本地开发](https://vercel.com/docs/services)

本轮校验包括官方配置 JSON Schema、前端构建及同域 API 请求回归。云端依赖打包、Supabase 环境和多服务实际启动尚未验证；部署前需先确认服务拓扑与持久存储方案。
