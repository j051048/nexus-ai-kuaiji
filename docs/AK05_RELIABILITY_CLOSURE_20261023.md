# AK05 本地可靠运行收口

日期：2026-10-23。基线提交：`6642ab596c83af46a92c07aa928215f52479f662`。

本轮限定本地离线实现与回归。未提交、推送、部署，未调用真实模型或访问生产数据库；没有新增或执行迁移。

## 修改归属

本轮修改 Worker、API 生命周期及对应测试，不修改权限、凭证复核/导出守卫、费用计算、发送资格或 attempt 状态机。

开始前已有的以下文件保留，已逐文件 SHA-256 比对：

- `app/accounting/database.py`
- `app/accounting/locking.py`
- `tests/test_batch2_concurrency_postgres.py`
- PostgreSQL 迁移 148、149、150

比对基线在 `tmp_test/ak05-foreign-baseline.json`。C01 不在本轮修改范围。

## 已修复

1. API 本地嵌入模式先停止全部四类 Worker，再共享一个 30 秒排空期限；修复 QueueWorker 缺失 wait_for_idle。启动中途失败、恢复检查/投影初始化失败也清理已经创建的资源。
2. 独立 Queue、Migration、Unified CLI 均须明确确认排空后才能关闭数据库。超时、异常或未知结果保留数据库，使用既有持久租约恢复，不释放未知费用或重发请求。
3. stop 为停止领取信号；wait_for_idle 覆盖 dispatcher、执行器及监控线程的真实退出。排空期间拒绝启动第二代调度，干净退出后可以重新准备运行。
4. Unified 保留两个有界执行槽：助手一槽，后台角色共享一槽。心跳、告警刷新在受跟踪的独立控制线程执行，不阻塞任务提交。
5. 独立 Queue/Assistant 也将心跳移出调度路径；助手告警刷新与心跳分离。任务排空期间继续心跳，最终 stopped 写入和监控线程纳入资源排空。
6. Queue/Assistant 的业务在途数从成功 claim 后开始，finally 递减。等待 claim、预提交 Future 和其他角色的任务不再计入助手在途。Unified 内部 Future 数仅用于本地执行槽及资源排空。
7. Queue 不再在 claim 与 process 之间运行告警维护。观测异常不能搁置已领取任务；业务完成后保留 best-effort 增量维护，以维持失败告警和 OCR 后续分录恢复。
8. 空领取/异常按各角色空闲期限退避，完成事件唤醒调度；后台槽占用时不因另一后台角色已到期而忙循环。空闲前置维护另作节流。
9. --once 改为有限加权一轮，空 OCR 队列不会吞掉批量/迁移角色；重复执行前重新准备，监控线程在返回时收口（超时保留排空状态）。

## 先失败再修复的证据

所有路径均为仓库下 `tmp_test/` 的本地合成测试产物：

| 证据 | 失败表现 |
|---|---|
| ak05-reliability-red.xml | 初始5项全部失败：空闲轮询、慢监控阻塞、排空心跳、有限一轮、维护异常 |
| ak05-lifecycle-red.xml | 初始8项生命周期失败 |
| ak05-cli-red.xml | Queue CLI中途启动/排空错误4项失败 |
| ak05-lifecycle-prepare-red.xml | 停止领取和重启准备7项失败 |
| ak05-assistant-monitor-red.xml | 独立助手监控4项失败 |
| ak05-busy-lane-red.xml | 共享后台槽阻塞时仍高速空转 |
| ak05-lifecycle-extra-red.xml | 重复once、独立Queue慢心跳2项失败 |
| ak05-alert-red.xml | 前置维护节流后，连续任务失败告警未及时刷新；已恢复完成后的增量维护 |

## 真实本地边界

- 使用同一个 SQLite、真实 UnifiedWorker/QueueWorker/AssistantWorker、合成企业和 FakeProvider。
- 明确等待 A 企业 OCR 进入阻塞后，才为 B 企业入队助手；B 完成时 OCR hook 尚未退出且任务仍 running。
- 慢 OCR 排空期间读取至少两个不同的持久 heartbeat_at；完成后核对 stopped/0。
- 监控数据库写入阻塞时仍可调度任务，但排空返回 false，不允许关闭数据库。
- 领取等待不记为已领取业务；停止信号后不再从维护等待继续领取。

## 两处旧正向夹具校正

Stage8 企业规则、HTTP OCR 缓存正向用例使用空白 PNG，没有独立原件文字；现有事实校验正确阻断规则采用/缓存复用。用已提交版本 QueueWorker 单独复现了相同失败：

- `tmp_test/ak05-rule-baseline.xml`
- `tmp_test/ak05-cache-baseline-red.xml`

已为正向夹具提供独立声明的合成原件文本，没有修改生产事实校验或缓存资格。另补企业规则负向：原件1.20、模型2.40时必须 needs_attention，不能应用规则或修改草稿。

## 验收范围

最终两组共 **275 passed，0 failed，0 errors，0 skipped**：队列/生命周期166项，费用/监控/助手109项。汇总和最终候选源码SHA-256见 `tmp_test/ak05-reliability-summary.json`；原始结果分别见 `tmp_test/ak05-core-final.xml`、`tmp_test/ak05-cost-assistant-final.xml`。

此前安全大组 `tmp_test/ak05-reliability-safety.xml` 为192通过、2失败；两处原因与修复已在上文披露，受影响用例复测证据为 `tmp_test/ak05-safety-fixes-green.xml`，不能将原失败大组直接写成全绿。

该大组包括真实loopback HTTP/子进程的AK01发送未知防重发、服务限额、429费用分类、企业预算及既有AK05多进程领取检查。两处失败也均纳入上述最终109项并通过。Ruff check、差异空白检查及本轮新增/主要修改文件格式检查通过；未对既有助手业务体和旧测试做整文件格式重写。

本轮结论：**AK05本地可靠运行收口通过。** PostgreSQL专有并发与恢复门槛须保留为独立待验证项。

真实供应商、生产、PostgreSQL多会话/多进程及完整恢复不在本轮本地门槛内；不能由本轮结果推出C01或AK05全部生产门槛通过。
