# FSC17 检查点（2026-11-17）

## 已完成

- 资产—车辆稳定关联表、active 唯一、撤销历史；PG RLS/FORCE。
- `AssetVehicleLinkService`：关联/撤销/摘要/按车辆查询；企业/公司/车龄版本/指纹校验；同车幂等、异车冲突。
- 折旧草稿写入车辆 aux 与 `source_ref`，输入指纹纳入车辆关联；审核/记账/导出按当前草稿重算。
- 前端资产卡片关联编辑器、撤销、stale 展示；1280/390 浏览器链通过。
- SQLite/PG16/17/恢复验证。

## 证据

- SQLite：`test_fsc17_links.py + test_tax04_recovery.py::test_fsc17...restore` 3 passed/12.32s。
- PG17：`test_fsc17_links_postgres.py` 1 passed/1.14s，`tmp_test/ak05-pg-runs/58da7ae0a848407d9801682df0fe3818`。
- PG16：1 passed/1.18s，`tmp_test/ak05-pg-runs/8dd998d6df94414b8cdfc243360d3bec`。
- 浏览器：`e2e/finance-operations.spec.ts` 4 passed/43.5s。
- 截图：`tmp_test/finance-agent/20261107-kernel-reuse/screenshots/`。

## 未完成

- 保险单/合同/原件绑定、单一保险计提守卫、多车保单分配。
- 退保/处置/漏提/尾差/两期跨年全链。
- 两企业浏览器、PG 并发、恢复后 hold/解除。
- 真实客户数据/外部回执。

## 候选

- source `2358 / 97835ffdadb3588c9bffd0239f77a01761028069f728bb53f10da117818c4d0f`
- migration `469 / d266a53a73a5a3237cfe6e3676497acc43294ddb6d25466dbf88fb5b5fefb6d7`
- 未提交、未推送、未部署。
