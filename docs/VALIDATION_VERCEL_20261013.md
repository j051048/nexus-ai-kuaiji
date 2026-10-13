# Vercel 配置验证 · 2026-10-13

部署拓扑已由用户选择：Vercel 仅运行静态前端和 API 网关，会计后端、数据库、私有附件及常驻 worker 在持久服务器。以下先记录初始本地验证，随后记录真实云端配置和联调；本地结果不替代云端验收。

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

大于 4 MiB 的原件／导出不能通过网关下载；前端 3 MiB 文件限制只在网关 metadata 存在时启用，原后端数据与直连上限保持。详见[部署说明](VERCEL_DEPLOYMENT.md)。

## 真实云端联调

已用用户授权的云资源部署：Vercel 正式域名 `https://kuaiji.flydao.top`，Zeabur API `https://kuaiji.zeabur.app`，Supabase 项目 `vipiyuwbywbyzvmfpzbo`。Vercel 生产分支为 `main`，实际云构建包含 Vite 和独立 Python 3.12 FastAPI 网关；先验证构建再提升为正式部署。

| 验证 | 实际结果 |
| --- | --- |
| 经正式前端域名请求 API | `/api/health` 200，`postgres` / `supabase`；`/api/auth/config` 200；未登录 `/api/me` 401 |
| PostgreSQL 初始化 | 001–072 全部执行；逐项版本／摘要匹配；151 张会计表启用 FORCE RLS |
| 运行账号 | API、OCR、助手、旧账后台各用独立 NOINHERIT / NOBYPASSRLS 账号与显式 TLS |
| 原件存储 | 私有 `accounting-originals` bucket；两企业合成 PNG 经网关上传／下载，SHA-256 一致 |
| 真实登录 | 4 个明确标记的联调用户通过 Supabase 密码登录和网关身份注册；未发送邮件 |
| 企业隔离 | 企业 A 员工不能读取 B 的事项、原件或账套，不能上传／创建 B 的事项；本人列表无跨企业事项 |
| 业务审批 | 申请人自审批和提前财务审批被拒绝；不同用户完成部门审批、财务审批及移交；重试移交返回同一来源／草稿 |
| 银行导入 | 首次导入 1 条，再导入 0 条、重复 1 条；仅生成 1 张对应草稿 |
| 会计审核与导出 | 未审核导出 409；独立会计审核通过后 CSV 导出成功；相同请求编号返回同一导出 ID |
| 员工事项闭环 | 报销附件、部门审批、财务审批、移交草稿、会计审核与 CSV 导出全部通过；23.45 元与银行草稿 12.34 元分别借贷平衡 |
| 待办与处理状态 | 管理员看到 2 个账套、仅授权 A 的会计看到 1 个；未启用 AI 的任务为 `needs_attention / processing_disabled` |
| 持久迁移任务 | 单行虚构科目 CSV 的解析／校验均由独立迁移 worker 实际领取并成功完成；缺资料进入 `needs_attention`，未验收／接管，当前凭证不变 |
| 四服务运行 | API、OCR／分录、助手、旧账迁移四进程已启动；逐容器核验 PID1 UID 10001、就绪探针及线上自检；三个后台保持私有 |
| Vercel 本机接入 | CLI 已登录；官方插件和共享 MCP 已安装，原生 OAuth 下实际文档搜索／团队列表调用成功 |

测试只使用标记为“联调虚构”的事务所、两家企业、业务资料及附件，没有导入真实客户账务。账号密码、云凭据、JWT、私密环境及详细回执仅保存于忽略的本机文件，不提交 Git 或上传 Vercel。已检查 CLI 实际上传清单：私密 MCP 配置、环境、日志和数据库产物不在其中。

上述流程共完成 36 项云端断言，全部通过。Supabase Auth Site URL 和明确允许的跳转地址已更新到正式前端域名。

本轮尚未配置模型供应商、模型 ID 与报价，也未启用企业 AI 处理，不进行付费模型调用。已审批业务生成的是确定性模板草稿；这不构成 OCR、模型记账或外部财务软件入账的验收。云端浏览器自动化连接超时，以上为实际 HTTP 流程验收；完整云端浏览器交互仍需补验。
