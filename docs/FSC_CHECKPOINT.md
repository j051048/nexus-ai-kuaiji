# FSC 检查点

- 时间：2026-11-16
- HEAD：`767f7cdf7c717c98d3516a0e31b53bd8cd9ae1a9`
- 当前批次：FSC02（本地/浏览器/PG16/PG17 部分通过）
- 工作区：FSC01–FSC03 与 FSC02 退款/多银行相关文件未提交；其余历史批次已由其他智能体提交到 HEAD，未回退。
- 候选：source `2234 / 36ead9d5…`，migration `403 / 529cdf2c…`。
- 已运行：
  - `pytest tests/test_tax06_fees.py tests/test_tax04_sources.py -q` → 36 passed/72.03s
  - `pytest tests/test_tax06_fees.py -q` → 20 passed/45.86s
  - Playwright tax06-fees 1280/390 → 2 passed/45.8s
  - PG16/17 `test_tax06_fees_postgres.py` → 各 6 passed/9.37s、8.77s
  - `npm run typecheck` 0
- 未通过：同候选 P01；逐行回执/代收独立依据。
- 下一步：
  1. 补逐行回执/代收独立依据，再跑同候选 FSC02 P01。
  2. 进入 FSC04；FSC04 不得在 FSC02 缺口未记录时宣布整体通过。
- 外部门槛：客户五行业档案确认、真实工时/质量/业务量、5 人运营验证、真实 T+/OA/HIS/银行/税务。

## 2026-11-16 增补

- PG 双会话核销并发已通过（PG16/17 各 7 passed）；SQLite 恢复覆盖应收/分配/退款/来源原件。
- 仍未做：逐行回执、代收独立依据、同候选 P01。
- 候选 source 2236/`258af313…`，migration 404/`8fd6eaab…`。


## 2026-11-16 FSC04 检查点

- 行业契约/共享导入信封/五企业能力矩阵/广告收入草稿完成；SQLite 8 passed、PG16/17 契约+导入+广告各 4 passed、浏览器 6 passed。
- 未完成：导入前端操作、profile 细分能力、项目合同/预付/毛利、配置失效联动、PG 并发/恢复、客户真实契约。
- 当时候选 source 2254/`e376ce06…`、migration 412/`897af48b…`。
- 详见 `docs/FSC04_CHECKPOINT_20261116.md`。


## 2026-11-17 FSC05 与迁移 173

- FSC05 证据政策核心/只读面板完成；11 passed、PG16/17 各 1 passed、浏览器 6 passed。
- 修复已应用 SQLite 162 的复合外键列序错误：恢复 162，新增 173 重建 `fsc_company_profiles`，保留数据/索引/触发器；FSC01 测试夹具补 `commit`。
- 候选 source 2264/`9bc2365e…`、migration 417/`680ee380…`。
- 未完成：FSC05 补件往返/五行业反例；FSC06–FSC12。


## 2026-11-17 FSC13

- 外部结转证据 sqlite 194/postgres 279：只核对、不生成第二张凭证、不贡献利润表。
- 权威模式 internal/tplus_voucher/hybrid；无 confirmed profile 拒绝登记；外部模式拒绝内部结转；模式变化使旧草稿复核 409。
- SQLite 5 passed/15.25s，tax02+FSC13 13 passed/36.95s，组合含恢复 18 passed/47.79s；PG16/17 各 1 passed；浏览器 2 passed；typecheck 0。
- 候选 source 2336/`b8b7b450…`、migration 459/`7543a5d9…`。
- 未完成：真实 T+ 回执、hybrid 自动并入、全量联合回归/FSC12、真实客户；FSC14–FSC17 未开始。


## 2026-11-17 FSC14

- 合同版本绑定 sqlite 195/postgres 280；审核/记账/导出统一守卫；旧 v1 草稿在 v2 获批后审核、记账、导出均 409。
- PG 修复 `amend` datetime 指纹与 `lease_plan_rows` DELETE 授权（前向 281/196）。
- 浏览器链：合同→业务/收款/发票→收入草稿→独立复核→导出→红冲→改版拒绝；修复重复动作导致请求发两次、创建后面板持续 busy。
- SQLite 13 passed/32.24s；PG17 stale 1 passed/1.34s、lease 3 passed；PG16 stale 1 passed/1.24s、lease 3 passed；浏览器 2 passed/1.5m；typecheck 0。
- 候选 source 2341/`50398a53…`、migration 463/`f86c7407…`。
- 未完成：外部回执/归档包、两企业跨期浏览器链、PG 竞争/恢复专项、真实 T+/客户；FSC14 为部分完成。


## 2026-11-17 FSC15

- 费率卡版本化：draft→confirmed→active→superseded/inactive；独立确认、生效/续期/停用、历史/影响。
- 重叠 active 409；同逻辑新版本须从旧结束日之后开始；停用/版本变化使来源复核 409。
- 迁移 197/282；PG `tax_included` 布尔修复；前端 loadSequence 防旧响应覆盖新状态。
- SQLite 20 passed/37.65s；PG16/17 各 3 passed；浏览器 2 passed/38.5s；恢复 1 passed/7.54s。
- 候选 source 2346/`49a5d987…`、migration 465/`1776b62a…`。
- 未完成：两企业 PG 隔离、真实客户费率、FSC16 结算冲回联动；FSC15 为部分通过。


## 2026-11-17 FSC16

- 包干合同版本化：draft→confirmed→active；独立确认；额度按合同累计。
- 额度占用/释放保留历史；超额/超释放 409；幂等重放；车辆范围/跨账套/跨事务所拒绝。
- PG JSONB 兼容修复：`vehicle_external_ids_json` 返回 list 时不再 `json.loads`。
- 并发：两线程同时占用 6000/10000，仅一笔成功，余一笔 409；最终 6000 已占用。
- SQLite 15 passed/24.35s；PG16/17 各 4 passed；浏览器 2 passed/38.0s；恢复 1 passed/7.44s；typecheck 0。
- 候选 source 2352/`7817238a…`、migration 467/`50fb3cc3…`。
- 未完成：真实客户合同/工单/T+ 归档、两企业浏览器/跨年全链、FSC17/FSC12。


## 2026-11-17 FSC17（部分通过）

- 新增 `periodic_asset_vehicle_links`（sqlite 199/postgres 284）：稳定车辆外部身份+主数据版本/指纹，active 唯一资产，撤销保留历史。
- 折旧草稿 aux 写 vehicle_external_id/source_version/source_fingerprint，source_ref 可回车辆；审核/记账/导出使用当前草稿源。
- 前端资产卡片关联/撤销；修复双重 JSON stringify、旧请求覆盖、资产指纹缺车辆、aux 键顺序。
- 证据：SQLite 3 passed/12.32s；PG16 1 passed/1.18s（`8dd998d6…`）；PG17 1 passed/1.14s（`58da7ae0…`）；浏览器 4 passed/43.5s；恢复通过。
- 候选 source 2358/`05e39195…`、migration 469/`d266a53a…`。
- 未完成：保险单/合同/原件绑定、单一保险计提守卫、多车保单分配、退保/处置/漏提/尾差、两期跨年、两企业浏览器、PG 并发、恢复 hold/解除。

## 2026-11-17 FSC12 容量探测（失败，未冒充通过）

- 隔离 SQLite API：`ACCOUNTING_QUEUE_EMBEDDED_WORKER=0`，单 uvicorn 进程，`tmp_test/fsc12-capacity/isolated.db`。
- 50 并发读 200 次（UI summaries=true）：p50 13,264.29ms、p95 16,479.05ms；写 50 次 p95 2,711.77ms。
- 100 并发读 300 次（UI summaries=true）：p50 27,278.28ms、p95 29,082.85ms；写 100 次 p95 3,420.09ms。
- 全部请求 200/201，无错误；但延迟远高于 QA05 PG 基线（100 并发 p95≈1.6s）。
- 单请求 `get_book` 约 75ms，健康 CTE 是主要成本；这是单进程 SQLite 探测，不证明多 API/PG/多节点容量。
- 报告：`tmp_test/fsc12-capacity/ui-summary-50.json`、`ui-summary-100.json`；API 已停止。
- 结论：FSC12 容量门槛未通过；需优化健康读模型或改 PG/多进程后再测，不得将本结果写成通过。

- 已尝试 `AccessControl.book` 管理角色快路径（减少 effective_book_access/company_memberships 联表）；浏览器 4 passed、访问回归 14 passed，但单请求仍约 32ms、容量 p95 无实质改善，未解决 SQLite 每连接首次查询/读模型成本。

## 2026-11-17 FSC12 容量二次优化（仍失败）

- 新增 opt-in `ACCOUNTING_SQLITE_READ_REUSE=1`：每线程复用只读 SQLite 连接，事务提交/回滚后归还，避免每请求首次查询的 schema/authorizer 编译成本；默认关闭。
- `AccessControl.book` 恢复单条查询，用 `firm_review_controls` LEFT JOIN 读取分离策略，避免额外查询；访问边界测试 15 passed、`test_business.py` 34 passed，合计 49 passed/63.72s。
- 50 并发 200 读/50 写：读 p50 588.94ms、p95 4,716.32ms；写 p95 2,651.31ms。
- 100 并发 300 读/100 写：读 p50 1,175.77ms、p95 5,418.26ms；写 p95 3,805.74ms。
- 报告：`tmp_test/fsc12-capacity/reuse-final-50.json`、`reuse-onequery-100.json`。
- 结论：读 p95 从 16.5s/29.1s 降至 4.7s/5.4s，但仍未达 QA05 PG 基线；单进程 SQLite 结果不作为生产容量通过。
- API 已停止，18007 无监听。
- 4 worker 同端口尝试无效：20 个读成功、280 个 401；会话只在签发 worker 的内存中，未配置共享会话/粘性路由，不能作为多实例容量数据。
