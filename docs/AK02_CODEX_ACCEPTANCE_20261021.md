# AK02 ContextPack 本地接入验收

日期：2026-10-21。基线 HEAD：`baf3f414187a198dbde1c41f8a90e42dee2629ca`。

## 结论

**AK02 本地离线接入与相关回归通过。** ContextPack 已进入助手、票据字段提取、员工附件预填和分录建议的实际请求构造；不是仅新增离线模块或记录字段。

本轮各组测试按 test ID 保留最后一次结果：本地离线 610 项通过，本机隔离 PostgreSQL 14 为 43 项通过。两组分别统计；这是多次定向回归的去重结果，不宣称一次全仓测试或生产验收通过。初次回归中的失败均保留证据，修复后相应用例已重跑通过。

未接真实模型、未访问生产、未提交、未推送、未部署。没有修改或执行项目线上迁移；没有修改 C01、UX、AG04/AG05 或并行 PG 投影实现。会计独立复核后导出流程不变。

## 实际接入

| 范围 | 实现及边界 |
|---|---|
| 单一编译器 | `app/intelligence/context_pack.py` 成为可部署后端实现；原 `tools/ai_inference_lab/context.py` 只兼容转发。后端运行不依赖仓库 tools 目录。序列化版本为 v2，旧 v1 包明确拒绝。 |
| 企业与账套 | 企业级包明确声明无单一账套/期间，并携带授权账套集合；零账套、多账套均支持，不编造账套、期间或发票页码。账套级包继续强制企业、账套、期间绑定。 |
| 事实与证据 | 来源附件、OCR 页、识别文档/字段、业务事项和当前事实版本独立绑定；严格检查引用、哈希、作用域和内容指纹。输入深拷贝，包内部被修改会被检测。 |
| 助手 | 从当前已授权快照构造包，保留原 citation ID；当前问题/历史问题标为不可信输入；缺少事实版本证明的历史助手回答不作为当前事实。保存数据不等于已审核，票据合计不得当收入或利润。 |
| OCR 与预填 | 在费用预留前校验授权来源和必需包预算；Paddle 文本进入原件证据，提取请求实际使用包。原始 OCR 转录不注入业务金额或历史样例，避免污染独立证据。 |
| 分录建议 | 使用重新读取的当前事实构造并发送同一包，校验事项/账套/期间、科目/模板和 input digest；继续由服务校验建议并要求会计复核。 |
| 预算 | 必需事实/规则不足时阻断，不能靠裁掉关键约束发送；可裁剪历史和可选规则。UTF-8 字节估算是保守输入预算，不是供应商计费 token。保留 AK01 完整传输预算、usage 和 attempt 防重发。 |
| 接纳结果 | 员工预填重新检查版本；助手在结果发布的同一数据库写事务中重新检查权限和外发政策。调用后撤权/政策变化：结果 stale、不发布，但保留实际发生的已知费用，不自动重发。 |

ContextPack 不是权限凭证；授权仍由现有服务与数据库执行。直接调用底层 provider 的兼容接口保留，应用服务四条路径均显式提供 ContextPack。

助手 PostgreSQL 发布沿用现有 `assistant_finish` SQL 函数与租约保护。在现有事务锁内以应用角色重检，再切换专用 worker 角色完成发布；未新增授权、迁移或绕过 RLS。真实受限角色路径已验证。

## 失败测试先行与修复

证据根目录：`tmp_test/ak02-codex-20261021/`。

| 复现问题 | 失败证据 | 修复与复验 |
|---|---|---|
| 原编译器缺少部署隔离、公司级范围和严格引用等契约 | `compiler-red.txt`：27 failed / 5 passed；`compiler-import-red.txt`：1 failed | 新编译器及既有 AG02/AG03/AG05 组合 168 passed。 |
| 实际四入口请求仍使用旧上下文 | `ticket-red.log`：3 failed；助手初始 XML：5 failed / 1 passed；PG `pg-ak02-baseline-final.xml`：4 failed / 3 passed | 实际 mock HTTP body 断言；助手、票据和 PG 最终组通过。 |
| 来源企业/归属不一致、旧 prompt 被接受 | `ticket-first-green.log`：4 failed / 12 passed | 服务端来源身份和 prompt/schema 绑定；最终票据组通过。 |
| 分录编包与发送使用不同时间读取的 context；预填版本过期仍被接受 | `binding-red.log`：2 failed | 发送同一份最新 context，预填发送及接纳重新校验；最终票据组通过。 |
| 助手调用期间政策改变仍发布旧回复 | `policy-red-valid.log`：3 failed；PG `pg-policy-red.xml`：1 failed | deny_chat、deny_data、政策版本变化均使结果 stale；SQLite 与 PG 同事务接纳检查通过，费用不丢失。 |
| 手动 HTTP 工作流夹具意外启动 worker，导致月结断言竞态 | `http-fixture-red.log`：1 failed；初次 `workflows.xml` 出现相关失败 | 仅关闭该手动 HTTP 夹具内嵌队列；不改月结断言、不改生产队列。整个相关文件和票据最终组 48 passed。真实 worker/双进程仍有专项覆盖。 |

编译器初始助手 XML：`tmp_test/offline-pytest/9cd7f49b7a1748fdb70c6d69717b3267/results.xml`。

费用预算测试有一处明确契约更新：必需包已经超限的极小预算应在 Paddle 和费用预留前阻断，故原“Paddle 一次且费用 released”改为“零调用、无费用行”。另新增“大段真实 mock Paddle 文本导致后续包超限”的场景，仍断言 Paddle 一次、LLM 零次、预留费用释放，没有删掉下游超限覆盖。

## 回归证据

| 组 | 结果 | 证据 |
|---|---:|---|
| 编译器、AG02/AG03/AG05 | 168 passed | `compiler-green.txt`；`tmp_test/offline-pytest/49cda3f64c574c6aab308e5b16043d2f/results.xml` |
| 助手初始 provider/会话/费用组合 | 56 passed | `tmp_test/offline-pytest/59ff6af928eb443db157a282c21356d9/results.xml`；被最终助手组覆盖的用例不重复计数 |
| 助手与 AK01 chat 专项 | 50 passed | `tmp_test/offline-pytest/88f18903dd5142529de541158c59282a/results.xml` |
| 初始票据/限制组及前置预算修正 | 92 passed / 2 failed → 受影响组 5 passed | `ticket-limits.xml`、`preflight-cost.xml`；后续最终票据组再次覆盖 |
| 初始业务工作流组及夹具修正 | 129 passed / 1 failed → 整文件重跑通过 | `workflows.xml`；最终票据/工作流 XML |
| AK01 attempt、费用重试、provider、usage | 156 passed | `attempt-provider.xml` |
| 助手最终：原子政策接纳、ContextPack、会话及费用 | 52 passed | `assistant-final.xml` |
| 票据最终：ContextPack 与完整 intelligence_workflow | 48 passed | `ticket-workflow-final.xml` |
| PostgreSQL：受限角色、四入口、企业隔离、撤权、费用、会计复核/导出 | 去重 43 passed | `pg/pg-ak02-final.xml`、`pg/pg-ledger-final.xml`、`pg/pg-policy-final.xml` |

去重计数、各 XML SHA-256、最后结果与残留失败清单：`test-evidence-summary.json`。最后结果无 failed / skipped。受影响源文件及新增测试 ruff 通过；保留已有 Starlette 测试客户端弃用警告。

PostgreSQL 细节见 [AK02_PG_LOCAL_VALIDATION.md](AK02_PG_LOCAL_VALIDATION.md)。使用本机隔离 14.24、临时集群、受限 runtime/worker；候选仅叠加必要 AK01/AK02 文件，未纳入并行 C01/投影修改。结束后临时集群已停止、临时凭据已清理，原 5432 未修改。

## 修改归属

本轮新增后端文件：

- `nexus_backend/app/intelligence/context_pack.py`
- `nexus_backend/app/intelligence/runtime_context.py`
- `nexus_backend/app/assistant/context_pack.py`

本轮增量修改：

- intelligence 的 provider、OCR、service；intake/service；assistant/provider、worker。
- `tools/ai_inference_lab/context.py` 兼容转发。
- AK01 的两个测试 mock 契约，以及 `test_intelligence_workflow.py` 的手动 HTTP 夹具；保留其已有改动。
- 新增五个 `test_ak02_*.py` 专项文件。

起始 1433 个可读文件的 hash、工作区 diff/status 已保留。`ownership-check.json` 的 `unowned_changes=[]`，`final-source-hashes.json` 记录候选源文件及测试哈希。未接管其他智能体未提交内容；两项既有不可读临时文件未触碰。

## 本地复跑

使用现有安全离线 runner，清除继承的数据库/模型环境变量，只允许本机 loopback；这是测试 guard，不宣称操作系统级网络隔离。示例：

```powershell
.\.venv\Scripts\python.exe scripts/test_accounting_offline.py nexus_backend/tests/test_ak02_context_pack.py nexus_backend/tests/test_ak02_assistant_context.py nexus_backend/tests/test_ak02_ticket_context.py nexus_backend/tests/test_ak02_policy_freshness.py -q
```

PG 专项须显式准备隔离库及受限角色，按 PG 报告复跑；不要把生产 DSN 注入测试。本轮没有为测试保留明文凭据。

## 未验证与后续门槛

- 真实模型质量、实时 Paddle 图片识别质量、实际供应商延迟/费用未验证。
- PG17 未验证；本轮 PG 证据仅覆盖隔离 PG14 候选，不能给并行迁移或 C01 背书。
- 没有上线、线上浏览器验收或自动发布。
- AK02 本地门槛已通过，可以继续 AK03 本地事实校验工作；这不代表 AK03 或后续执行记录/调度包已完成。
