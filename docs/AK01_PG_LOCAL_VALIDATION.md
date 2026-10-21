# AK01 本机 PostgreSQL 隔离专项验收

日期：2026-10-20。仅本地虚构资料、假供应商；未联网调用模型、未操作线上。

## 结论

最终冻结候选：**122 passed，0 failed，0 skipped**，耗时 153.34 秒。

- 新增 AK01 专项 27 项：三条路径的双进程领取、两种崩溃恢复、429 费用分类、取消与撤权。
- 既有相关 PG 回归 95 项：服务、队列、独立 worker、费用、权限、公平调度、混合容量和员工预填。
- 另对同一冻结候选定向补验 `test_intelligence_ledger.py` 的 12 个 PostgreSQL 参数用例：**12 passed，0 skipped**，13.49 秒。这是独立补验，不是一次性运行 134 项。
- 一个既有 Starlette TestClient 弃用警告，没有掩盖失败或跳过用例。
- 本报告只证明 **PG14 的上述已执行矩阵**，不证明 PG16/17、CI、真实供应商或生产环境。
- 本轮没有完成或替代 C01、完整恢复、真实企业试点验收。

## 候选与环境隔离

HEAD：`baf3f414187a198dbde1c41f8a90e42dee2629ca`。

使用 HEAD 的独立本地副本：

`tmp_test/ak01-codex-20261020/pg/candidate`

只叠加本轮冻结的 7 个生产文件、合法 token 上限测试修正和新增 PG 专项：

| 生产文件 | SHA-256 |
|---|---|
| intelligence/provider.py | `b296e0005983824abff3d3bc101ddb63af9c780fe764c231bfb1a009d64023b0` |
| intelligence/service.py | `f2d3f1adbd4cb63acc16fef6c6a0ddbd7b9ec83bddee4205fc921926ea1b349f` |
| intelligence/ocr.py | `64dd1d2bff61de6bef8eff87966e354d5bf54921205043fa711fc1a69d77bf45` |
| intake/service.py | `5d891cea5a15d1c182345736c42f4f8e4dcd63432a2ca5271d472752eb64615e` |
| assistant/worker.py | `6a7078ece36e773b90dbb9c72d7e16f8ca435f304db773226d89f1c000000d24` |
| jobs/worker.py | `0407a5c2cf6aeabb56d87955e3bd66aefbd6cd4f7d5ae05e887217faf84a8043` |
| operations/meter.py | `ac093678fbd8fd2e07ceb859838b13363e82dc6b6054d5657d3710f66b7760c5` |

路径前缀均为 `nexus_backend/app/`。测试前后已核验候选与工作区的这些文件指纹一致。

下列候选文件逐字节核验为 HEAD，没有混入其他任务的修改：

- accounting/database.py、main.py、maintenance_worker.py；
- workspace/projection.py、workspace/service.py；
- scripts/migrate_accounting.py；
- test_intelligence_workflow.py、test_postgres_integration.py。

未纳入未提交的 `138_projection_identity.sql`；未修改任何迁移文件。

环境：

- Python 3.14.1。
- WSL Ubuntu 22.04 中已有 PostgreSQL 14.24 二进制。
- 独立临时集群 `/tmp/nexus_ak01_pg_9dce865ffa`，仅监听 `127.0.0.1:15438`。
- 独立空库 `ak01_local_9dce865ffa`，应用 HEAD 既有迁移 134 项、最大编号 135。
- 独立受限 `ak01_runtime_9dce865ffa`、`ak01_worker_9dce865ffa`，均为 NOINHERIT、非超级用户、无 BYPASSRLS。
- API/worker 业务操作使用受限身份。管理员仅用于初始化、断言读取及针对本测试任务的租约故障注入。
- 原本的 PG14 `main:5432` 集群未修改、未重启。
- 未安装软件、未修改原集群配置、未访问生产凭据。

启动器清除供应商、平台与数据库环境变量，再注入明确的临时测试 DSN。
Python 网络限制到本地；子进程假供应商禁止网络连接，数据库目标单独严格核验。
libpq 连接使用明确传入的隔离 DSN；不把 Python socket 防护冒充操作系统网络沙箱。

## 红—绿证据

新增 `test_ak01_postgres.py` 的 429 三路径 × 三种 usage 矩阵，
先对未修复 HEAD 执行：

```text
6 failed, 3 passed
```

6 个失败均是 OCR/proposal 的业务断言：

```text
assert task.status == "failed"
actual: retry_wait
```

覆盖已知用量、显式零、缺失用量。修复前允许已发送的 429 自动进入重试等待。
修复后相同矩阵 9 项全部通过；chat 的已有正确行为保持不变。

首次编写测试时曾遇到夹具 meter 尚未实例化的问题，已经修正后重新取得上述业务失败。
夹具错误没有计入红—绿缺陷证据。

其余双进程、取消、撤权项目属于补充验收；没有虚构原实现失败。

## 新增 27 项专项

| 场景 | 路径 | 项数 | 核心断言 |
|---|---|---:|---|
| 两个独立进程同时领取 | OCR / proposal / chat | 3 | 真实 PG worker 身份；只一个进程取得任务；没有重复 attempt |
| 持久发送标记后、假供应商收件前崩溃 | 三路径 | 3 | 进程硬退出；新进程恢复后 interrupted；收件数仍为 0，费用不能当作免费 |
| 假供应商收件后崩溃 | 三路径 | 3 | 独立新进程恢复；收件数仍为 1；结果不发布，已发送费用保持 held |
| 429：known / zero / missing | 三路径 | 9 | 每条路径发送一次；无自动新 attempt；分别 estimated、已知零、uncertain |
| 发送前取消 | 三路径 | 3 | 发送次数 0，未产生该请求的费用记录 |
| 发送中取消 | 三路径 | 3 | 已知用量 2 + 3、费用 16 micro 保留；内容不发布 |
| 结果接纳时撤权 | 三路径 | 3 | stale、无发布；费用不因权限丢失而丢失 |

双进程领取使用两个独立 Python 解释器、两个实际 PG 连接和确定性启动屏障。
崩溃测试使用 `os._exit(79)`，不是捕获异常后在同一对象上模拟重启。
收件计数写入本地文件并 fsync，恢复进程使用重新构建的 service/worker。
不使用真实供应商或远程 HTTP；不能将假供应商收件解释为供应商实际结算证据。

## 最终回归命令

在已核验的隔离目标中设置专用测试 DSN 和 `ACCOUNTING_AK01_ISOLATED_TARGET=1`，
使用本轮 guarded 本地启动器执行：

```powershell
.\.venv\Scripts\python.exe tmp_test/ak01-codex-20261020/pg/run_local.py `
  nexus_backend/tests/test_ak01_postgres.py `
  nexus_backend/tests/test_queue_worker_postgres.py `
  nexus_backend/tests/test_model_cost_postgres_workflow.py `
  nexus_backend/tests/test_stage7_proposal_postgres.py `
  nexus_backend/tests/test_stage15_assistant_meter_postgres.py `
  nexus_backend/tests/test_stage15_conversations_postgres.py `
  nexus_backend/tests/test_queue_postgres.py `
  nexus_backend/tests/test_stage15_mixed_queue_postgres.py `
  nexus_backend/tests/test_queue_fairness_postgres.py `
  nexus_backend/tests/test_stage12_intake_postgres.py `
  -q --tb=short --junitxml=../pg-final.xml
```

历史命令供审计；临时密码文件已清理。再次执行需重新创建隔离测试目标，不能改用生产 DSN。
新增专项默认不运行，必须显式选择独立 AK01 本地库；不能将默认 skip 算成 CI 验收。

本地证据：

- `tmp_test/ak01-codex-20261020/pg/pg-429-red.xml`：6 个业务失败。
- `tmp_test/ak01-codex-20261020/pg/pg-final.xml`：最终 122 项全部通过。
- `tmp_test/ak01-codex-20261020/pg/final-overlay-hashes.json`：冻结候选清单。
- `tmp_test/ak01-codex-20261020/pg/pg-verification.json`：迁移校验和、受限角色、指纹与清理记录。

新增测试 SHA-256：

`0a692fcfc4d524e57be117e0e1fefa056818d49bed5cd93643c9d2c53bb0b790`

Ruff 检查通过。

## 会计复核与导出定向补验

离线广泛回归清除了数据库 DSN，因此该文件的 PostgreSQL 参数用例没有在离线运行中执行。
本次只重新启动上述 15438 临时集群，重新生成该集群内的临时测试密码，完成独立 PG 补验：

```powershell
.\.venv\Scripts\python.exe tmp_test/ak01-codex-20261020/pg/run_local.py `
  nexus_backend/tests/test_intelligence_ledger.py `
  -k 'postgres and not sqlite' -q --tb=short --junitxml=../pg-ledger-final.xml
```

结果：`12 passed, 12 deselected`。

第一次 `-k postgres` 还选中一个名称含 postgres 的 SQLite 参数用例，结果为 12 passed、1 明确不适用 skipped。
最终选择器明确排除 SQLite，产生包含 12 个实际 PostgreSQL 用例、无跳过的独立 XML。
未重复运行 122 项主体组合，也没有修改测试或生产代码。

覆盖包括当前 OCR 事实对复核/导出的约束、缺失 OCR 和金额不一致的阻断、
审核后启用政策时导出重新核验、正常复核导出不调用外部模型，以及关期边界。
测试文件逐字节等于 HEAD，最终 7 个生产文件与冻结清单的指纹在补验前后均相同。

- XML：`tmp_test/ak01-codex-20261020/pg/pg-ledger-final.xml`
- XML SHA-256：`5219de1b8912b4c130f144f20c5c03c3bad19896c3efa993ad06b55dbfdda01c`
- 测试文件 SHA-256：`d02754069fda1c68de77ebc12ac313e035329d9945f243329cf5a7200f50492a`

迁移仍为 134 项、最大编号 135；结束后测试连接数为 0。
已再次停止临时集群并清除新生成的测试密码文件及重启脚本。

## 清理与尚未证明的范围

最终测试后：

- 隔离库其他连接数为 0。
- 已停止本轮创建的 15438 临时 PG 集群。
- 已删除本轮临时测试密码状态文件及含密码的初始化脚本。
- 原 5432 集群仍在线。
- 保留测试源码、JUnit、无密钥的候选指纹与验证元数据。

本报告没有证明：

- PG16/17 或 CI 执行结果；
- 真实模型效果、真实供应商幂等/账单或网络行为；
- 生产配置、生产数据库或混合版本部署；
- C01 收口、全量备份恢复与真实企业切换。

未提交、未推送、未部署。
