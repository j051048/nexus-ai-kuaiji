# 出租车代账批次 D 验收记录（TAX07–TAX09）

日期：2026-11-11
状态：**本地工程部分通过（TAX08 为主）；TAX07/TAX09 仍有明确缺口，不宣称整批完成。**
候选：HEAD `781fd93499dcba7bb76056f0febe19e372202af2`，source 2107 / `0c49f01538051b24118f5ad47c829772d3b14270e4d5332ce2e0ce76fdf4f8ee`，migration 367 / `34d8f5422a53c363a56f541892e6150fcbc693756fff5386d90a44dc0bea2cfc`。

## 结论分级

| 范围 | 本地实现 | 离线验证 | PG16 | PG17 | 浏览器 | 外部验收 |
| --- | --- | --- | --- | --- | --- | --- |
| TAX07 收款/银行核销（既有） | 部分 | 通过（既有） | 通过（既有） | 通过（既有） | 通过（既有 D3 路径） | 未授权 |
| TAX08 T+ 54 列合成适配与回执 | 完成本地合成适配 | 通过 | 通过 | 通过 | 通过 1280/390 | 客户真实模板/导入未验证 |
| TAX09 入口与返回 | 复用既有导航 | 部分 | 不适用 | 不适用 | 通过既有 12 例 | 未授权真机 |

## 已通过

### TAX08（本批新增）

- 从**已复核冻结交付包**生成 54 列合成 CSV，协议 `hengzhang.tplus.synthetic54.v1`，明确标注 `synthetic`、`native_tplus_format=false`，不改写客户原模板。
- 基础 12 列 + 6 组×7 分录列=54 列；外部凭证字固定“记”、内部“转”；外部凭证号 `T54-<code>`；金额用 Decimal 两位小数；文本公式前缀转义。
- 回执固定四列：行号、外部凭证编号、结果、原因；要求连续行号、覆盖全部凭证、拒绝/重复/未知必须给原因。
- 只记录客户文件结果，`external_posting=false`；外发 `dispatch` 仍 409。
- 防篡改：下载时重新渲染并比对 `content_sha256`；artifact/receipt 同码同内容幂等，同码异内容 409。
- 权限：operator 越权、外部事务所查询/下载均被拒绝。

证据：
- `tmp_test/tax-d/d1-d2-combined.xml`：19 passed（D1 结算 8 + D2 适配 4 + D2 API 1 + 既有 integrations 6）/23.89s。
- `nexus_backend/tests/test_tax08_tplus54_postgres.py`：PG17 1 passed/1.75s、PG16 1 passed/1.78s（最终报告目录 `tmp_test/ak05-pg-runs/b3932db9...`、`18eb1420...`）。
- `e2e/stage10-integrations.spec.ts`：1 passed/15.6s；截图 `artifacts/stage10-financial-bridge.png`、`artifacts/stage10-financial-bridge-390.png`。
- 前端 `npm run typecheck` 0；构建成功（TEMP 指向 `tmp_test/esbuild-tmp`）：index JS 565.54 kB/gzip 175.18 kB，CSS 99.72 kB/gzip 20.27 kB。

### TAX07（既有能力回归）

已覆盖部分付款、多银行、合并代收、并发、撤销、跨企业；本批未新增预收/退款/付款人差异实现。
- `nexus_backend/tests/test_stage10_settlements.py` 8 passed/16.71s。
- PG16/17 `test_stage10_settlements_postgres.py` 各 4 passed/9.67s、9.39s。
- C01 `test_c01_remaining_postgres.py -k settlement_direction` 4 passed/11.92s。

### TAX09（既有导航回归）

- `e2e/ux07-09-journey.spec.ts` 12 passed/38.9s：跨客户筛选、服务单返回保留月份/筛选、1280/390、更正入口、企业财务核验。
- D2 的集成工作台可从账套“交付与导出”进入；新增 04 区在 390 下可用。

## 明确未完成 / 不冒充

1. **TAX07 预收、退款、付款人非驾驶员的独立核定代收分配**：未新增业务实现与正/负向测试；现有核销只覆盖已有应收/应付项目的分配。
2. **TAX09 单条“差异→原件→复核→冻结导出→回执”浏览器专项链**：未新做；只有分散的既有导航回归和 D2 集成链。
3. **客户真实 54 列模板、T+ 实际导入/过账**：未获授权、无真实模板，禁止宣称原生 T+ 格式或过账成功。
4. **PG 行级恢复**：P01 只恢复模式与既有种子；D 三张新表在源/目标存在，但目标行数为 0，未做 D2 artifact/receipt 行级目标恢复断言。
5. **外部真机、真实通知、真实模型、远端 CI、生产发布**：均未执行。

## 恢复与迁移

- P01 `tmp_test/tax-c/p01-d/reports/summary.json` **passed**：candidate 2107，regression 188/148.230s、joint 2/86.793s、identity 4/56.902s，0 skipped；PG16/17 均 migrations 219、max schema 231、九项 checks 全 true。
- 源/目标均存在 `accounting.bridge_export_artifacts`、`bridge_export_receipts`、`bridge_export_receipt_rows`；目标无 D 业务行，不能据此宣称 D 文档级恢复演练完成。

## 限制

- 只使用合成数据、本地身份、私有原件存储；未提交、未推送、未部署、未执行线上 SQL、未调用真实模型/通知/T+。
- 本轮未改历史迁移 checksum。
