# 第二批：有界并发与企业公平调度

本轮为 2–3 家企业试点提供可测量的扩容底座。默认处理上限仍为 2；企业账务审批、分录套用、导出、关期及权限修改继续使用原排他事务。没有宣称已验证 200–500 家企业容量。

## 兼容分锁

- 未适配领域保留原 `6420031786281903101` 排他锁。
- 已审查的后台 OCR 创建、发送前检查、结果持久化使用同一个键的共享锁，再取得稳定企业锁。
- 同一企业串行，不同企业可并行；任何旧排他写入都与共享写入互斥。
- 数据库对实际被修改的行校验声明的企业范围。跨范围写入、在企业事务进入未适配领域会立即拒绝，避免共享锁隐式升级造成死锁。
- 金额预留函数跟随企业范围；已经发送的费用结算保留原事务，以便人员撤权后仍能根据不透明令牌保留费用事实。
- SQLite 保留 `BEGIN IMMEDIATE`，不通过进程内锁伪装跨进程并行。

内部接口：`db.transaction(write=True, actor=actor, company_id=company_id, write_scope="company")`。只有经过原生守卫回归的领域才能使用；默认 `write_scope="global"`。

## 三通道调度与供应商限制

OCR、分录建议和对话共享全局运行额度及企业轮转顺序。同企业三种任务合计最多一个 `running`，大客户批次不能一直占据首轮。领取和最多 100 项的恢复扫描使用短调度锁；模型请求期间不持有数据库连接或调度锁。旧流程的租约令牌、资料版本、实时权限、未知结果人工重试约束继续有效。

模型传输另有三层限制：进程供应商 hostname 门、数据库全局供应商槽位、每企业最多一个供应商请求。OCR、分录建议、员工预填和对话都接入。不同 API 密钥或模型不会把同一供应商的容量重复计算。槽位带 20–120 秒租约，故障重启后过期自动回收；本地 XML 解析和 OCR 缓存复用不占供应商槽位。

数据库槽位表与容量表没有授予 API/worker 直接读写权限；获取函数重新核验当前企业权限，释放函数只能处理本次身份的请求编号。没有向普通用户公开其他客户的运行信息。

## 配置与试点扩容

先停写、备份数据库及原件，用管理员执行 PostgreSQL 077–078 迁移；部署同版 API、OCR worker 和助手 worker。管理员连接只用于离线维护。

```sh
python scripts/configure_processing_capacity.py --processing 4 --provider-global 3 --provider-default 2 --supplier api.example.com=1
```

命令读取 `ACCOUNTING_ADMIN_DATABASE_URL`，不能将该连接填入运行服务。三个全局值及供应商值均有 1–8 的数据库边界。降低上限允许已有工作完成，但阻止新任务或请求越过新上限。

运行服务配置：

```env
ACCOUNTING_PROCESSING_GLOBAL_LIMIT=4
ACCOUNTING_QUEUE_CONCURRENCY=2
ACCOUNTING_ASSISTANT_CONCURRENCY=1
ACCOUNTING_AI_MAX_CONCURRENCY=2
ACCOUNTING_DB_POOL_MAX=4
```

实际领取上限取 worker 请求值与数据库持久上限中较小者。每个 worker 的线程仍保持 1–2；需要更多并行任务时增加独立 worker 副本，并同时校准数据库连接总数、供应商容量与企业月费用上限。仅提高线程或环境数字不会绕过数据库额度。

先对 2–3 家企业测量排队 P95、锁等待、请求成功率、任务恢复、月费用暴露和会计处理时间，再逐步升到 3–4 个后台任务。若供应商容量低于任务容量，多余任务进入可重试等待，费用不会当成实际消耗；已经发送但结果未知的请求保留费用预留与人工处理。

## 可复现验证

`test_batch2_concurrency_postgres.py` 用真实受限 PostgreSQL 账号验证不同企业持锁重叠、同企业互斥、旧新门互斥、原生跨范围拒绝、两企业完整 OCR 结果持久化并行、全局上限、企业轮转、供应商槽位、权限和过期恢复。旧队列、预算及撤权测试一起运行。

```sh
python -m pytest nexus_backend/tests/test_batch2_concurrency.py nexus_backend/tests/test_batch2_concurrency_postgres.py
python scripts/benchmark_processing.py --requests 12 --latency-ms 150
```

合成传输压测只访问脚本启动的 `127.0.0.1` HTTP 服务，不读取云密钥、不发送财务原件。2026-10-14 的一次运行：并发 1/2/4 完成 12 个任务分别约 2.088/0.972/0.511 秒，供应商峰值分别为 1/2/4；这是固定延迟合成请求的重叠能力，不是模型正确率或真实企业吞吐承诺。
