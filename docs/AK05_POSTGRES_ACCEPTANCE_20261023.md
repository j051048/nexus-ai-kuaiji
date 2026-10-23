# AK05 PostgreSQL 并发与恢复验收

日期：2026-10-23。基线：`6642ab596c83af46a92c07aa928215f52479f662` 加工作区修改。

## 结论与适用范围

**本轮要求的 AK05 本地 PostgreSQL 核心场景通过。** 使用真正的 PostgreSQL 14.24、受限运行身份和独立操作系统进程，完成多进程领取、租约恢复、停机排空、慢 OCR 与其他企业助手并行、费用和 attempt 防重发验证。

最终分组回归 **129 passed、0 failed、0 errors、0 skipped**。另有 **1 条其他任务拥有的既有测试清理失败**，独立复现并保留失败证据；它在最终扩展分组中明确排除，未计入通过，不能据此宣称整个仓库或全部 PostgreSQL 测试通过。

未接真实模型、未连接线上、未提交、未推送、未部署。PG16/PG17、真实供应商和生产容量未验证。本轮不代表 C01 或其他工作包通过。

## 隔离环境与协作边界

- 本轮新建 WSL PostgreSQL 14.24 临时集群：`/tmp/nexus-ak05-pg-2c2e45/data`，仅回环监听，最终端口 `25489`；没有使用或停止原有 `5432` 集群。
- `unified/process/regression/attempt` 四组使用不同的本地数据库，避免不同套件的全局领取夹具互相干扰。仅使用虚构企业、账号、原件和假供应商。
- `ak05_runtime/ak05_worker/ak05_projection` 为受限登录账号；业务测试实际切换应用、worker、projection 能力角色。六个登录／能力角色均非超级用户、无 BYPASSRLS、无建库或建角色权限；三个能力角色不可直接登录。
- 管理员只用于本轮临时环境准备、迁移和独立查验，不作为业务 Worker 身份。临时 DSN/密码只存于忽略目录的本地配置，没有写入本报告或源码。
- 测试 runner 拒绝非回环、默认数据库端口、错误数据库前缀、连接串查询参数覆盖和未声明的数据库连接，并清除继承的云端和供应商环境变量。子进程使用最小环境并单独限制连接目标；假供应商不联网。
- 对本轮基线保护清单中的 **141 个原有文件**重新计算 SHA-256，全部保持原样，包括其他任务正在修改的 `accounting/database.py`、`accounting/locking.py`、`test_batch2_concurrency_postgres.py` 及原有 PostgreSQL 迁移。上一轮七个 Worker/生命周期生产源码也未再修改。
- 未修改 C01 的服务、模型、迁移、测试和报告；仅复跑其既有 PostgreSQL 专项。

## 先红后绿与实际修复

### 写域守卫回归：前向迁移修复

空库应用现有迁移后，在真实企业写事务中创建业务资料／部门审批人即失败：

```text
psycopg.errors.ObjectNotInPrerequisiteState:
Legacy write domain cannot enter a company transaction
```

`148–150` 的守卫替换丢失了 `115` 中通用 `company_id` 及 book/case 归属解析，普通合法企业表被错误送入旧全局写域。初始失败证据：

- `tmp_test/ak05-pg-smoke.xml`：2 failed。
- `tmp_test/ak05-pg-unified-red.xml`：3 setup errors。

本轮**没有修改已存在或已应用迁移**，新增：

1. `151_preserve_company_write_domains.sql`：恢复完整企业、账套、事项归属解析，同时保留 actorless projection 的精确企业／事务所限制和原 dispatcher 表白名单。
2. `152_preserve_projection_scope_checks.sql`：保留两张 workspace 投影表在非 projection 路径上的严格 firm/company 校验。151 已在隔离库应用后，复审发现这一遗漏，因此用新的 152 补齐，没有修改 151 校验和。

没有新增宽泛 GRANT、关闭触发器、绕过 RLS 或修改旧校验和。最终四个测试库均有 **140 个迁移文件对应记录，最大版本 152**；迁移序号存在历史空档，不能表述为连续 1–152。四库状态均为已应用、校验和匹配。

注意：148–150 是其他任务的未提交变更；未来候选合并时必须统一核对 148–152 的依赖。**本轮没有上线这些迁移。**

### 验收夹具校正

- 多进程测试初版以 Windows `Popen.pid` 查询心跳，但该 PID 可能属于 Python 启动器。失败证据在 `tmp_test/ak05-pg-process-red.xml`；改为子解释器实际 `os.getpid()` 回传，按真实 Worker PID 查验。
- 旧 migration116 正向夹具仅提供空图片和模型自报文字，现有 AK03 正确拒绝。仅在该正向测试增加独立合成原文证据，保留生产事实校验；没有把模型自报内容当原件。其连续运行场景改为有界等待真实供应商限流容量，没有关闭限流器。
- 费用未知场景显式开启企业配额和 0.10 元预留，再断言未知费用保留预留；没有用“默认配额关闭、预留为零”的夹具推导费用丢失。
- 最终从 Windows 临时端口范围内的初始端口迁到 `25489`，重新执行最终套件；早期端口冲突不作为产品缺陷。

## 实际覆盖的核心场景

| 场景 | 实际断言 |
| --- | --- |
| 多进程领取 | 两个独立 UnifiedWorker 子进程通过 ready/go 屏障同时启动；一个任务及多企业任务均不重复领取，核对真实 attempt、持久状态和假供应商 fsync 收据 |
| 发送标记前崩溃 | 子进程已领取后退出；确认旧进程结束再让该任务租约到期，新进程恢复，第二次 attempt 只实际发送一次，计费一次 |
| 发送标记后崩溃 | marker 已提交、供应商收据尚不存在时退出；恢复为 interrupted，费用未知，不自动重发、不释放真实预留 |
| 供应商接收后崩溃 | 收据已经 fsync 后退出；恢复仍保持未知、不重复发送、不重复记费 |
| 提交后响应丢失 | 成功状态已提交但子进程未回传结果；新进程仍识别 succeeded，不产生新 attempt、收据或费用 |
| 旧租约围栏 | 恢复后旧 lease token 的 renew/finish 都失败，不能接纳旧执行结果 |
| 真实停机排空 | 运行中 stop 后短等待返回 false，PG 仍打开且 SELECT 1 成功；释放任务后排空成功，监控线程结束、落库 stopped/0，再关闭数据库 |
| 慢 OCR 与助手并行 | 先确认企业 A 的 OCR 已进入阻塞，再提交企业 B 助手；数据库同时 running，两条心跳各至少两次递增；B 完成时 A 仍阻塞 |
| 停止后不再领取 | stop 后新企业任务不被该 Worker 领取，排空期间已运行 OCR 仍维持心跳 |
| 已知与未知费用 | 固定实际 usage 2/3 和配置价格得到每项一次 16 micro 费用；未知发送保留 sent/uncertain、金额与 token 为空及真实预留，不伪造零费用 |
| 调度与隔离 | 保留全局并发上限、企业公平、跨角色互斥、同企串行／异企并行、受限角色和原生 SQL 越权拒绝 |
| 会计边界 | 企业间详情不可见；未复核不能导出；既有审核后导出正向流程仍通过 |

费用与 attempt 扩展回归还覆盖 OCR、journal、chat 三入口的并发领取、429 已知／显式零／缺失 usage、取消和撤权后结果接纳保护。它们不等于真实供应商端到端效果或供应商幂等保证。

## 最终结果与复跑

| 证据文件（均位于 tmp_test） | 结果 |
| --- | --- |
| `ak05-pg-unified-final.xml` | 3 passed |
| `ak05-pg-process-final.xml` | 7 passed |
| `ak05-pg-attempt-final.xml` | 27 passed |
| `ak05-pg-guards-final.xml` | 91 passed，显式排除下述 1 条 |
| `ak05-pg-fairness-final.xml` | 1 passed |
| 合计通过 | **129** |
| `ak05-pg-foreign-cleanup-unresolved.xml` | **单独复现 1 failed，未计入通过** |

Ruff check、六个本轮 Python 文件 format check、`git diff --check` 通过。机器可读汇总及 XML／候选文件哈希在 `tmp_test/ak05-pg-summary.json`；目录被忽略，不包含在提交范围。

在同样的隔离临时环境准备好后，从仓库根目录分别执行（配置文件仅存在于本机，不共享其密码）：

```powershell
.\.venv\Scripts\python.exe scripts/test_accounting_postgres_isolated.py --config tmp_test/ak05_pg_unified_config.json nexus_backend/tests/test_ak05_unified_postgres.py -q --junitxml=tmp_test/ak05-pg-unified-final.xml

.\.venv\Scripts\python.exe scripts/test_accounting_postgres_isolated.py --config tmp_test/ak05_pg_process_config.json nexus_backend/tests/test_ak05_process_postgres.py -q --junitxml=tmp_test/ak05-pg-process-final.xml

.\.venv\Scripts\python.exe scripts/test_accounting_postgres_isolated.py --config tmp_test/ak05_pg_attempt_config.json nexus_backend/tests/test_ak01_postgres.py -q --junitxml=tmp_test/ak05-pg-attempt-final.xml

.\.venv\Scripts\python.exe scripts/test_accounting_postgres_isolated.py --config tmp_test/ak05_pg_regression_config.json nexus_backend/tests/test_migration115_write_gates_postgres.py nexus_backend/tests/test_projection_dispatch_postgres.py nexus_backend/tests/test_batch2_concurrency_postgres.py nexus_backend/tests/test_migration116_proposal_helpers_postgres.py nexus_backend/tests/test_business_postgres.py nexus_backend/tests/test_ak05_pg_scope.py nexus_backend/tests/test_stage15_mixed_queue_postgres.py nexus_backend/tests/test_c01_r3a_postgres.py -k "not test_real_pg_system_company_scope_refreshes_projection_without_global_lock" -q --junitxml=tmp_test/ak05-pg-guards-final.xml

.\.venv\Scripts\python.exe scripts/test_accounting_postgres_isolated.py --config tmp_test/ak05_pg_regression_config.json nexus_backend/tests/test_queue_fairness_postgres.py -q --junitxml=tmp_test/ak05-pg-fairness-final.xml
```

runner 自身不创建、迁移或删除数据库。不要将测试指向生产，也不要让多个套件并行共享会全局领取的同一个测试库。

## 未隐藏的剩余问题

`test_batch2_concurrency_postgres.py::test_real_pg_system_company_scope_refreshes_projection_without_global_lock` 的同企业投影写入及跨企业拒绝已实际执行，最后清理却使用 projection 身份删除 `workspace_projection_state`，该角色没有 DELETE 权限：

```text
psycopg.errors.InsufficientPrivilege:
permission denied for table workspace_projection_state
```

该文件是其他任务已有未提交修改，本轮保持字节不变。没有为测试通过而增授 DELETE。独立新增的 scope 测试已验证同企业成功、跨企业和跨事务所拒绝。原测试应由其负责人改为使用隔离管理员或合适的 fixture 所有者清理，之后再计入其验收；本报告明确保留失败。

此外，本轮只验证 PG14.24 和受控假供应商；PG16/17、真实供应商、全部项目技术验收及真实企业试点仍需独立完成。临时集群验收后停止，数据和证据保留，便于复核。
