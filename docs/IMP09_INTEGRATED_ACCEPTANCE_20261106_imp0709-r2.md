> 历史阶段记录：本文件的候选与数据只描述当时运行，已由 [最终修补验收](IMP_REPAIR_ACCEPTANCE_20261106.md) 取代。请勿把本文旧容量数据当作当前版本结论；后续候选以 [当前验收索引](CURRENT_ACCEPTANCE.md) 为准。

# IMP09 整合验收（2026-11-06 / imp0709-r2）

状态：**本地部分通过**。PG17/PG16 30 分钟持续负载均 passed；HTTP 50 并发功能可用但延迟不达标（SQLite，p50 13.07s）；真实多节点/生产网络未验证。

## 证据

- PG17 `imp09-pg17-soak-r2`：`status=passed`；source 1933 / `9e87f6f01cab47ecbbcbfc78850c6e0de42ec6fa8e0ca75fed3483a88ce7d3b6`。
  - 批量导出 200 项 / 13.625s，两个 worker 各 100；批量建包 200 项 / 35.556s。
  - 持续读 1801.099s、1446 轮、每类 11568 请求；p50/p95/max：客户 607.16/1165.90/1317.98ms，月包 29.36/589.18/639.45ms，原件 33.90/589.85/635.47ms。
  - 0 写、0 回滚、0 连接失败、0 锁等待；pool 4/4；CPU 1.156→264.891s；RSS 77,316,096→120,320,000B。
  - 报告：`tmp_test/imp-integrated/imp0709-20261106-r2/imp09-pg17-soak-r2/report.json`。
- 前一次 PG17 运行 `imp09-pg17-soak`：数据完整但 `status=failed`，`diagnostic.phase=sustained_reads, class=AssertionError`；原因是运行期间候选从 1932 变为 1933（本轮自己修改了文件），属候选冻结纪律失败，不是产品失败，**不计通过**。
- PG16 30 分钟同候选 `tmp_test/imp-integrated/imp0709-20261106-r2/imp09-pg16-soak/report.json` **passed**：导出 200/13.298s、建包 200/33.152s；持续 1800.625s/1395 轮/每类 11160 请求；p95 客户 1208.84ms、包 610.69ms、原件 611.65ms；0 写/回滚/连接失败/锁等待；pool 4/4；CPU 238.25s、RSS 77.4→115.5MB。
- `test_unified_worker.py + test_ak05_reliability.py` **18 passed**，含“慢后台准备不阻塞助手独立槽”。
- `node --test scripts/frontend_budget.test.mjs` **6 passed/119ms**；`frontend_budget.mjs dist` passed（initial 565,956B/gzip 175,049；home 593,479B/gzip 185,629；CSS 144,373B）。
- HTTP 50 并发（SQLite）`tmp_test/imp-integrated/imp0709-20261106-r2/http/report.json`：100/100 200、0 失败；p50 13066.09ms、p95 17238.12ms、p99 17697.09ms、max 17950.3ms；功能可用但 50 并发延迟不达标，且不是 PG/生产。
- 客户端对照 `http/diag_persistent.py` 复用共享 `httpx` 连接池再跑 50 并发/100 读：100/100 200，p50 10918.47ms、p95 15790.46ms、elapsed 25.96s。复用连接后仍约 11s，说明瓶颈在本地 SQLite/服务端串行，不是每次新建 TCP 客户端；不能用客户端调优冒充达标。

## 09-T 对照

| 编号 | 结果 |
|---|---|
| 09-T1 | 前端预算/闭包/伪 split 负向 6 passed；预算未抬高 |
| 09-T2 | 既有 `OperationsPerformance` 轮询/卸载/换企业取消路径复核，无新增真实缺陷 |
| 09-T3 | PG17 soak 0 写/0 回滚/0 连接失败/0 锁等待；100 并发过载仍为历史诊断，不是本轮通过项 |
| 09-T4 | 两 worker 各 100 项、200 导出/200 包精确；强杀/租约到期未注入 |
| 09-T5 | 授权读取与跨企业 404 有覆盖；排队期间撤权动态交错未做 |
| 09-T6 | PG17 pool 4/4；30 分钟不足以判定长期泄漏 |
| 09-T7 | HTTP 50 并发 100/100 成功但 p50 13.07s、p95 17.24s，延迟目标不达标（SQLite） |

## 诚实限制

- 同机、单 API 进程 + 两个 worker 子进程；不是多节点。
- PG16 同候选已通过；HTTP 50 仅 SQLite 功能验证，延迟不达标；真实多节点/生产未验证。
- 200–500 家真实企业、真实网络、生产资源指标未验证。
