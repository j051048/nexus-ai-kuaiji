# TAXI 批次 H 检查点（2026-11-12）

## 当前候选与归属

- HEAD：`781fd93499dcba7bb76056f0febe19e372202af2`
- 批次 H 当前候选：source 2152 / `0d0aea3a64a1096595733d5eb5f64c331aec74a5fa482a269d151200d8461e11`；migration 377 / `12c7e6ec4d91bc67dbc4a26b4749eda39514e0f0ca20ebf37dd3fb40d200d745`
- 批次 G 历史候选：source 2143 / `882bd439...`；migration 375 / `882f13d4...`；P01 `tmp_test/tax-g/p01-g` passed。
- 工作区：A–H 共 184 项未提交；本批 H 已修改业务代码但未提交。
- P01：`tmp_test/tax-h/p01-h/reports/summary.json` passed；188/165.663s、2/94.641s、4/58.735s；PG16/17 migrations 224，九项 checks true。
- H 文档规范：`docs/TAXI_DEEPSEEK_BATCH_H_20261110.md`

## H0 冻结点（基线，不得事后放宽）

- 固定候选：上述 source 2143，任何 H 代码改动后必须重算候选并重跑受影响门槛。
- 固定测量环境：本机 Windows + WSL2，PG16/PG17 隔离集群 25496–25499；API 单进程和 worker 连接预算以既有 QA05/IMP09 配置为准。
- 固定数据：只使用仓库内合成夹具；不接真实客户、真实模型、真实通知、真实 T+/OA。
- 固定指标口径：
  - 延迟只统计成功请求；拒绝/冲突/超时单列，不混入 p50/p95。
  - 帐务守恒、权限隔离、独立复核、attempt 不重发为每轮硬断言。
  - 未分配共用费用不得计入单车贡献或净利；未知值不得写 0。
- 既有容量基线来源：`test_imp_http_capacity.py`、`test_qa05_capacity_http.py`、`test_qa05_capacity_gate.py`、`test_finance_mixed_capacity.py`、`test_ux11_operations_capacity.py`，以及 `scripts/imp_http_capacity.py`、`scripts/next09_http_capacity.py`。它们是历史候选证据，H2 必须按本批候选复跑或明确失效。

## H1 / TAX24 持续门禁与监控

状态：本地通过。

- `_operations` 五项 + 五个信号；SQLite 13 passed，PG16/17 各 2 passed。
- 浏览器 `e2e/tasks0712.spec.ts` 1365/390 2 passed；截图 `tmp_test/tasks0712-20261107/screenshots/monitor-1365.png`、`monitor-390.png`。
- 未接真实通知渠道；远端 CI 未运行。

## H2 / TAX22 容量

状态：固定基线通过，扩展场景未做。

- HTTP 50 并发 PG17/16 各 250/250 成功；PG17 p50 863.14 / p95 1090.9 / p99 1134.69ms。
- 混合 50 企业/100 请求 errors 0、admission 0；read p50/p95 400.9/419.49ms。
- 未做 100 并发、慢存储、租约/断连、多节点；不能称生产容量通过。

## H3 / TAX19 经营指标

状态：本地/PG/UI 空状态通过。

- `FleetMetricsService` 单车收入/成本/贡献、未分配成本、驾驶员账龄；净利润恒 None。
- SQLite 1 passed；PG17/16 各 4 passed（含 fleet）；浏览器 tax-f 1280/390 2 passed。
- 未做有数据浏览器截图、100 并发和同源重复贡献的独立专项。

## H4 / TAX21 助手与规则记忆

状态：既有只读交付诊断通过，专项准确率未做。

- SQLite `test_assistant_delivery_context.py` 22 passed；浏览器 `assistant-delivery-pages` 14 passed。
- 未做独立 holdout、真实模型、单车成本/欠款问句接入。

## H5 / TAX23 体验精修

状态：自动化可访问性与三引擎关键页通过。

- `qa-accessibility.spec.ts` Chromium/Firefox/WebKit 各 3 passed。
- 缺 IME、真机、人工屏幕阅读器；自动化扫描不等于人工认证。

## H6 / TAX20 本地同步替身

状态：本地服务/HTTP/PG 通过，真实上游未接。

- 迁移 sqlite 153 / postgres 236；HMAC、时间窗、幂等、迟到版本、游标。
- SQLite 4 passed（含 HTTP）；PG17/16 各 4 passed（含签名事件）。
- 无真实文件链接口、断点续传、撤回协议；`external_posting=false`。

## 下一步（按顺序）

1. 完成 H 文档/候选/P01。
2. 进入 I（前端壳层），随后 J/K/L；M 仅在明确授权后。
3. 真实上游、真机、人工评测、远端 CI 与生产发布继续列为外部门槛。
