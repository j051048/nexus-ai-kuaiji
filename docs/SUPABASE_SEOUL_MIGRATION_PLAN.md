# Supabase 首尔迁移方案（历史评估，已被实施报告取代）

> 当前状态请见 [第二十一阶段：Supabase 首尔迁移与线上验收](STAGE21_SEOUL_MIGRATION.md)。2026-10-15 已切换至用户创建的首尔项目。本文件保留当时的只读评估、报价与停机估算；下文的“待批准”“先演练”“保留 7 天”均为历史提案，不代表当前待办或仍有效的操作要求。用户后续已取消额外演练项目并授权直接迁移，模拟数据旧库无需保留；旧库删除状态以实施报告最终记录为准。

原评估核查日期：2026-10-15。形成本方案时仅进行了管理 API、SQL 聚合和官方文档的只读核查，尚未创建资源、修改生产配置、停服务或迁移数据。以下内容按原评估保留。

## 建议

保留现有韩国 Zeabur；在同一个 Pro 组织中新建明确指定首尔 `ap-northeast-2` 的 Supabase Micro 项目（拟名 `nexus-ai-kuaiji-seoul`），将 PostgreSQL、Auth 和 Storage 一并迁移。旧加拿大项目保留至少 7 天用于回退，退役另行确认。

不要选择仅写“APAC”的通用地域：它目前对应新加坡。Supabase 现有项目不能原地改区域；Dashboard 的 “Restore to a new project” 也会保持源地域，因此不能用它直接完成加拿大至首尔迁移。[区域](https://supabase.com/docs/guides/platform/regions)、[改区域](https://supabase.com/docs/guides/troubleshooting/change-project-region-eWJo5Z)、[克隆限制](https://supabase.com/docs/guides/platform/clone-project)

## 已核实的现状

| 项目 | 只读结果 |
| --- | --- |
| 源 Supabase | `vipiyuwbywbyzvmfpzbo`，`ca-central-1`，PostgreSQL 17.11，健康 |
| 账务数据库 | 总大小 26,646,195 bytes，约 26.6 MB；accounting 约 8.16 MB、166 张表 |
| 权限与迁移 | 001–082 共 82 个迁移；全部 accounting 表已开启并强制 RLS |
| 登录 | 5 个用户、5 个 identity、2 个已验证 TOTP MFA factor |
| 原件 | 私有 bucket `accounting-originals`，9 个对象、421,381 bytes |
| 后台任务 | 核查时 OCR、分录建议、助手、旧账迁移均无排队/运行任务；正式切换仍须重新核查 |
| 额外数据库依赖 | Vault secret 为 0；未发现 auth/storage 中调用自定义 schema 函数的触发器，未安装 pg_cron / pg_net / wrappers / pgsodium；正式迁移仍须复查 |
| Supabase 套餐 | 已是 Pro，本项目为 Micro、1 GB RAM、共享 CPU，$0.01344/小时 |
| 组织管理 | Vercel Marketplace 组织，另有一个活跃项目；不将其未核实费用计入本方案 |
| Zeabur | 实际管理 API 确认为 KR / Seoul，2 核、7686 MiB RAM、约 80 GB 磁盘，服务器价格 $6/月 |

所有容量为核查时快照，不包含原件在业务上的真实性认定。

## 两条路径与费用

| 路径 | 运行范围 | 费用与影响 |
| --- | --- | --- |
| 推荐：迁 Supabase 到首尔 | 迁数据库、Auth、原件；Zeabur 五个服务留在原服务器 | 新旧库同规格替换后计算月费不增加；重叠期间多一台 Micro |
| 备选：迁 Zeabur 到加拿大 | 重建 API、三个 worker、PaddleOCR 及其模型缓存，切域名/内部地址 | 本次实时查到 AWS Lightsail 加拿大 2 核 8 GB / 160 GB 为 $48/月；DigitalOcean Toronto 4 核 8 GB / 160 GB 为 $52/月；均显示 available。不是全平台最低价承诺，且购买前仍须复核结算报价 |

迁后端的上述服务器报价分别比当前服务器多 $42 / $46 每月，另有新旧服务器并存和已付租期成本。Zeabur 服务器按月固定计费，不能把重叠数天直接按日摊销。备选路径可先平行部署，但仍需排空任务、切内部 OCR 地址和域名，再验证会计流程；建议至少预留与主方案相同的 30–60 分钟维护窗口，实际以演练为准。[Zeabur 服务器购买与计费](https://zeabur.com/docs/en-US/server/purchase)

推荐路径的预算（美元、未含税，使用现有 Pro 组织）：

| 项目 | 预算 |
| --- | ---: |
| 新首尔 Micro | $0.01344/小时，约 $10/月 |
| 新旧库重叠 24 小时 | 额外约 $0.32 |
| 新旧库重叠 72 小时 | 额外约 $0.97 |
| 新旧库重叠 7 天 | 额外约 $2.26 |
| 旧库退役、仅以首尔替换原 Micro 后 | 相对当前计算月费增量 $0 |
| Zeabur 当前服务器 | 继续 $6/月 |

无需再买一份 $25 Pro 订阅。现有 Pro 的 $10 compute credit 属于组织总额，不能给新增项目再抵一份；因此本方案按新增 Micro 的完整小时费估算。表格不是组织总账单，组织内其他项目、既有订阅及 Vercel/模型费用另计。预演也占用新项目小时数，若预演加回退保留共约 7–8 天，可预留 $3 左右额外计算费；保留更久则继续累计。[Compute 计费](https://supabase.com/docs/guides/platform/manage-your-usage/compute)

本数据规模的传输量很小，预计在 Pro 的既有出口/存储额度内；是否免费取决于组织其他项目的已用额度。Pro 普通出口额度 250 GB/月，超额 $0.09/GB；存储额度 100 GB，超额 $0.0213/GB/月。不购买 PITR、IPv4、额外备份或其他付费附加项。[官方价格](https://supabase.com/pricing)

现组织通过 Vercel Marketplace 统一结算，官方价格与直购相同；按当前官方限制应从 Vercel Dashboard 创建该组织下的新项目。目标资源先不关联生产部署，防止自动环境变量同步提前切换。不要另外新建一个 Pro 组织，也不要卸载已有集成。[Marketplace 说明](https://supabase.com/docs/guides/integrations/vercel-marketplace)

## 执行与停机安排

### 1. 获批后准备、演练（原系统继续可用）

- 创建首尔 Micro，记录实际项目 ref、区域、连接方式和扩展，目标维持 PostgreSQL 17 主版本；如目标不支持 17、首尔缺货或报价不符，先停在准备阶段，不在本次迁移顺带升级主版本。
- 用官方 Supabase CLI 逻辑备份/恢复路径迁角色、schema、数据，保留 Auth UUID、密码哈希以及会计用户关联；新目标不开 worker、不连真实财务软件、不发邮件。
- 迁移 001–082 的历史及摘要原样保留，核对角色成员关系、函数所有者、固定 search_path、SECURITY DEFINER、GRANT 和全部 FORCE RLS。四个运行账号重新设置独立密码，继续使用受限账号与 SSL。
- 单独复制全部 9 个原件对象；保持 bucket 私有、原 storage_key、逐件大小和 SHA-256。继续经后端授权读取原件，不创建公共或签名直链。
- 复查扩展、cron、数据库 webhook 和外部作业，审阅逻辑恢复内容，防止目标导入后提前发送请求。当前 Vault secret 为 0；若窗口前发现新增 Vault/列加密依赖，须按官方流程另行处理加密根密钥，并验证解密，不能仅复制密文。
- 对照迁移 Auth 配置、回调 URL、MFA 设置、SMTP/模板/限流等。2 个已验证 TOTP 因素及加密依赖是切换门槛，须实际完成管理员 aal2 验证；若无法验证，先解决或回退，不能关闭管理员 MFA。
- 使用相同虚构用户验证普通登录、企业隔离、撤权、原件、凭证、复核后导出及后台任务。先从韩国服务器测新数据库往返，建议演练门槛 p95 ≤20 ms，再重复首页和助手脚本；这是验收目标，不是已测得结果或秒开承诺。

官方允许迁移 Auth 表及密码哈希，用户通常不用改密码；新项目 URL、issuer 和签名配置变化，本产品按一次重新登录安排。[Auth 迁移](https://supabase.com/docs/guides/troubleshooting/migrating-auth-users-between-projects)、[逻辑备份恢复](https://supabase.com/docs/guides/platform/migrating-within-supabase/backup-restore)

现有 `scripts/backup_accounting.py` 只备份 accounting 和业务引用原件，不含完整 Auth、全局角色及托管设置；其 restore 面向空 PostgreSQL 且忽略 privileges，不能直接恢复进已有内置 auth/storage 的新 Supabase。可复用其原件摘要、账务快照核验，以及 `migrate_accounting.py status`、`check_demo_deployment.py --online`；需要另做跨项目编排。

### 2. 正式维护窗口（建议预留 30–60 分钟）

以下为工程估算，预演后再缩小范围，不能按 26 MB 传输时间推算整个停机时间。

| 步骤 | 预计时间 |
| --- | ---: |
| 显示维护页，封写并排空在途任务 | 5–15 分钟 |
| 最终一致备份、恢复及原件增量核验 | 5–10 分钟 |
| 四个服务切换目标连接与密钥、重新启动 | 5–10 分钟 |
| 登录/MFA/企业权限/原件/复核导出验收、恢复服务 | 10–20 分钟 |

当前仓库没有全站封写开关。正式实施前须准备并演练覆盖业务 API 和旧 Auth 用户/identity/MFA 变更的冻结手段，不能仅隐藏前端或只禁注册。迁移期间拒绝登录/密码及 MFA 更改；短期会话和刷新 token 不作为继续会话的保证。若旧 Auth 关键数据无法可靠冻结，不执行最终切换。

先停止接收新任务并等待 OCR、分录、助手及迁移任务结束，再停三个 worker 和旧 API 写入口。`worker.stop()` 只 join 约 5 秒，收到终止信号不能当作供应商请求已结束；未知发送结果继续保留 interrupted/uncertain、预算预留和人工处理，不自动重新付费调用。

### 3. 切换配置与验收

- API 更新 `ACCOUNTING_DATABASE_URL`、`ACCOUNTING_SUPABASE_URL`、`ACCOUNTING_SUPABASE_PUBLISHABLE_KEY`、`ACCOUNTING_SUPABASE_ADMIN_KEY`。
- 三个 worker 更新各自 `ACCOUNTING_WORKER_DATABASE_URL`；四个服务统一更新 `ACCOUNTING_STORAGE_SUPABASE_URL`、`ACCOUNTING_STORAGE_SERVER_KEY`，bucket 名不变。新项目必须使用新密钥，不复用旧项目 service key。
- AI 配置、公共域名 `kuaiji.flydao.top`、Zeabur API 域名及 PaddleOCR 内网地址保持当前值。PaddleOCR 本身不连接 Supabase，无需搬迁。
- API 先启动验收，随后启动 worker，确认心跳、幂等与队列。所有用户刷新并重新登录一次。
- 当前前端从 `/api/auth/config` 运行时获取登录配置，网关仍访问相同 Zeabur 域名，通常不需要重建前端；仍需核对 Marketplace 自动变量关联并清除旧登录会话。
- 保持未复核凭证不能导出、旧导出及回执可追溯；复测首页和助手后再正式恢复写入。

### 4. 回退与保留

正式恢复新环境写入前失败，可切回旧连接并恢复旧服务，预估 10–20 分钟。新环境已产生写入后，必须先停写、核对并反向同步，不能直接切回旧库丢弃新增记录。旧项目保留原始数据与冻结状态，不允许两边同时运行写入/worker。

建议保留旧项目至少 7 天，之后另行确认退役或延长保留；未退役期间继续按小时付费。目标若 MFA、RLS、原件摘要或关键会计流程不通过，保持维护状态或回退，不降低验收条件。

## 待确认

是否按“同一 Pro 组织新建首尔 Micro，迁 PostgreSQL + Auth + Storage，保留韩国 Zeabur；预演及保留共约 7–8 天、额外计算费约 $3，正式预留 30–60 分钟维护窗，旧项目不自动删除”推进？

用户批准资源与迁移方案后先准备演练，正式切换前再确定具体维护时段。当前文档属于方案，没有实施迁移。
