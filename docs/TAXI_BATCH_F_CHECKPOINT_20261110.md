# TAXI 批次 F 检查点（2026-11-12）

## 当前状态

- 批次：F / TAX13–TAX15。
- 候选：HEAD `781fd93499dcba7bb76056f0febe19e372202af2`，source **2135 / `a7ce3d85d649c07d51f4fb14f544d0ae41a9cc59ff6b9516b1132ea5dc9b12c7`**，migration **373 / `38a3cc409ca8dfb9df59996b72ca4e7838e157c38e1a1d1495724cf820b13725`**。
- 工程状态：本地主链通过；恢复仅 SQLite；P01 未对本候选运行。
- 外部验收：不适用/未授权。真实客户合同、税局申报、T+ 导入、真机未验证。
- 工作区：141 项未提交；未提交、未推送、未部署。

## 已完成

### TAX13

- `leases.py`、sqlite 151 / postgres 234。
- 合同、逐月计划、业务/收款/发票确认、收入草稿、退款、改版、终止。
- 预收按可用余额释放；同一预收不跨期重复；确认事实与收入分离。
- 2 SQLite + PG 用例 + 1280/390 浏览器。

### TAX14

- `basis.py`。
- 政策版本、税率/附加、基数、正负调整、人工证据、来源引用、独立复核。
- 2 SQLite + PG 用例 + 浏览器生成/复核。

### TAX15

- `profit.py`。
- 映射版本、已过账凭证行归集、利润项目、凭证/来源下钻、未知所得税/净利润为 null。
- 2 SQLite + PG 用例 + 浏览器展示/下钻。

### 恢复

- `test_tax_f_recovery.py` 1 passed：租赁/税费/映射在 SQLite 全库备份恢复后保留。

## 测试证据

- SQLite/HTTP：`tmp_test/tax-f/tax-f-combined.xml`，7 passed / 16.25s。
- PG17：`tmp_test/ak05-pg-runs/661897ca08cb4a7fb27ab2379efd0eee`，3 passed / 1.49s。
- PG16：`tmp_test/ak05-pg-runs/7370b47722da414d894fb87ccaf9de7f`，3 passed / 1.69s。
- 浏览器：`npx playwright test --config playwright.tax-f.config.ts`，2 passed / 21.8s。
- 截图：`tmp_test/tax-f-1280.png`、`tmp_test/tax-f-390.png`。
- 构建：typecheck 0；index 565.54 kB / gzip 175.18 kB；TaxWorkpaperPanel 70.55 kB / gzip 21.38 kB。

## 已知缺陷/下一步

1. 租赁收入草稿未在浏览器走独立复核、过账和导出；`approved_version` 仍未落审批状态。
2. 税费未接申报/缴款/官方回执；政策未与 `tax_profile` 强制一致。
3. 利润未做 T+ 结转去重、四层完整下钻、年累计和真实客户算术核回；混合凭证标记近似。
4. 恢复只有 SQLite；PG 目标行级恢复未验证。
5. 需冻结最终候选后跑 P01；如在此之后改代码，必须重算并重跑。

## 环境

- PG16/17 25496–25499 在测试中使用；P01 bridge 未启动。
- 未执行任何提交、推送、部署、线上 SQL、真实模型/通知/OA/T+ 调用。
