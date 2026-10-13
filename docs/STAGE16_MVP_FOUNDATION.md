# 第一批 MVP 改进 · 0.16.0

本批围绕现有导入、资料提交、草稿、独立会计复核与导出流程实施。真实客户与模型质量仍以 2–3 家企业的完整月份试点验收；本地及专用 PostgreSQL 测试使用虚构资料。

## 使用入口

| 改进 | 使用方式 |
| --- | --- |
| 工作区 URL | 页面进入 `/workspace/<工作区>`，账套、凭证和待办等位置保存在 URL。复制链接、刷新、浏览器后退均重新取得当前授权。未保存凭证编辑先保存或明确放弃。 |
| 连续复核 | 桌面以紧凑借贷表格核对科目、辅助项、金额与原件；同屏复核支持键盘操作和下一笔。手机保持可读布局。确认勾选刷新后需要重新核对。 |
| 简单企业资料提交 | “部门与企业设置 → 资料提交方式”选择“老板／出纳提交 → 授权会计接收”，填写变更依据。企业 owner/admin/finance 可无部门上传；对应月份的非申请人授权会计在待办接收、生成草稿，然后复核导出。 |
| 分级审批 | 默认保持员工、部门、企业财务流程。提交方式冻结在新建事项上，之后的配置变更不改变既有事项。新建资料始终重新检查当前成员与角色。 |
| 数电票 XML | 一体化录入及业务附件支持数电票 XML；开启企业处理后可免费本地解析、人工采用字段、批量排队并生成模板草稿。具体格式和安全限制见 [结构化票据](STRUCTURED_INVOICES.md)。 |
| 固定评测样本 | 在真实业务评测中选独立确认标准、填写名称及依据并冻结版本。按固定样本集回放，再选择基线与候选检查范围、覆盖、退步和费用证据。详见 [评测基准](QUALITY_BENCHMARKS.md)。 |
| 性能观测 | 费用与监控页展示最近 15 分钟的企业事务、跨进程样本、OCR／草稿排队及处理 P95、有限样本吞吐和当前进程连接池容量。 |

XML 原文解析提供字段和路径证据，尚未验签或验真。当前明确支持财政部指南中的 EInvoice 0.3 蓝字格式；其他 XML、OFD／XBRL、PDF 内嵌结构化数据需要分别按真实样本适配。图片和普通 PDF 继续使用视觉模型。原件保留、金额交叉核对、重复票据检查和人工异常处理继续生效。

模型评测比较已归档结果，不在评测页面调用未使用的新模型。新模型缺少实际结果时标记未覆盖。未来真正的离线重新调用应有独立持久任务、外发授权、费用预留与结果未知恢复；不得复用业务任务覆盖已审核证据。本批比较通过只表示固定样本已测范围满足所选条件，不自动发布模型、规则或审批凭证。

## 升级

PostgreSQL 新增 073–076，SQLite 新增 057–060。旧迁移保持历史摘要。升级前备份数据库及私有原件，在维护窗口停写，用管理员连接显式执行：

```powershell
python scripts/migrate_accounting.py migrate
python scripts/migrate_accounting.py status
```

之后运行同一版本的 API、OCR／草稿、助手和旧账 worker。管理员 DSN 仅供迁移终端使用；运行服务继续使用独立受限账号。云端迁移、部署与真实供应商验证不能由本机测试替代。

每个服务可设置以下连接池参数。API 和各 worker 各有独立池；所有副本的最大客户端连接数为各服务 `MAX` 的总和。

```dotenv
ACCOUNTING_DB_POOL_MIN=0
ACCOUNTING_DB_POOL_MAX=4
ACCOUNTING_DB_POOL_TIMEOUT_SECONDS=5
ACCOUNTING_DB_POOL_MAX_WAITING=32
```

连接池保留 `prepare_threshold=None`，适配 Supabase 事务连接池；身份、企业和角色使用事务内 `SET LOCAL`，归还前回滚。程序退出及 API 初始化失败释放连接与监测线程。全局写锁及现有总并发上限本批保持，后续依据压测调整。

Vercel `app` 已加 `/workspace/:path* → /index.html` 服务内回退，支持工作区链接刷新。`/api` 仍优先进入网关。服务内路由按 [Vercel 官方 Services 路由说明](https://vercel.com/docs/services/routing) 配置，并通过官方 JSON Schema 校验。其他静态服务器需提供同样的 SPA 回退，并保留 API 与静态资源路径。

企业处理开关与补件关闭触发的恢复信号，在 PostgreSQL 下统一采用数据库 UTC 时间，避免 API 主机时钟略领先导致刚触发的任务暂时漏扫。

## 开发检查

```powershell
python -m pip install -r nexus_backend/requirements-dev.txt
npm ci
npm run format
npm run format:check
npm run lint
python -m ruff format nexus_backend/app services/api_gateway scripts
python -m ruff format --check nexus_backend/app services/api_gateway scripts
python -m ruff check nexus_backend/app services/api_gateway scripts
npm test
npm run build
python -m pytest nexus_backend/tests -q
npm run test:e2e
```

CI 执行相同格式、可执行代码检查、单测、构建、PostgreSQL 与浏览器验证。性能统计只包含有授权企业归属的事务；全局调度不分摊到企业。后台约 5 秒刷新、每次最多 16 组。任务耗时每种最多观察 500 个近期尝试，助手与旧账尚无独立领取时间，不混入耗时计算。
