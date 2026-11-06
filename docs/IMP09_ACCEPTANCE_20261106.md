# IMP09 验收记录：性能、容量与前端按需边界

- 日期：2026-11-06
- 工程状态：**PG17 与 PG16 30 分钟持续负载均通过**；前端预算门禁通过；真实多节点、生产网络未验证。
- 候选：HEAD `143d4aea7154b1905b96ab757ecd14865354b641`，source 1927 / `e6a947031fe9e3b4bd23b522b29ef46bbfddd89232a5c9d989b70a170f44baa5`，migration 338 / `2c96acd52de191347a2365962e879340918fad013786faa260b4b13140646116`。
- 运行目录：`tmp_test/imp/imp-20261106-a/imp09-capacity-pg17-soak-r2/`。
- 边界：同机、单 API 进程 + 两个 worker 子进程、私有无头原件存储；不是多节点，不是生产网络，不称 200–500 家已支持。

## 09-T 对照

| 编号 | 要求 | 结果 |
| --- | --- | --- |
| 09-T1 | vendor 拆分/依赖闭包/预算门禁 | 复用 `scripts/frontend_budget.test.mjs`：**6 passed / 119ms**；拒绝伪 split、依赖缺失、raw/gzip 超限、越界路径。现有 `frontend_budget.mjs dist` passed；未抬高预算 |
| 09-T2 | 后台高频轮询/旧请求写页面 | 只读复核 `src/OperationsPerformance.tsx:55-80`：15s 轮询、`document.hidden` 跳过、组件卸载/换企业 `abort()` + `clearInterval`；未发现真实缺陷，**未改代码** |
| 09-T3 | 准入拒绝不记成功，过载与容量状态分开 | 30 分钟运行中任何异常/拒绝会使报告 failed；本次无失败请求。100 并发过载边界仍引用 57bb 诊断，**不是本候选通过项** |
| 09-T4 | 多进程领取、超时、取消/重启，不重复导出/建包/收费/attempt | 本 soak 两 worker 各 100 项，200 份导出/200 个包，输出集合与 200 企业精确一致（PG16 导出 23.72s、建包 33.706s）；取消/重试由 `test_batch_operations.py` 等 32 passed 覆盖；soak 内未做 worker 强杀/租约到期注入 |
| 09-T5 | 排队期间撤权、不同企业请求交错 | 每个读取都走受限服务/授权；最终 200 企业集合精确；跨企业原件 404 断言；**排队期间撤权的动态交错未在 soak 中覆盖** |
| 09-T6 | 失败/取消后在途、连接、等待名额归还；无确定性泄漏 | PG17 结束 pool 4/4 可用；PG16 快照 3/4 可用（进程随后退出，未证明长期泄漏）；两库 0 connection failures、0 lock wait、0 writes/rollbacks（900s 采样窗）；RSS 分别 76.9→114.6MB、77.3→114.0MB，**不能据此证明长期无泄漏** |

## 运行配置

- PG17，端口 25498，受限角色；数据库迁移 217 已应用；历史 checksum 未改。
- 两批各 200 家虚构企业：A 批真实复核后批量导出；B 批真实月结后批量生成月包。
- 导出/建包各 2 个 worker 子进程（`spawn`），服务端独立连接池；持续读并发 8。
- 预热：批量导出/建包先完成；持续读从 0 秒开始计时，目标 1800 秒。
- 客户端：`tools/capacity/firm_operations.py`，仅本地 loopback/私有存储。

## 批量结果

| 阶段 | 结果 | 证据 |
| --- | --- | --- |
| 批量导出 | **200 项 / 13.554s**；两个 worker PID 不同，各 100 项 | `report.json.phases.batch_export` |
| 批量月包 | **200 项 / 34.132s**；两个 worker 各 100 项 | `report.json.phases.batch_package` |
| 输出守恒 | `company_id` 集合精确 200、`output_id` 唯一 200；无重复 | `validate_results` |
| 持续读 | **1800.601s / 1433 轮 / 34392 请求**（每类 11464） | `phases.sustained_reads` |

### PG16 同配置

| 指标 | PG17 | PG16 |
| --- | --- | --- |
| 批量导出 | 200 / 13.554s | 200 / 23.720s |
| 批量月包 | 200 / 34.132s | 200 / 33.706s |
| 持续读 | 1800.601s / 1433 轮 | 1800.073s / 1364 轮 |
| 客户快照 p95 | 1170.69ms | 1231.88ms |
| 月包 p95 | 592.71ms | 623.77ms |
| 原件 p95 | 593.25ms | 623.66ms |
| 写/回滚/连接失败/锁等待 | 0/0/0/0 | 0/0/0/0 |
| 结束 pool 可用 | 4/4 | 3/4（进程随后退出） |
| CPU / RSS | 259.047s / 76.9→114.6MB | 249.047s / 77.3→114.0MB |

## 持续读延迟（成功请求）

| 读取 | p50 | p95 | max |
| --- | --- | --- | --- |
| 客户分页快照（20/页，total=200） | 611.44 ms | 1170.69 ms | 1358.65 ms |
| 月包下载（ZIP） | 30.37 ms | 592.71 ms | 709.46 ms |
| 原件读取 + SHA-256 校验 | 34.95 ms | 593.25 ms | 707.52 ms |

- 客户端快照最大响应 22,931 B；原件累计读取 7,087,687 B；每次原件均校验 SHA-256。
- 0 业务写、0 回滚、0 连接失败、0 锁等待（900s 采样窗，1366 事务）。
- 连接池：max 4，结束可用 4；等待上限 64，超时 5s。
- 进程资源：CPU 1.328→259.047s；peak RSS 76,890,112→114,610,176 B。**30 分钟不足以判定长期趋势**。

## 命令与证据

```bash
.venv/Scripts/python.exe tools/capacity/firm_operations.py \
  --config tmp_test/next02-env/runs/4fb7aceea75d4232a2aaeb99c00705c9/pg17-recovery_source.json \
  --major 17 --companies 200 --seconds 1800 \
  --out tmp_test/imp/imp-20261106-a/imp09-capacity-pg17-soak-r2
```

- 报告：`tmp_test/imp/imp-20261106-a/imp09-capacity-pg17-soak-r2/report.json`（`status=passed`，candidate 1927/e6a）。
- PG16 报告：`tmp_test/imp/imp-20261106-a/imp09-capacity-pg16-soak/report.json`（`status=passed`，同一候选）。
- 批量取消/重试：`pytest -q test_batch_operations.py test_dcp04_batch_control.py test_batch_review.py` → **32 passed / 38.75s**。
- 前端预算：`node --test scripts/frontend_budget.test.mjs` → **6 passed / 119ms**；`node scripts/frontend_budget.mjs dist` → passed。

## 诚实限制

- PG16 与 PG17 均通过；PG16 结束 pool 快照 3/4 可用，但进程随后正常退出，不能据此认定泄漏，亦不能证明无泄漏。
- 同机双 worker 不是多节点；无跨节点网络故障、无真实反向代理/HTTP 边缘。
- 未做 100 并发在本候选的过载复测；57bb 的 100 clients/132 queue_full 只作历史诊断，不是本候选验收。
- 未在 soak 中强杀 worker、制造租约到期或排队撤权；这些只由单元/服务测试覆盖。
- 未测 10,000 行流水导入、真实大附件、真实邮件/通知。
- 不承诺单会计 200–500 家；本报告只证明当前本地配置在 200 家合成数据、30 分钟内无失败请求。
