# P1/P2 执行进度（2026-11-16）

## 本轮完成

### F 租赁：独立复核与发票确认

- `src/TaxFPanel.tsx` 租赁计划行新增“复核租赁凭证”，打开同屏复核抽屉（`focus_draft_id`）。
- 新增发票号输入和“确认本期发票”；业务、收款、发票三条事实分别保存并展示。
- `nexus_backend/app/learning/service.py` 对 `lease:` 合成来源跳过普通分类学习，修复复核时“审核样例业务归属无效”409。
- 验证：`test_tax13_leases.py` 2 passed/6.05s；学习回归 21 passed/31.40s；浏览器 1280/390 通过；截图 `tmp_test/tax-f-lease-review-1280.png`。

### HTTP 100 并发容量边界

- `scripts/imp_http_capacity.py` 增加 `--concurrency/--companies`。
- PG17：100 并发、100 企业、400 读/100 写。
- 结果：500/500 成功，p50 3258.97ms、p95 4158.61ms、p99 4354.55ms、max 4569.68ms；CPU 13.141s、peak RSS 351,604,736 bytes。
- 定位：默认 pool=4 时 connection 等待 p95 629ms、max 740ms，HTTP p95 4.16s，说明排队在连接池。
- 调整 pool=8：p95 2273.87ms；pool=12：p95 **1617.84ms**（PG17）、**1709.79ms**（PG16），500/500 成功。
- 结论：100 并发达到本地 p95<2s 目标需要每 API 进程 12 个连接；两进程预算 24，不盲目扩池。报告 `tmp_test/p1-http-100-pool12/summary.json`、`tmp_test/p1-http-100-pg16/summary.json`。
- 未做：数据库侧 max_connections/锁等待预算、多节点、慢存储、长稳。

### 案例创建 NameError 修复

- 上传绑定 6 条 HTTP 用例因 `case_tasks` 旧 OA 引用残留 `oa_snapshot` 未定义而 500；`nexus_backend/app/cases/service.py` 删除该无效校验。
- 修复后 `test_upload_binding_http.py` **6 passed/15.92s**；`test_stage8_cases.py + test_business_api.py` **18 passed/34.62s**。
- `tests/test_business_api.py` 增加旧 Starlette 无 `TestClient(client=...)` 时的 loopback middleware 兼容；测试环境兼容，不改业务断言。

## 仍未完成

- F：租赁正式过账/导出、发票管理/红冲、合同变更旧审批失效、税费申报/缴款、利润 T+ 结转去重。
- E 车辆成本、G3 联合验收、D 收款预收/退款、H 100 并发瓶颈定位、K OA 待办绑定、L 真机。
- P2 看板增强、FDE 认证、M、CI/部署。
- P0-5 真实 T+ 模板认证仍为外部门槛。

## 候选

- HEAD `9517ddb4`；source 2208/`5d29c7fed29e916c9bc4e7b5cbd99512186b91cf1180db147efd8b6204075e26`；migration 388。
- 未重跑 P01；未提交/推送/部署。

## 更新候选

- HEAD `9517ddb4`；source 2208/`059520aa0e2f2a058ec192afda1613a47b9105cb39eb0141f4ef3ddc6e241ecc`；migration 388。
- 未重跑 P01；未提交/推送/部署。

## 2026-11-16 追加

- F 租赁：复核、发票确认、改版门禁、版本记录、CSV 导出；SQLite 3 passed，浏览器 1280/390 通过。
- 100 并发：pool4 p95 4158.61ms；pool8 2273.87ms；pool12 PG17 1617.84ms、PG16 1709.79ms，500/500。
- K05：员工只读 OA 材料请求；后端 4 passed，浏览器 1 passed。
- J：自由列宽/键盘/重置；516 行 6 passed。
- 运营看板：财务趋势、企业对比环比、240 账套 p50 32.0/p95 62.3ms。
- 候选：HEAD `9517ddb4`；source 2211/`adf92aa0…`；migration 388/`0f667fde…`。

## 2026-11-16 追加：F 租赁改版门禁

- 改版后 `recognize` 409，必须确认当前版本；旧审批凭证导出服务端 409，UI 同步禁用导出。
- 版本记录显示改版/版本确认；后端 events 断言。
- 验证：`test_tax13_leases.py` 4 passed/9.91s；`tax-f` 1280/390 各 1 passed。
- 候选：source 2211/`a676751273245fa383939d2f2e9bbc0502d3830e2b3ac55b5e68749031ca62e5`，migration 388。
- 未完成：合同改版通知、发票红冲、税费申报/缴款、利润 T+ 结转去重。

## K05 员工补件收口

- 员工侧只读补件请求已通过：后端 4 passed、浏览器 1 passed。
- 不使用假 `case_tasks` 来源；员工页材料请求列表承担待办展示，管理侧仍可生成/查看请求。
- 仍缺真实 OA 接入与真机验证。

## F2 税费办理证据复核

- 既有 filings 分层/追加历史/附件/权限通过：23 passed/13.38s。
- F2 本地口径可视为已具备，真实税务回执仍为外部门槛。

> 后续未完成项与恢复顺序已整理到 `docs/TAXI_FOLLOWUP_DEVELOPMENT_PLAN_20261116.md`。
