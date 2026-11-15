# 批次 H 验收记录（TAX19–TAX24）

状态：**部分通过（本地工程、PG16/17、Chromium/Firefox/WebKit 关键路径）**。真实上游、真机、人工屏幕阅读器与生产容量未验证。

## 候选

- HEAD：`781fd93499dcba7bb76056f0febe19e372202af2`
- source：2152 / `0d0aea3a64a1096595733d5eb5f64c331aec74a5fa482a269d151200d8461e11`
- migration：377 / `12c7e6ec4d91bc67dbc4a26b4749eda39514e0f0ca20ebf37dd3fb40d200d745`
- 工作区未提交；文档不参与 source 指纹。
- P01：`tmp_test/tax-h/p01-h/reports/summary.json` **passed**；run `379caa1dfbdb462799f39f1e5447b1df`；regression 188/165.663s、joint 2/94.641s、identity 4/58.735s；PG16/17 migrations 224、九项 checks 全 true。

## H0 基线

`docs/TAXI_BATCH_H_CHECKPOINT_20261110.md` 固定了机器、PG 端口、合成数据、成功/拒绝分列与“不得把未分配费用算净利”的口径。

## H1 持续门禁 / TAX24

- `FirmObservabilityService._operations` 返回资料、待复核、拒绝行、通知 unknown、最长队列年龄。
- 信号：material_backlog、review_backlog、source_rejections、notification_unknown、queue_age；全部 `automatic_action=false`。
- SQLite `test_firm_observability.py` 通过；PG16/17 worker-health 各 2 passed。
- 浏览器 `e2e/tasks0712.spec.ts` 1365/390 各 1 passed；截图 `tmp_test/tasks0712-20261107/screenshots/monitor-1365.png`、`monitor-390.png`。
- 限制：CI 托管远端未运行；告警阈值仍是只读建议，未接真实通知渠道。

## H2 容量 / TAX22

- HTTP 50 并发：PG17/16 各 250/250 成功；PG17 p50 863.14 / p95 1090.9 / p99 1134.69 ms，PG16 p50 866.28 / p95 1092.85 / p99 1120.83 ms；pool 4。
- 混合服务：50 企业/100 请求，errors 0、admission_rejections 0；read p50/p95 400.9/419.49 ms；pool 4、max_waiting 64。
- 报告 `tmp_test/h2-http-pg17`、`tmp_test/h2-http-pg16`、`tmp_test/h2-mixed-pg17/report.json`。
- 未做：100 并发曲线、慢存储、租约/断连、同机多进程 vs 多节点、54 列大导出/下钻/大原件下载。不得称生产容量通过。

## H3 经营指标 / TAX19

- `FleetMetricsService`： reviewed 规费 + recognized 租赁确认收入；reviewed 维修/保险/OA 批次按车辆归属成本；未匹配成本进 `unallocated_cost_cents`；净利润恒 `None` 并给出原因。
- 驾驶员应收来自 1122 结算项，无明细时 `aging_known=false`、`outstanding=None`，不补 0。
- SQLite `test_h3_fleet.py` 1 passed；PG17/16 `test_tax06_fees_postgres.py` 各 4 passed（含 fleet）。
- UI `FleetMetricsPanel` 挂载到税务工作台利润段；`e2e/tax-f.spec.ts` 1280/390 各 1 passed（空状态 + 未知净利润提示）。
- 限制：浏览器仅验证空状态；有数据的车辆/账龄表由 SQLite/PG 断言，未做专用有数据浏览器截图。

## H4 助手 / TAX21

复用既有只读交付诊断，不新增写账能力。

- SQLite `test_assistant_delivery_context.py` **22 passed**：交付阻断、负责人、下一步、计划期限、撤权/陈旧的证据指纹。
- 浏览器 `e2e/assistant-delivery-pages.spec.ts` **14 passed**：1440/390 的 none/stale/revoked/wrong-scope/late/mfa/server 页面。
- 限制：未做独立 holdout 准确率；单车成本/欠款问句未接成新的只读工具；真实模型未调用。

## H5 体验 / TAX23

- `e2e/qa-accessibility.spec.ts` Chromium、Firefox、WebKit 各 3 passed（employee/finance/accountant 首屏与键盘可达）。
- 既有 `stage17-monthly` 六阶段、`workbench-focus`、`tax-f` 1280/390 均通过。
- 限制：未做 IME、真实触屏、人工屏幕阅读器、Safari 真机；自动化无障碍扫描不等于人工认证。

## H6 同步替身 / TAX20

- 新增本地签名替身 `app/integrations/sync_stub.py`，无外部网络。
- HMAC 签名 + 300 秒时间窗；同事件同版本同内容幂等、异内容 409；迟到旧版本不覆盖；新版本把旧行标 superseded；游标分页。
- SQLite `test_h6_sync_stub.py` 4 passed（含真实 HTTP 路由）；PG17/16 `test_tax_f_postgres.py` 各 4 passed（含签名事件/游标）。
- 限制：没有真实文件链接口、分页游标回退、断点续传、撤回/更正语义的完整协议；`external_posting=false`，不得称上游已过账。

## 结论

H1/H3/H4/H6 本地路径通过；H2 仅限固定并发基线；H5 仅自动化与三引擎关键页。真实上游、真机、人工评测、远端 CI 与生产发布均未验证。
