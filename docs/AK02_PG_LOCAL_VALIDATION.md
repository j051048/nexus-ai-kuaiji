# AK02 本机 PostgreSQL 验证

日期：2026-10-21。仅本机隔离 PG14，真实受限数据库角色 + 合成 HTTP；没有真实模型、生产连接、提交、推送或部署。

## 结论

- 新增四入口 ContextPack 专项及发送后政策撤销保护通过。
- 初始定向组合 30 passed；独立账务复核/导出 PG 参数 12 passed；助手原子接纳修复后的专项与助手回归 20 passed。后者替代前者中重复的助手用例，不能相加成一次全量通过。
- 三组按 test ID 去重为 43 项；本轮仅对实际改动相关路径验收，PG17、真实模型效果、生产接线未验证。

## 候选与隔离

- HEAD：`baf3f414187a198dbde1c41f8a90e42dee2629ca`。从 HEAD 导出全新 candidate，仅叠加明确列出的 AK02 源码与 AK01 jobs/worker、operations/meter 补丁。
- 候选目录：`tmp_test/ak02-codex-20261021/pg/candidate`。
- PostgreSQL 14.24；仅 `127.0.0.1:15438`；复用此前已停止的临时集群。原 5432 集群未修改/重启。
- 临时数据库 `ak01_local_9dce865ffa`；业务/worker 登录均为 NOSUPERUSER、NOBYPASSRLS、NOINHERIT。
- 实际验证 worker 登录可以在同一事务中切换 `accounting_app` → `accounting_worker`，最终助手正/负路径已通过。
- 候选迁移 134 个，最大版本 135；不包含并行 138。未修改 C01 或任何迁移。
- database/main/maintenance/workspace projection/workspace service/migrate/原有PG integration/原有workflow夹具等 8 文件与 HEAD 逐字节一致；未叠加其并行修改。

## 红—绿证据

1. 对上轮 AK01 冻结候选运行相同新增专项：`4 failed, 3 passed`。OCR、intake、journal、chat 实际请求缺少 ContextPack；旧请求同样返回有效合成响应，失败落在请求契约断言。证据：`pg-ak02-baseline-final.xml`。
2. AK02 初始候选：四入口及隔离/撤权 `7 passed`，随后纳入 30 项定向组合。
3. 新增发送中通过受限 API 撤销 chat 外发政策的 PG 专项：旧接纳路径错误发布 `succeeded`，预期 `stale`，`1 failed`。证据：`pg-policy-red.xml`。
4. 覆盖 root 修复后的 assistant/worker.py：新增专项与既有费用/对话组 `20 passed`；政策撤销后费用保持已知 3/4 token、22 micro，内容不发布、无自动重发。
5. 首次探索中的 1 项失败来自测试误把公司级助手 scope 当单账套。已依据实际契约改为 `book_id=None` 与 `books` 精确集合，未当生产缺陷统计。

## 新增专项（test_ak02_postgres.py）

- OCR、journal、intake 三入口实际 MockTransport payload：firm/company/task/版本/指纹、原件身份、未知账套不编造、费用持久化、相同请求只发送一次。
- 助手独立 worker 发送授权包：企业/账套集合、必需规则、实际 usage 与费用。
- 他企 OCR/intake：零发送、零费用。
- 助手发送前撤权：stale、未发送、无费用。
- 助手发送后外发政策撤销：stale、保留已知费用、无助手消息、不重发。

## 既有回归范围

- stage15 conversations 与 assistant meter：RLS、不可变输入/历史、租约与费用。
- stage12 intake：私有草稿、原件、独立审批、转移识别不重复收费。
- 原 worker OCR 全链路、stage7 自动草稿/模板不调用模型、AI 完整独立复核后导出。
- test_intelligence_ledger.py 使用 `-k "postgres and not sqlite"`，严格选择 12 个 PostgreSQL 参数，0 skip；验证异常阻断、复核、导出、期间保护。

## 证据文件

| 文件 | cases | failures | skipped | SHA-256 |
|---|---:|---:|---:|---|
| `pg-ak02-baseline-final.xml` | 7 | 4 | 0 | `46f7c3c294c0f6f935dbdba77e2c9825ff95e7e1a85cd03e8b9e9b92b1105914` |
| `pg-ak02-final.xml` | 30 | 0 | 0 | `6cd9532b8ae47b6db2f8d07ab0e34e40c3c8a4a1bf491b6551ccf4aa5af8eb54` |
| `pg-ledger-final.xml` | 12 | 0 | 0 | `02b0900c9473386c2284a1e96a9490d848059d2a70d38c39a0920ad689194fcb` |
| `pg-policy-red.xml` | 1 | 1 | 0 | `d79975647dfd9f3d2a47b13158b011e5a18a31a265faae2962701df96d253f34` |
| `pg-policy-final.xml` | 20 | 0 | 0 | `6e7c16dd48e289f3bb13716d0d7401600e24e947cef192f7305789b0adb36790` |

以上 XML 位于 `tmp_test/ak02-codex-20261021/pg/`。完整迁移校验和、角色属性、前后源码 hash 和清理结果：`pg-verification.json`。源码清单：`final-overlay-hashes.json`。

## 最终源码 SHA-256

| 文件 | SHA-256 |
|---|---|
| `nexus_backend/app/intelligence/context_pack.py` | `ad3408ac579d9167594c9120759aca86f4721f2542daa008c95c7451332d4bda` |
| `nexus_backend/app/intelligence/runtime_context.py` | `5b93c91de3627b1605c8ef05720df14fdd2bba789f29a4c7877524e166c42230` |
| `nexus_backend/app/intelligence/provider.py` | `661d7d9c96b74d89401c6f20fff405f38126d7f1ba97a4e76638a856e823ff0a` |
| `nexus_backend/app/intelligence/ocr.py` | `f9af5ddb3a7a32104e3ef1aca0da4474a54b856aab640ec5b17831b70f76601f` |
| `nexus_backend/app/intelligence/service.py` | `770b718216d1400f66c89a98502259dd738869f522538ca6d83222a0fb50efcf` |
| `nexus_backend/app/assistant/context_pack.py` | `6176182fce2f545eaf0d7827f4cfed52a1f497688bf42c0e939afe4890ebc782` |
| `nexus_backend/app/assistant/provider.py` | `396f5e14492a7f4292ba39bf554dfb2bc02cd730a0eb75fcef5da43171e855ca` |
| `nexus_backend/app/assistant/worker.py` | `c645be5a392e17a8d41b93a66a1c71a9a6057000b28eae2d15fec2d93fc88430` |
| `nexus_backend/app/intake/service.py` | `a35f4b8c600567de9a2658e9cb9d68e4595cc07faa2259b0e4f6f980b8f5321d` |
| `nexus_backend/app/jobs/worker.py` | `0407a5c2cf6aeabb56d87955e3bd66aefbd6cd4f7d5ae05e887217faf84a8043` |
| `nexus_backend/app/operations/meter.py` | `ac093678fbd8fd2e07ceb859838b13363e82dc6b6054d5657d3710f66b7760c5` |
| `tools/ai_inference_lab/context.py` | `e608d90a87e86ff671a58953cb8049e1cf61ed898c0b7547f31d8bd47c7dc242` |
| `nexus_backend/tests/test_ak02_postgres.py` | `ab5e426c6706a418d9c5177200b96b5941bc9d1d26b972ba987ee5980abfd342` |

## 清理与限制

- 测试结束时该临时库其他连接数为 0；15438 已停止，pg_isready 返回 no response。
- 本轮临时密码状态文件与含密码启动脚本已删除；报告与日志不包含密码。原 5432 保持 online。
- runner 清除继承数据库/供应商变量，显式 DSN 指向隔离库；Python socket 限 loopback，psycopg wrapper 限 host/port/db。此为测试 guard，不宣称操作系统级网络隔离。
- 未覆盖 PG17、新供应商、实时OCR质量、线上部署；未改变独立会计复核后导出边界。
- 新增测试 ruff 通过。保留已有 Starlette 测试客户端弃用警告，非失败。
