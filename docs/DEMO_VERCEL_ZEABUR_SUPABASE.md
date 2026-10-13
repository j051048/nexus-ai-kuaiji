# Vercel + Zeabur + Supabase Demo 部署

本配置用于 2–3 家企业的辅助记账试点：Vercel 提供前端与同域 API 网关，Zeabur 提供常驻 API 和三个独立后台进程，Supabase 提供 PostgreSQL、真实登录和私有原件存储。API/worker 不依赖共享磁盘；`/tmp` 只存放短期处理数据。代码和本地测试不等同于已部署云资源或已验证真实供应商。

## 0. 从零准备

准备 GitHub、Supabase、Zeabur、Vercel 账号，确认用于 Demo 的 GitHub 分支已包含本次改动。云平台读取 GitHub 分支中的代码，不会读取本机尚未推送的文件。云项目需按以下步骤创建。首次部署按 Supabase → Zeabur → Vercel 的顺序；已知 Vercel 最终域名后，回填 Supabase Auth 与 Zeabur API 的来源白名单。

本机准备 Python 3.11+ 与独立虚拟环境，安装 `nexus_backend/requirements.txt`，供一次性迁移和自检使用：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r nexus_backend/requirements.txt
```

下文 `python` 均指此虚拟环境中的 Python；如未激活，使用 `.\.venv\Scripts\python.exe` 替换。云端 Dockerfile 使用 Python 3.12。Docker Compose 是可选的本机联调方式，不是创建云项目的前置条件。

## 服务分工

| 平台与服务 | 公网入口 | 配置职责 |
| --- | --- | --- |
| Vercel `app` | `/` | Vite 前端，不配置数据库或模型密钥 |
| Vercel `api_gateway` | `/api`、`/api/*` | 服务端 `ACCOUNTING_BACKEND_ORIGIN=https://…zeabur.app` |
| Zeabur `accounting-api` | HTTPS 域名，路由仍为 `/api/*` | `ACCOUNTING_SERVICE_ROLE=api`，一个受限 API DSN |
| Zeabur `accounting-worker` | 不绑定域名 | `ACCOUNTING_SERVICE_ROLE=worker`，OCR/分录后台 DSN |
| Zeabur `accounting-assistant` | 不绑定域名 | `ACCOUNTING_SERVICE_ROLE=assistant`，助手后台 DSN |
| Zeabur `accounting-migration` | 不绑定域名 | `ACCOUNTING_SERVICE_ROLE=migration`，旧账后台 DSN |

四个 Zeabur 服务从同一仓库根目录的 `Dockerfile` 构建，以非 root UID `10001` 运行。入口读取 Zeabur 注入的 `PORT`，API 监听 `0.0.0.0`；worker 初始化成功后才开启同端口的内部 `/healthz` 探针，只返回固定 `process_running`，不提供会计 API。只给 API 绑定公网域名；三个后台不绑定域名、不启用公网 TCP 转发。Zeabur 官方支持 [Git 仓库 Dockerfile](https://zeabur.com/docs/en-US/deploy/methods/dockerfile)，根目录 `zbpack.json` 明确使用 `Dockerfile`；不把 `Root Directory` 改为 `nexus_backend`。

## 1. 准备 Supabase

创建专用于本 Demo 的新项目。取得项目 URL、publishable 公钥和 PostgreSQL 连接地址。长驻容器优先直连，IPv4 网络可用 session pooler；从项目 Connect 面板复制实际地址，不能猜 pooler 域名。pooler 用户名是 `账号名.PROJECT_REF`，直连用户名是 `账号名`，连接须包含 `sslmode=require` 或证书校验。参考 [Supabase 连接说明](https://supabase.com/docs/guides/database/connecting-to-postgres)。

在本地私密维护终端设置 `ACCOUNTING_ADMIN_DATABASE_URL`，安装 `nexus_backend/requirements.txt`，显式执行：

```powershell
python scripts/migrate_accounting.py status
python scripts/migrate_accounting.py migrate
python scripts/migrate_accounting.py provision-login --username accounting_api
python scripts/migrate_accounting.py provision-worker-login --username accounting_ocr
python scripts/migrate_accounting.py provision-worker-login --username accounting_assistant
python scripts/migrate_accounting.py provision-worker-login --username accounting_migration
Remove-Item Env:ACCOUNTING_ADMIN_DATABASE_URL
```

每次创建账号输入独立的至少 20 字符密码，脚本隐藏输入；不要把密码放命令参数。已有同名账号不会被覆盖。API 继承 `accounting_app/accounting_system`，后台继承 `accounting_app/accounting_worker`，无超级用户、创建角色或绕过 RLS 权限。管理员 DSN 仅在迁移终端使用，不注入任何运行服务。应用启动只验证迁移摘要，不自动执行 PostgreSQL 迁移。

本版本 PostgreSQL 迁移包含 `072_assistant_schedule_isolation.sql`，为内部公平调度表补齐 FORCE RLS；既有版本只追加迁移，不修改旧文件。确认 `status` 中所有版本已应用，再启动服务。新项目和已有 Demo 项目都使用相同显式迁移步骤。

Supabase 托管 PostgreSQL 的角色权限需实际执行后确认：若管理员不允许建立固定角色、转交权限函数或 `BYPASSRLS` 所有者，先按 SQL 错误排查；不可为方便而让 API/worker 使用 `postgres` 管理员。完整背景见 [PostgreSQL 设置](POSTGRES_SETUP.md)。`accounting` schema 不加入 Data API 的 exposed schemas。

在 Storage 新建 `accounting-originals`，**Public bucket 关闭**，设置最大单文件至少 `3 MiB`，兼容使用的票据/CSV/JSON/XLSX MIME 类型或保持 bucket 类型限制空白。设置 `ACCOUNTING_STORAGE_SERVER_KEY` 为服务端 secret（或 legacy `service_role`），仅放四个 Zeabur 服务。publishable/anon 公钥不能用于原件存储；服务端密钥不进浏览器、不进 Vercel。原件必须经后端当前业务授权读取，不生成公共 URL，也不配置浏览器直接访问该 bucket。该专用 bucket 不添加 `anon/authenticated` 对对象的通用读取或上传策略；如复用有其他应用的 Supabase 项目，须检查已有 `storage.objects` policies 是否意外覆盖此 bucket。`--online` 只确认 `public=false`，不代替完整策略检查。参考 [Supabase Storage 访问控制](https://supabase.com/docs/guides/storage/security/access-control)。

在 Auth 配置 Vercel 前端 Site URL 和准确的允许重定向地址，启用 Email / Password。在 Supabase Dashboard 的 Authentication / Users 创建少量已确认邮箱的 Demo 用户，设置各自的密码；也可使用已配置邮件服务的邀请流程。至少准备事务所负责人、企业员工、部门审批人、企业财务、会计复核人账号，测试时可以复用跨企业会计身份，但不能让同一人审批自己提交的业务。当前前端是登录入口，首次开户通过 Dashboard 完成。每个用户先登录一次，再由负责人在“成员与权限”里授权；登录不自动授予企业/账套数据。

## 2. 创建 Zeabur 四服务

从同一 GitHub 仓库创建四个 Git 服务，Root Directory 保持仓库根目录。不要使用 `nexus_backend` 子目录构建；不要设置 `ZBPACK_IGNORE_DOCKERFILE=true`。每服务配置以下公共项：

```dotenv
ACCOUNTING_ENV=production
ACCOUNTING_SEED=0
ACCOUNTING_QUEUE_EMBEDDED_WORKER=0
ACCOUNTING_STORAGE_BACKEND=supabase
ACCOUNTING_STORAGE_SUPABASE_URL=https://PROJECT_REF.supabase.co
ACCOUNTING_STORAGE_BUCKET=accounting-originals
ACCOUNTING_STORAGE_SERVER_KEY=sb_secret_…
ACCOUNTING_STORAGE_TIMEOUT_SECONDS=20
ACCOUNTING_STORAGE_ALLOW_HTTP=false
```

远端存储模式不配置 `ACCOUNTING_ATTACHMENT_ROOT`，不挂持久 Volume。四服务使用同一私有 bucket；原件与数据库一起纳入备份计划。Storage 的服务端密钥可读取整个 bucket，应用业务授权必须持续生效。

API 独有配置：

```dotenv
ACCOUNTING_SERVICE_ROLE=api
ACCOUNTING_AUTH_MODE=supabase
ACCOUNTING_DATABASE_URL=受限 accounting_api 账号的 PostgreSQL TLS DSN
ACCOUNTING_SUPABASE_URL=https://PROJECT_REF.supabase.co
ACCOUNTING_SUPABASE_PUBLISHABLE_KEY=sb_publishable_…
ACCOUNTING_ALLOWED_HOSTS=YOUR_API_DOMAIN.zeabur.app
ACCOUNTING_ALLOWED_ORIGINS=https://YOUR_FRONTEND.vercel.app
```

不要给 API 配置 `ACCOUNTING_WORKER_DATABASE_URL`。每个后台只注入对应账号的 `ACCOUNTING_WORKER_DATABASE_URL`，不注入 `ACCOUNTING_DATABASE_URL`：

| 后台角色 | 额外环境变量 |
| --- | --- |
| `worker` | `ACCOUNTING_QUEUE_CONCURRENCY=2`、`ACCOUNTING_QUEUE_POLL_SECONDS=1`、`ACCOUNTING_QUEUE_LEASE_SECONDS=180` |
| `assistant` | 不需要额外并发参数 |
| `migration` | `ACCOUNTING_MIGRATION_CONCURRENCY=1`；不需要模型密钥 |

在 API、OCR/分录后台和助手后台配置相同的模型场景与报价。可以一个兼容供应商共用 `ACCOUNTING_AI_BASE_URL/API_KEY`，另填视觉、分录、对话 model ID；也可以将两家供应商按场景分配：

```dotenv
ACCOUNTING_AI_OCR_BASE_URL=https://PROVIDER_A/v1
ACCOUNTING_AI_OCR_API_KEY=…
ACCOUNTING_AI_OCR_MODEL=明确的视觉模型 ID
ACCOUNTING_AI_JOURNAL_BASE_URL=https://PROVIDER_B/v1
ACCOUNTING_AI_JOURNAL_API_KEY=…
ACCOUNTING_AI_JOURNAL_MODEL=明确的结构化模型 ID
ACCOUNTING_AI_CHAT_BASE_URL=https://PROVIDER_B/v1
ACCOUNTING_AI_CHAT_API_KEY=…
ACCOUNTING_AI_CHAT_MODEL=明确的对话模型 ID
ACCOUNTING_AI_RESPONSE_FORMAT=json_schema
ACCOUNTING_AI_TIMEOUT_SECONDS=45
ACCOUNTING_AI_MAX_CONCURRENCY=2
ACCOUNTING_AI_ALLOW_HTTP=0
```

场景覆盖地址/密钥必须成对填写；这用于独立场景分配，没有故障自动切换备用供应商。接口地址通常以 `/v1` 结束，不填写 `/chat/completions`。填入实际供应商的模型 ID、结构化输出格式与报价；未知报价保持空白，不将未知费用当零元。云端逐服务范本为 [API](../deploy/.env.api.example)、[OCR/分录后台](../deploy/.env.worker.example)、[助手后台](../deploy/.env.assistant.example)、[旧账后台](../deploy/.env.migration.example)。[deploy/.env.demo.example](../deploy/.env.demo.example) 的四个 DSN 聚合变量仅用于本地 Compose，不能整份注入任一 Zeabur 服务。

建议先部署 API，启动自检后再启动三后台。启动后台前确认数据库迁移完成，避免尚未具备权限的进程反复重启。Zeabur 默认 TCP 健康检查可检测四服务；三个后台也可设置 HTTP 健康检查路径 `/healthz`。API 默认 TCP 检查不受 Host 白名单影响。探针只确认当前进程初始化完成，供应商可用性、任务积压与后续数据库故障继续由工作台心跳及告警判断。参考 [官方健康检查说明](https://zeabur.com/docs/en-US/operations/monitoring/health-checks)。Zeabur 中只有 API 要绑定 HTTPS 域名；入口及域名配置参考 [官方公网网络说明](https://zeabur.com/docs/en-US/deploy/networking/public-networking)。

本次实际 Git 部署发现平台运行设置会覆盖 Dockerfile 的 `USER`。四个服务的部署规格已显式设置 `source.runAsUserID=10001`，仓库根目录、环境变量、域名及端口保持原配置。新建服务和 Git 自动重建后，应在容器终端核对 PID1，而不是只检查镜像声明或当前终端用户：

```sh
python -c 'print(next(line for line in open("/proc/1/status") if line.startswith("Uid:")))'
```

应显示 UID 10001。规格调整使用已核验的 Zeabur 部署接口并保留现有环境；不要把本机私密运维脚本、令牌或环境文件提交到仓库。

## 3. 配置 Vercel

Vercel 从仓库根目录读取 `vercel.json`；沿用 `app` 与 `api_gateway` 两服务，网关 `/api/*` 先匹配，前端兜底。仅在网关服务端设置：

```dotenv
ACCOUNTING_BACKEND_ORIGIN=https://YOUR_API_DOMAIN.zeabur.app
ACCOUNTING_GATEWAY_TIMEOUT_SECONDS=110
ACCOUNTING_GATEWAY_ALLOW_LOCAL_HTTP=0
```

不设置任何 `VITE_` 模型/数据库/Storage secret。网关保留 `/api` 路径，后端仍提供 `/api/health`，不再增加额外 path prefix。调用链是浏览器 → 公开网关 → Zeabur API，未新增 Vercel 内部 service binding。

在 Vercel 导入同一 GitHub Demo 分支，Root Directory 保持仓库根目录，按配置构建两服务。保存以上环境变量后重新部署。取得最终 HTTPS 前端域名后，更新 API 的 `ACCOUNTING_ALLOWED_ORIGINS` 与 Supabase Auth Site URL；访问 `https://你的前端域名/api/health`，应得到 `status=ok`、`storage=postgres`、`auth=supabase`，再进入登录页面。如果显示网页 HTML 而非 JSON，先检查多服务路由是否按根 `vercel.json` 生效。

当前网关单文件上限 `3 MiB`、请求体 `4 MiB`；Demo 大扫描件先压缩或拆分。后台队列在 Zeabur 运行，不占用 Vercel 请求生命周期。Vercel Preview 每次域名可能不同，真实登录及来源白名单须添加明确来源后才能用，不启用 `*` 通配。

## 4. 只读自检和首次验收

在各 Zeabur 容器终端执行：

```sh
python scripts/check_demo_deployment.py
python scripts/check_demo_deployment.py --online
python scripts/check_demo_deployment.py --online --require-ai --json
```

默认只校验环境，不加载旧 `.env`。`--env-file` 只加载明确指定的文件；`--online` 仅查询受限数据库目录/迁移摘要、Auth settings 和 bucket metadata，不迁移、不注册用户、不上传或下载原件、不调用模型。所有输出只含检查名与固定提示，隐藏 DSN、密钥、供应商地址和原始 HTTP/驱动异常。静态配置错误时跳过网络请求；报价和业务初始化为独立提示，自检通过不表示会计处理已验收。

首次验收按下列顺序：

1. 从 Vercel 打开登录，用真实 Supabase 账号登录；创建事务所、2–3 家客户、账套/期间及科目模板，给员工和会计分配权限。
2. 企业管理员开启票据与模型处理；按实际报价配置企业月度预算及 OCR/分录/对话单次预留。先用少量标注为 Demo 的脱敏票据。
3. 员工录入业务并上传不超过 `3 MiB` 的票据，按原审批移交；OCR 与分录任务应进入持久后台队列，原件经 API 私有读取。
4. 确认工作台展示真实后台进度、识别字段、异常和草稿。手动制造金额差异，确认异常要求人员处理，凭证仍须会计复核后导出。
5. 打开会计助手，选择明确企业和资料范围；供应商真实调用需这一步明确发起。核对用量、预算和任务结果。
6. 重启一个后台再观察任务恢复；已发送且结果未知的模型请求保持人工确认，不盲目重复扣费。用另企业员工账号确认无法查看原件、会话或凭证。

如只想先预览 UI，可暂不配置模型，现有人工录入/审核仍能运行；`--require-ai` 会明确报自动化未配置。

## 本地容器重现与后续升级

复制环境范本至 Git 忽略的 `.env.demo.local`，填写新项目受限 DSN，再运行：

```sh
docker compose --env-file .env.demo.local -f docker-compose.demo.yml config --quiet
docker compose --env-file .env.demo.local -f docker-compose.demo.yml up --build
docker compose --env-file .env.demo.local -f docker-compose.demo.yml exec api python scripts/check_demo_deployment.py --online
```

Compose 不部署本地 PostgreSQL、Vercel 或 Supabase，它只重现四个后端进程。API 绑定本机 `127.0.0.1:18080`；配合 `vercel dev` 时显式启用网关的本地 HTTP 标志。不要把 Compose 默认 `config` 的完整输出发送给他人，它可能包含密钥。Zeabur 当前不直接部署 Compose；云端使用上述 Git/Dockerfile 四服务操作。

升级前备份数据库与私有原件，远端原件的备份使用 `python scripts/backup_accounting.py backup --configured-storage --destination …`（数据库备份仍需独立管理员维护环境，准确参数见 `--help` 和 [备份说明](BACKUP_RECOVERY.md)），并执行实际恢复演练；停写后显式迁移，再滚动启动 API/后台。API 和三个 worker 都要部署同一代码版本，并使用相同 bucket 和场景配置。此文提供可操作底座；本仓库测试不会替你创建云资源、配置域名或提交真实账务。

## 0.16.0 升级与连接池

新版本运行前须显式执行 PostgreSQL 073–076；API 与三个 worker 使用同一版本。每进程连接池默认 MAX=4，多个服务及副本的 MAX 合计应符合数据库容量。企业资料直接提交通道、数电票 XML、固定样本比较及性能监控见 [第一批改进与升级](STAGE16_MVP_FOUNDATION.md)。
