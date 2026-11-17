# FSC17 验收：资产车辆稳定身份与来源绑定

## 范围与结论

本批先完成“资产—车辆稳定关联、版本/指纹绑定、折旧辅助值与来源引用、撤销保留历史、审核/记账/导出链、恢复”这一可本地验收的纵向切片。

工程状态：**部分通过**。以下能力已接通并验证；FSC17 文档要求中的保险单唯一计提、合同/保单/原件版本绑定、多车保单分配、退保/处置/漏提更正、两期/跨年全链仍未实现，不得把本文件当作 FSC17 整体完成。

## 实现

- 迁移：
  - `nexus_backend/migrations/sqlite/199_fsc17_asset_vehicle_links.sql`
  - `nexus_backend/migrations/postgres/284_fsc17_asset_vehicle_links.sql`
- 表 `periodic_asset_vehicle_links`：
  - `id/firm_id/company_id/book_id/asset_id/vehicle_external_id/source_kind/source_version/source_fingerprint/status active|revoked/linked_by/linked_at/revoked_by/revoked_at/revoke_reason`；
  - 每资产仅一条 active；车辆外部编号+公司 lookup 索引；PG RLS/FORCE/GRANT。
- 服务 `nexus_backend/app/periodic/links.py::AssetVehicleLinkService`：
  - `link`：资产 edit 权限、资产 active、车辆同企业/公司且 active；同车幂等、不同车 409；写审计。
  - `revoke`：只置 revoked、原因、撤销人/时间，不删除历史；重复撤销 404。
  - `summary`/`for_vehicle`：返回车辆名称、主数据版本、指纹、stale reason。
- 路由：
  - `GET/POST /api/periodic/assets/{asset_id}/vehicle-link`
  - `POST /api/periodic/assets/{asset_id}/vehicle-link/revoke`
  - `GET /api/periodic/vehicle-links?book_id=&vehicle_external_id=`
- 折旧：`periodic/service.py::generate_asset` 将车辆外部编号/来源版本/指纹写入分录 `aux`，`source_ref` 尾部追加 `;vehicle:<external>@v<version>`；输入指纹纳入车辆来源版本。
- 前端：
  - `src/AssetVehicleLinkEditor.tsx` 挂在 `FinanceOperationsPanel` 资产卡片；
  - 支持登记/撤销/查看 stale；处理了重复 `JSON.stringify` 导致的 422，以及刷新时旧响应覆盖新关联的竞态（请求序号守卫）。
- 修复的跨层缺陷：
  - 资产草稿输入指纹未纳入车辆关联，导致关联后复核误报“资产来源已变化”；
  - 周期稿 `aux` 未排序，正式记账冻结行比较因键顺序失败；
  - PG `audit_events` 列名、boolean 写入、datetime JSON 序列化问题。

## 验证结果

| 环境 | 命令/范围 | 结果 |
| --- | --- | --- |
| SQLite | `test_fsc17_links.py + test_tax04_recovery.py::test_fsc17...restore` | 3 passed / 12.32s |
| SQLite 回归 | `test_tax09_costs.py + test_fsc17_links.py` | 16 passed / 24.34s |
| 保险来源去重 | `test_same_insurance_policy_and_period_cannot_be_recorded_twice` | 1 passed；同保单同期间第二来源 409 |
| 保险来源去重 PG17 | 隔离 runner 同用例 | 1 passed / 0.81s，`tmp_test/ak05-pg-runs/4177628c681e40ba94ba6b796601f430` |
| 保险来源去重 PG16 | 隔离 runner 同用例 | 1 passed / 0.81s，`tmp_test/ak05-pg-runs/af460548710c4e9a99256df240bf5866` |
| PG17 | 隔离 runner `test_fsc17_links_postgres.py` | 1 passed / 1.14s，`tmp_test/ak05-pg-runs/58da7ae0a848407d9801682df0fe3818` |
| PG16 | 隔离 runner 同文件 | 1 passed / 1.18s，`tmp_test/ak05-pg-runs/8dd998d6df94414b8cdfc243360d3bec` |
| 浏览器 | `e2e/finance-operations.spec.ts` | 4 passed / 43.5s（1365/390） |
| 恢复 | SQLite 全库备份/verify/restore | 3 passed 组合内；关联/指纹/aux/source_ref 保留 |
| 类型/格式 | typecheck / Prettier / ruff | 通过 |

浏览器链路：建车辆主数据 → 资产建卡 → 关联车辆 → 生成折旧草稿（aux 车辆来源）→ 独立复核 → 正式记账 → 科目余额表；另含 1365/390 的错范围/撤权清空负向。

截图：
- `tmp_test/finance-agent/20261107-kernel-reuse/screenshots/maker-1365.png`
- `tmp_test/finance-agent/20261107-kernel-reuse/screenshots/maker-390.png`
- `.../posted-1365.png`、`.../posted-390.png`、`.../revoked-1365.png`、`.../revoked-390.png`

## 候选

- HEAD：`767f7cdf7c717c98d3516a0e31b53bd8cd9ae1a9`
- source：`2358 / 97835ffdadb3588c9bffd0239f77a01761028069f728bb53f10da117818c4d0f`
- migration：`469 / d266a53a73a5a3237cfe6e3676497acc43294ddb6d25466dbf88fb5b5fefb6d7`

## 未完成 / 外部待验

- 已实现 TaxCostService 内“同一保单同一期间第二来源 409”的去重守卫；但保单/合同/原件结构化绑定、TaxCostService 与 PeriodicService 之间的跨引擎互斥、多车保单逐车分配/未分配额仍未实现。
- 退保不终止资产、换车不重置累计、处置后停止计提、漏提补提、尾差更正、两期/跨年全链未实现。
- 政策不支持时阻断、未知历史不补零、锁期/更正/红冲后累计回算未覆盖。
- 两企业浏览器全链、PG 并发重复计提、非空恢复后 hold/解除未覆盖。
- 真实客户车辆/保单/合同、真实原件、T+ 回执、真实政策未提供，全部保持外部待验。
- 未提交、未推送、未部署。
