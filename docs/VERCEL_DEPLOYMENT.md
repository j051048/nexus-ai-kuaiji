# Vercel 前端与 API 网关

按用户确认的分工：Vercel 部署 Vite 前端与轻量 API 网关，实际会计 FastAPI、PostgreSQL、私有持久附件及 OCR／迁移／对话 worker 保留在持久服务器。服务名称、公开范围与调用关系仍待用户最终确认；本轮没有执行云端部署。

| 服务 | 根目录 | 构建／入口 | 公开路径 |
| --- | --- | --- | --- |
| app | . | Vite，npm run build，输出 dist | / 及非 API 页面／资源 |
| api_gateway | services/api_gateway | FastAPI，main:app，Python 3.12 | /api、/api/* |

根目录 vercel.json 使用有顺序的 routes，先将 API 分派到网关，再把其他路径交给前端服务。保留原始 /api 前缀与 query，匹配现有 src/api.ts 与外部会计路由。未知 API 不会回退为前端 HTML。

本地 Vercel CLI 63.1.2 的 rewrites 会在 filesystem 阶段执行，仓库根部的原始 TSX／HTML 会抢先命中静态文件，跳过 Vite，造成白屏。改用顶层 routes 确保先进入各服务，再由服务处理文件和路由；没有使用废弃 handle/override 字段。[官方 routes 说明](https://vercel.com/docs/project-configuration/vercel-json#routes)

## 调用链与 bindings

浏览器 → Vercel 公开 API 网关 → 外部持久会计服务器。

app 是纯静态前端，没有服务端函数调用另一个 Vercel service；外部后端也不属于当前 services，所以本轮不添加无效 binding。ACCOUNTING_BACKEND_ORIGIN 是手动配置的服务器端外部 origin。未来若新增真正的内部服务，应在实际调用它的 service 上声明 binding，函数运行时读取 Vercel 注入的 URL；不能把绑定地址编译到 VITE_*、在构建或 middleware 使用。[官方 binding 说明](https://vercel.com/docs/services/bindings)

## Vercel 环境变量

```dotenv
ACCOUNTING_BACKEND_ORIGIN=https://your-accounting-backend.example
ACCOUNTING_GATEWAY_TIMEOUT_SECONDS=110
ACCOUNTING_GATEWAY_ALLOW_LOCAL_HTTP=0
```

origin 只允许 HTTPS 主机地址，可带端口，不包含 /api、密码、query 或 fragment；缺配置返回明确 503，不能回退到 localhost。变量仅由网关读取，不发送给浏览器。Vercel 不需要会计数据库、模型密钥或 worker 数据库凭据。

网关保留 Authorization、X-Firm-Id、Content-Type、Accept、Origin。上游 Host 从服务器配置产生，忽略浏览器 Host、Cookie 和 forwarding headers；真正的登录及企业／账套授权仍由会计后端检查。API 响应 no-store，网关不跟随跳转、不自动重试、不记录令牌和原件。完整读取／外发／接收 deadline 默认 110 秒，可设 1–110；函数 maxDuration 为 120 秒。超时写操作可能已完成，先查询任务或回执，再沿原幂等流程恢复。

## 持久服务器

保留 Supabase 真实登录、受限 PostgreSQL 与显式 TLS、私有原件存储及独立 worker。原件可在同一持久服务器目录保存，也可供 Zeabur 独立服务通过 Supabase 私有 bucket 共用，具体见 [云端 Demo 部署](DEMO_VERCEL_ZEABUR_SUPABASE.md)。ACCOUNTING_ALLOWED_HOSTS 包含实际后端域名；ACCOUNTING_ALLOWED_ORIGINS 包含前端的 HTTPS 来源，需要预览时明确添加相应来源，不开放通配符。管理员迁移继续显式执行，不作为 Vercel buildCommand。网关没有导入会计应用，不创建 SQLite、原件目录或常驻线程。

实际后端参数见 [.env.accounting.example](../.env.accounting.example) 和 [数据库接入](POSTGRES_SETUP.md)。Vercel 只配置外部后端 origin，不填写 PostgreSQL、Storage secret 或模型密钥。

## 上传与下载限制

Vercel Functions 请求／响应上限 4.5 MB。网关保守限制每次请求与响应 4 MiB，成功的 /api/auth/config 响应下发 transport_limits，使浏览器将单文件上限动态收紧到 3 MiB，为 multipart 留余量；原来 2 MB 的导入、回执保持更小值。[官方限制](https://vercel.com/docs/functions/limitations)

员工录入、补件、旧账、流水等提前显示当前上限，并检查 JSON UTF-8 字节及 FormData 文件合计。直连原后端且没有网关 metadata 时，原单文件 10 MB 等限制保持。客户端校验只是体验帮助，网关继续执行最终流式大小检查。

既有大于 4 MiB 的原件或导出不能走本网关下载，原文件没有被压缩、替换或删除；管理员需要使用持久服务器的授权下载流程。后续可另做短期授权的私有对象直传／直读，这项本轮没有实现。

## 本地联调

先用隔离数据库启动持久会计后端，再显式设置本地环境：

```powershell
$env:ACCOUNTING_BACKEND_ORIGIN = 'http://127.0.0.1:8000'
$env:ACCOUNTING_GATEWAY_ALLOW_LOCAL_HTTP = '1'
npx vercel@63.1.2 dev -L
```

-L 不连接 Vercel 账号；生产不启用本地 HTTP。CLI 不自动读取旧平台环境。普通 npm run dev 继续使用 ACCOUNTING_API_PROXY 本地代理，这个变量不参与生产网关路由。[官方本地服务开发](https://vercel.com/docs/services)

.vercelignore 排除原会计后端、私有数据、旧平台快照和本地产物；独立网关仅需 FastAPI/httpx。各服务自动生成的 .venv/.vercel 被 Git 和上传规则排除。

本轮结果见 [验证记录](VALIDATION_VERCEL_20261013.md)。实际公网后端 HTTPS 地址、云端构建与运行尚待提供并验证。
