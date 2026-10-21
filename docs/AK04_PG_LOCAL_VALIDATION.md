# AK04 本机 PostgreSQL 执行记录与人工接手验证

日期：2026-10-21。仅使用本机一次性 PostgreSQL 14.24、真实受限 runtime/worker 登录及合成 HTTP；未连接真实模型或生产数据库，未改 C01/138、未提交、推送或部署。

最终冻结候选合跑 **41 passed，0 failed，0 skipped**（42.92 秒）。其中 AK04 新增 13 项，AK01–AK03 相关既有对话、费用、上下文和事实边界回归 27 项，既有结构化 XML PG 缓存/RLS 1 项。前面的重复运行不计入不同用例总数。

## 隔离与候选

- HEAD：`baf3f414187a198dbde1c41f8a90e42dee2629ca`。从 HEAD 导出新候选到 `tmp_test/ak04-codex-20261021/pg/candidate`，只覆盖 32 个明确列出的 AK01–AK04 Python 文件。
- 沿用 AK03 的 21 文件清单，新增 assistant service/router、intelligence router、intake router、cases service、execution 目录的四个文件、`test_ak04_postgres.py` 及最终隐私契约调整后的 `test_stage16_structured_invoice.py`。完整路径与 SHA-256 见 `final-overlay-hashes.json`。
- database、main、maintenance worker、workspace projection/service、旧 intelligence workflow/PG integration 夹具、migrate 共 8 个保护文件与 HEAD 字节一致。未复制工作区并行 C01 修改。
- 使用 HEAD 的 134 个迁移，最大版本 135；未纳入未跟踪的 `138_projection_identity.sql`，未新增或修改迁移。数据库每一项迁移校验和均与候选规范化源码一致。
- 新集群位于 WSL `/tmp/nexus-ak04-c44ef7357e7a`，只监听 `127.0.0.1:15438`，独立库为 `ak01_local_ak04_c44ef7357e7a`。库名保留旧隔离测试夹具要求的 `ak01_local_` 前缀。
- 独立登录 `ak04_runtime_c44ef7357e7a`、`ak04_worker_c44ef7357e7a` 均为 NOSUPERUSER、NOBYPASSRLS、NOINHERIT、NOCREATEDB、NOCREATEROLE。新专项实际检查 session_user 与 current_user，业务和 worker 分别切换到 `accounting_app` / `accounting_worker`。
- 管理员仅用于本机迁移、登录创建、合成身份夹具和权限故障注入/核验；业务服务、队列执行、case 创建/关闭、原件异常处理、会计复核及导出均通过受限登录执行。
- runner 清理继承的数据库、模型及代理环境，psycopg 只接受明确的 loopback 端口和临时库，Python socket 拒绝外网与原 5432。此为测试进程防护，不宣称操作系统网络沙箱。

## AK04 新增 13 项

1. 实际 runtime / worker 登录及受限角色属性；没有以管理员代替业务执行。
2. 合法当前事实、错误金额 claim、无绑定自由回答三种真实 worker 路径：分别持久 `ak04_fact_verified` / `ak04_fact_invalid` / `ak04_fact_accountant`，公开 succeeded 的原 error_code 仍为空，reply 保持原三字段。
3. 专用助手 execution HTTP 与 turn 的投影一致；匿名 401、他人会话 404；普通会计无 diagnostics，无费用 token、供应商地址、口令或伪造金额。
4. 当前账套授权撤销后立即隐藏 execution；真实 worker 不发送、不收费。
5. OCR execution 保留原件未核验 finding；普通员工无 diagnostics；当前事务所 owner 可看实际估算费用与 token，未持久的 context_pack_hash 为 null；他人和他企被拒。
6. 真实 chat execution preview / handoff HTTP：受限安全列读取、can_create 当前权限、case 创建串行重放幂等、同一来源只产生一个内部异常任务、其他员工不能接手，也不触发模型重放。
7. 真实 OCR 人工接手及串行重放幂等：会计 can_create=true，员工 false；他企和申请人不能发布内部 case。关闭接手任务不能替代原件核验，申请人不能确认原件；会计明确核验后建议仍是草稿，独立会计 approve 后才允许 export。
8. 草稿版本在 preview 后变化时，接手返回 409 且零 case。
9. 草稿版本在接手预检后、CaseService 公司写锁之前变化时，真实 source_guard 在写事务内返回 409，零 case、零额外模型调用。
10. 两线程 Barrier 同时提交同一 handoff key；在两个生产 CaseService 公司写事务内记录的真实 pg_backend_pid 不同。两次结果为同一 case ID，case_count=1，模型请求次数与费用行均不变；SQL、连接与公司写锁没有被 mock。
11. 结构化 XML 员工私有 intake 无模型调用、无费用，转入业务事项后保留原件来源；公开 API 不暴露 provider 或 diagnostics，数据库仍保存真实 `local-structured-parser` provenance，他人不能读取私有 intake execution。
12. XML 税额与净额/总额不符时仍有 blocking `document_1_tax_sum`；两个独立处理均不命中缓存、零模型调用、零费用。

第 2 项为三个参数，第 3 项是同组测试中的 HTTP 断言，不另计数；以上共 13 个不同 test ID。

## 既有回归 27 项

- `test_ak03_postgres.py`：7 项，事实发布、三字段 JSON、非法引用、纯视觉独立原件核验、复核后导出及无依据拆分拒绝。
- `test_ak02_postgres.py`：8 项，OCR/journal/intake/chat 当前授权 ContextPack、幂等、费用、他企拒绝及发送后外发政策撤销。
- `test_stage15_assistant_meter_postgres.py`：5 项，真实 worker 费用、共享预算、伪造 token 拒绝、未知费用保留且不重发、撤权后不发布。
- `test_stage15_conversations_postgres.py`：7 项，真实队列/RLS、会话私有、不可伪造领取/发送/完成、租约及引用版本绑定。

另运行既有 `test_stage16_structured_invoice_postgres.py::test_pg_local_xml_model_free_scoped_original_and_cache`：结构化解析、原件跨企业 RLS、模型零调用/零费用、同企业缓存复用。其旧 intake 测试仍断言公开 provider，与新隐私契约不符，使用上面的新 intake 专项覆盖真实流程。

## 运行与证据

首轮新专项为 `7 passed, 1 failed`；唯一失败是新测试直接解包 CompatibleRow 得到列名，改为显式索引后 8 项通过。不是产品权限缺陷，也不将其当作业务红灯证据。补齐 can_create 与草稿变更两项后，10 项及当时的全部 37 项通过。

后续联合回归发现真实的 XML 相邻兼容缺陷：金额标签不识别可信结构化解析器的固定 FIELD_PATHS，导致 XML 缓存/模板链路中断。root 仅为 gross/net/tax 增加三个完整固定 XML 路径，保留裸金额、金额裁剪及字段串列守卫。最终 checks.py SHA-256 为 `74e233a29b10398805290193e75af85689e13bf3ad91a91d80e572f2716cdcb2`。

为此安全重启同一临时集群，经该集群 Unix socket peer 仅轮换 postgres 和两个测试登录的随机口令，同步最终 32 文件。新增 XML/并发组合首轮 `40 passed, 1 failed`，失败是新测试误当直接 recognize 响应总有 provider/execution 键；按既有键省略及独立 execution 读取契约修正测试后，最终同期 41 项全绿。两次测试断言修正均没有改业务源码。

| 文件 | cases | failures | errors | skipped | SHA-256 |
|---|---:|---:|---:|---:|---|
| `pg-ak04-first.xml` | 8 | 1 | 0 | 0 | `77f06e54b274e40cb7b06752c945c10d5a4e8d99d0a151f4a4abcda20d6364dc` |
| `pg-ak04-green.xml` | 8 | 0 | 0 | 0 | `16baf08bd90d987c2cc6b2a52d744502c2702175ebad1454cdac15b0b6d4a525` |
| `pg-ak04-final.xml` | 10 | 0 | 0 | 0 | `49554dcfaa35f21261ab5f69a15cbd456c96607236241826980f64326b6b2834` |
| `pg-regression.xml` | 27 | 0 | 0 | 0 | `b0244b4a9bcaf35063aecf8999f74b375618af9650a705b5d06c9e5f49a662ea` |
| `pg-final-all.xml` | 37 | 0 | 0 | 0 | `5af33efde176708f8fe84371663e0812e3b0565f2b2b89f5d34e3e2db65d09b6` |
| `pg-xml-first.xml` | 41 | 1 | 0 | 0 | `c59d0c7b0ab250ee94352856641ccaa306448a35445609374196b683ae9e3f7a` |
| `pg-final-with-xml.xml` | 41 | 0 | 0 | 0 | `54e3d5ae57c526acae69f46c311d2322e2221153fa1b69f7e3162e2adfcd4d45` |

所有运维脚本、日志、JUnit、候选 manifest、32 文件最终哈希、角色/迁移核验均位于 `tmp_test/ak04-codex-20261021/pg/`。`final-overlay-hashes.json` 的 SHA-256 为 `daa491a5550b7d7902235c6c1c311f6b5720e831571f84d47e9229115024602d`。`before-xml-*` 文件保留重启前的 31 文件核验；最终状态以不带该前缀的文件为准。

## 结束状态与范围

- 全部 32 个候选与工作区最终白名单 hash 一致；测试后临时库其他连接数 0。保护文件仍与 HEAD 一致，迁移校验和不变。
- 15438 已停止，pg_isready 返回 no response，Windows 无该端口 listener；初始化密码输入与 Windows 私有明文状态已删除。核验在删除前确认生成口令没有出现在其他源码、JSON、日志或 JUnit 中。
- 原 5432 listener 前后均为 `127.0.0.1:5432`、PID 10568，未重启或改动。
- Python 子进程启动 WSL 曾报 E_ACCESSDENIED；改为直接 PowerShell 调用同一 postgres 用户的 initdb / pg_ctl 成功，未绕过数据库角色限制，未使用或读取生产凭据。
- 新 router 通过受控 FastAPI 实例连接真实受限服务验证；没有将脏 main.py 带入候选，也未宣称完整生产 create_app / 部署接线已验收。
- 本次兼容修复所需重启期间，已补齐此前未验证的双连接并发接手；串行重放、并发重放、预览后变更和锁内失效均有真实受限 PG 证据。
- 只有现有 Starlette TestClient 弃用警告；未验证 PG17、真实供应商效果、生产接入或 C01。
