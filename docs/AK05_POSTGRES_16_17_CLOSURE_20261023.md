# AK05 测试清理收口与 PostgreSQL 16/17 并发验收

日期：2026-10-23。基线：`8b185b9b2ba07767af98fa3e23e7d2c3f78f3a18`。

## 结论

**此前剩余的投影测试清理权限问题已修复，PostgreSQL 16.15、17.11 的本地 AK05 并发矩阵均通过。** 不再排除原失败测试，没有新增权限、修改迁移或改变 Worker 生产代码。

| 本轮最终分组 | PG16.15 | PG17.11 |
| --- | ---: | ---: |
| UnifiedWorker：慢 OCR／其他企业助手并行、心跳、排空 | 3 passed | 3 passed |
| 真实 OS 多进程：领取、崩溃、租约、费用、停机 | 7 passed | 7 passed |
| OCR／journal／chat attempt 与费用专项 | 27 passed | 27 passed |
| 写域、投影权限、业务流程、公平调度、清理回归等 | 98 passed | 98 passed |
| **合计** | **135 passed** | **135 passed** |

两个版本均 **0 failed、0 errors、0 skipped、0 deselected**。另在 PG14.24 复验清理专项 6 passed；格式化后又在 PG16/17 各复跑这 6 项，重复运行不叠加计入完整矩阵。合计为 270 个 PG16/17 版本化用例运行，加 6 个 PG14 专项。

未提交、未推送、未部署、未访问线上、未接真实模型。此结论只覆盖本轮本地隔离验收，不代表 C01、全部项目验收、真实供应商效果、生产容量或云平台配置已通过。现有 Starlette/httpx 弃用警告保留，非本轮失败项。

## 修改归属与范围

开始核对时，上一轮工作区修改已由另一任务提交为上述基线，因此重新记录完整基线，保留已提交的独立 projection DSN、actorless 企业锁与其他修复。

本轮修改范围仅为：

- 局部修复 `nexus_backend/tests/test_batch2_concurrency_postgres.py` 最后一个测试的清理和关闭逻辑，并收窄该测试的越权异常断言。
- 新增 `nexus_backend/tests/test_ak05_projection_cleanup_postgres.py`。
- 新增本报告；忽略目录 `tmp_test` 保存本轮隔离环境和证据。

对基线 1496 个已跟踪文件复算 SHA-256，除获本次授权修复的目标测试文件外，**其余 1495 个文件均不变**。C01、数据库权限／迁移、生产 Worker、费用与 attempt 实现均未修改。没有 reset、覆盖或回退其他智能体的代码。

## 先红后绿：清理问题如何闭环

新增四种参数组合：已有／无投影状态 × 正常结束／中途业务异常，另新增受限 projection 删除元数据的负向测试。与原失败测试一起执行：

```text
修复前：5 failed, 1 passed
修复后：6 passed
```

红证据：`tmp_test/ak05-pg1617-cleanup-red.xml`。修复前正常路径因 projection 无元数据 DELETE 权限失败，中途异常路径则未关闭数据库连接；权限负向项原本就通过。

修复措施：

1. 业务正向和跨企业负向仍使用受限 `accounting_projection`。跨企业断言从宽泛 `Exception` 收窄为 `InsufficientPrivilege`，明确核对 SQLSTATE `42501`。
2. 只有测试基础设施清理使用既有隔离管理员 fixture。内容清理限定 `firm_id + company_id + 本次随机 ref`，不再按事务所批量删内容。
3. 通过 `INSERT ... ON CONFLICT DO NOTHING RETURNING` 记录是否由本测试插入元数据，且只在事务成功提交后认领清理责任。夹具原有状态不删除。
4. 嵌套 `finally` 保证业务断言失败或管理员清理失败时也关闭 projection 连接。
5. 新回归测试对已有状态整行比对，并保留同企业无关 sentinel；验证只清理自建记录。`has_table_privilege(...,'DELETE')` 仍为 false，实际删除仍返回 `42501` 且行保持不变。

没有为测试通过而新增 GRANT，也没有改成特权身份执行受限业务路径。原失败测试在两个新版本的完整矩阵中都实际执行并通过。

## PostgreSQL 环境与数据库准备

使用 [PostgreSQL 官方 Ubuntu 软件仓库](https://www.postgresql.org/download/linux/ubuntu/) 的 jammy/amd64 包。官方 InRelease 经 GPG 验签，指纹为 `B97B0AFCAA1A47F044F244A07FCC7D46ACCC4CF8`，Packages 和五个所需 deb 的 SHA-256 均验证。仅 `dpkg-deb -x` 解压到新临时目录，未执行系统安装、修改 `/etc` 或升级已有 PostgreSQL。

| 环境 | 真实版本 | 回环端口 | 临时数据目录 |
| --- | --- | ---: | --- |
| PG16 | 16.15 | 25496 | `/tmp/nexus-ak05-pg1617-r1-8c734d/data16` |
| PG17 | 17.11 | 25497 | `/tmp/nexus-ak05-pg1617-r1-8c734d/data17` |

两个实例均确认 `fsync=on`、`synchronous_commit=on`、`full_page_writes=on`。本轮临时环境不使用可选 LLVM JIT。`pgcrypto` 创建、SHA-256 计算和删除已实测。

每个版本分成 `unified/process/attempt/regression` 四个全新数据库，合计八库，避免不同套件全局领取夹具互相干扰。每库从空库应用 **140 个现有迁移文件，最大版本 152**，随后迁移重入无新增，验收后校验和再次全部匹配。迁移存在历史序号空档，不能写成连续 1–152；本轮没有新增或改写任何迁移。

沿用三个独立受限登录身份：`ak05_runtime/ak05_worker/ak05_projection`。登录及能力角色均无超级用户、BYPASSRLS、建库、建角色权限；能力角色 NOLOGIN。运行身份不使用准备阶段的管理员。临时连接只监听本地，测试配置含随机密码并保存在忽略目录，未进入源码或报告。

环境证据：`tmp_test/ak05_pg1617_environment_8c734d.json`、`tmp_test/ak05_pg1617_gpgv_8c734d.txt`。原有 `5432` 集群保持运行。验收结束后停止本轮 PG16/17 临时实例，并把为清理复验而重启的上一轮 `25489` 临时实例恢复为停止状态；保留数据和证据供复核。

## 并发与财务边界实证

两个新版本均复用相同候选源码和相同受控假供应商，验证：

- 两个真正的 UnifiedWorker OS 进程同步竞争领取，按子解释器实际 PID 核对；一个任务不会产生重复 attempt、接收收据或费用。
- 发送标记前崩溃可以在租约到期后恢复；标记后、供应商接收后崩溃保持未知，不自动重发，保留真实费用预留。
- 已提交但响应丢失时保持成功状态，不重复执行；旧租约 token 无法 renew/finish。
- OCR 已进入阻塞后，再提交其他企业助手；数据库同时 running、两路心跳连续增长，助手先完成，OCR 此时仍阻塞。
- stop 后不再领取新任务；短时排空失败仍保留可用 PG 连接，释放任务后排空成功，心跳 stopped/0，最后才关闭数据库。
- 已知 usage 的费用只记一次；未知 usage／金额不伪造为零。429、取消、撤权、三入口费用与 attempt 专项通过。
- 保留企业公平调度、供应商限流、全局并发上限、同企业串行／不同企业并行及原生 SQL 权限拒绝。
- 企业间业务不可见，未复核不可导出，既有审核后导出正向流程仍通过。

这些是本地真实 PostgreSQL 与受控供应商替身的证据，不冒充真实模型调用或外部供应商幂等承诺。

## 复跑与证据索引

从仓库根目录执行，先独立准备上述临时实例和受限账号。配置只提供给现有隔离 runner，不复制密码或连接串到命令行。runner 会拒绝非回环目标、默认 5432、错误数据库前缀、连接参数覆盖及未声明数据库，并阻止供应商外网访问。

```powershell
foreach ($pgMajor in 16,17) {
    .\.venv\Scripts\python.exe scripts/test_accounting_postgres_isolated.py --config "tmp_test/ak05_pg${pgMajor}_unified_config.json" nexus_backend/tests/test_ak05_unified_postgres.py -q --junitxml="tmp_test/ak05-pg${pgMajor}-unified-r1-final.xml"
    .\.venv\Scripts\python.exe scripts/test_accounting_postgres_isolated.py --config "tmp_test/ak05_pg${pgMajor}_process_config.json" nexus_backend/tests/test_ak05_process_postgres.py -q --junitxml="tmp_test/ak05-pg${pgMajor}-process-r1-final.xml"
    .\.venv\Scripts\python.exe scripts/test_accounting_postgres_isolated.py --config "tmp_test/ak05_pg${pgMajor}_attempt_config.json" nexus_backend/tests/test_ak01_postgres.py -q --junitxml="tmp_test/ak05-pg${pgMajor}-attempt-r1-final.xml"
    .\.venv\Scripts\python.exe scripts/test_accounting_postgres_isolated.py --config "tmp_test/ak05_pg${pgMajor}_regression_config.json" nexus_backend/tests/test_migration115_write_gates_postgres.py nexus_backend/tests/test_projection_dispatch_postgres.py nexus_backend/tests/test_batch2_concurrency_postgres.py nexus_backend/tests/test_migration116_proposal_helpers_postgres.py nexus_backend/tests/test_business_postgres.py nexus_backend/tests/test_ak05_pg_scope.py nexus_backend/tests/test_stage15_mixed_queue_postgres.py nexus_backend/tests/test_c01_r3a_postgres.py nexus_backend/tests/test_queue_fairness_postgres.py nexus_backend/tests/test_ak05_projection_cleanup_postgres.py -q --junitxml="tmp_test/ak05-pg${pgMajor}-regression-r1-final.xml"
}
```

机器可读汇总：`tmp_test/ak05-pg1617-summary.json`，包含各 XML 与候选文件哈希、八库迁移状态、环境 manifest 哈希及修改范围核对结果。`ak05-pg14/16/17-cleanup-r1-final.xml` 是格式化后清理专项复验，不重复计数。

Ruff check、仅新增文件的格式检查和 `git diff --check` 通过。原有测试文件只局部改动，没有对整份文件重新格式化。CI、云端及真实供应商验证未执行，本轮不自动发布。
