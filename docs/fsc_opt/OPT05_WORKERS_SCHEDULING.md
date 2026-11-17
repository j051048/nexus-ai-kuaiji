# OPT05：批量 worker 部署、续租、队列化开账、定时触发、关账恢复

先读 `OPT00_MASTER.md`。无前置。五名财务办理五家单位，依赖批量任务可靠执行和到点自动准备。

## OPT05-A  批量 worker 的正式部署角色

**问题。** `scripts/run_accounting_service.py` 的 `ROLES` 只有 `api, worker, assistant, migration, maintenance`。`BatchWorker` 只在两处启动：`nexus_backend/app/main.py` 的嵌入模式（容器入口强制 `ACCOUNTING_QUEUE_EMBEDDED_WORKER=0`）和 `nexus_backend/app/unified_worker.py`。按 `docker-compose.demo.yml` 部署时，批量接口返回 202 后任务一直排队。

**核实。** 搜索 `BatchWorker` 的全部启动位置，并读 `scripts/run_accounting_unified.py`（存在的话），确认上述结论仍成立。

**做法。**

1. 在 `ROLES` 和 `WORKER_MODULES` 中增加 `batch` 角色，对应一个只运行 `BatchWorker` 的入口模块。入口的构造方式照 `unified_worker.py` 中创建 `BatchOperationsService` 的写法，健康探针、信号处理、心跳照 `app.jobs.worker` 的入口写法。
2. 同步更新角色校验的中文报错文案、`docker-compose.demo.yml`、`Dockerfile` 或相关部署说明、`README.md` 中的角色列表。
3. 不改 `unified_worker.py` 的行为。

**先失败测试。**

- `scripts/` 下已有入口脚本的测试（搜索 `run_accounting_service` 的测试）：`batch` 角色修改前被拒，修改后通过校验。
- 集成用例：不开嵌入 worker，创建一个批量操作，启动 `batch` 入口处理一轮，任务从 `queued` 到终态。

**验收。** 新增用例通过；`python scripts/check_demo_deployment.py`（存在则跑）通过。

## OPT05-B  批量 worker 续租

**问题。** 批量任务租约固定 120 秒，`nexus_backend/app/batch_ops/worker.py` 没有续租。运行超过 120 秒的任务会被恢复逻辑重新认领，原执行仍在继续，靠围栏兜住但白白消耗 3 次尝试中的 1 次。

**做法。** 照 `nexus_backend/app/jobs/worker.py` 的续租实现（每不超过 15 秒续一次，续租失败即视为失去所有权并停止提交结果），给 `BatchWorker` 加同样机制。续租要校验租约令牌。

**先失败测试。** 用可控时钟或缩短的租约时长模拟一个超过租约的任务：修改前出现第二次认领且 `attempts` 增加；修改后只认领一次。再测：续租期间任务被取消或租约被他人夺走时，原执行不写入结果。

**验收。** `python -m pytest nexus_backend/tests -q -k "batch"`。

## OPT05-C  批量开账改走队列

**问题。** `nexus_backend/app/monthly/workbench_router.py` 的 `POST /batch/open` 调用 `nexus_backend/app/monthly/batch.py` 的 `open_month`，在请求内同步循环每家企业，每家又是多个独立事务。无租约、无重试，子步骤失败只作为警告返回。

**做法。**

1. 接口改为创建一个批量操作（每家企业一个条目），返回 202 和批次标识，由 `BatchWorker` 执行。复用 `batch_ops` 现有的条目状态、失败重试、失败清单导出。
2. 单家企业的开账子步骤（建期间、复制授权、建材料清单、写审计）：能放进一个事务的放进一个事务；确实不能的，保证重跑幂等，并且任一子步骤失败时条目状态为失败而不是"成功带警告"。
3. 前端调用方同步改为轮询批次状态。先搜索 `batch/open` 的前端调用点；页面已有批次进度组件则复用。
4. 保留幂等：同一事务所同一期间重复提交不产生重复期间。

**先失败测试。**

- 接口在企业数较多时仍立即返回 202，响应中没有逐家结果。
- 注入第三家企业的某个子步骤失败：该条目失败，其余成功，重试失败项后全部成功，没有重复数据。
- 重复提交同一期间返回同一批次或 409（照现有 `batch_ops` 幂等约定）。

**验收。** 相关后端测试通过；`e2e/monthly-batch.spec.ts` 本机能跑则跑。

## OPT05-D  定时触发器

**问题。** 仓库没有任何定时机制。月初开账、周期计提、报税期限提醒全靠人点。

**设计约束。**

1. 定时器只做一件事：**到点创建待办或把任务放进现有队列**。它不直接过账、不关账、不对外发送、不调用模型。
2. 计划存在数据库表里：事务所、企业（可空表示全所）、类型、周期规则、下次触发时间、启用状态、创建人、版本。计划的增删改要有权限检查和审计。
3. 触发幂等：每个"计划 + 触发时点"在表里有唯一行，多个进程同时扫描只会有一个成功。
4. 进程停机后恢复：错过的触发点按类型决定补跑一次还是跳过，写明规则，不能把错过的每一次都补跑。
5. 时区用 `Asia/Shanghai` 计算触发时点，存储用 UTC。
6. 首批只支持三种类型：
   - 月初开账：到点创建 OPT05-C 的批量开账批次，状态为"待确认"，需有权限的人点击后才执行。OPT05-C 未完成时，先只生成待办提醒。
   - 周期计提与折旧：到点生成"待生成"的提醒条目，指向 `periodic` 现有入口，不自动生成凭证。
   - 报税期限提醒：按配置的每月截止日提前若干天生成提醒。截止日是配置数据，不写死，不从行业名推断。
7. 放在哪个进程：先读 `nexus_backend/app/maintenance_worker.py` 和 `unified_worker.py` 的循环结构，挂到合适的现有循环里。确需新角色时照 OPT05-A 的方式添加。

**先失败测试。**

- 到点后产生恰好一条触发记录和对应待办；再扫描不重复。
- 两个扫描器并发，只产生一条。
- 停机跨过三个触发点后恢复，行为符合你写明的补跑规则。
- 计划被停用或企业授权被撤销后不再触发。
- 无权限用户不能创建或修改计划；修改有审计行。
- 触发不产生任何凭证、过账、关账或外发副作用（断言相关表行数不变）。

**验收。** 新增用例 SQLite 与 PG16、PG17 通过。

## OPT05-E  关账发布可恢复

**问题。** `nexus_backend/app/b04_close/service.py` 的发布流程跨三个事务：读取、`reports.publish`、更新状态。中间崩溃会留下"报表已发布、关账批次仍为 `locked`"。

**做法。** 二选一，选改动小且能证明正确的：

- 合并为一个事务（先确认 `reports.publish` 内部没有自开事务或跨范围写入）。
- 保持分段，但让发布操作幂等：重试时识别"报表已发布"，直接补完状态更新，结果与一次成功完全相同。

**先失败测试。** 在第二段成功后、第三段之前注入异常；随后重试发布。断言：最终状态为 `published`，报表只有一份，审计记录不重复或有明确的"恢复完成"记录。修改前的行为如实记录在状态文件。

**验收。** `python -m pytest nexus_backend/tests -q -k "close"`，PG 侧同样通过。
