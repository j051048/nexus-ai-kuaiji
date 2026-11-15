# TAXI 批次 B 检查点：TAX03–TAX04

- 检查时间：2026-11-11 08:06 +08:00
- 当前 HEAD：`781fd93499dcba7bb76056f0febe19e372202af2`（未提交）
- 候选 source：`2087 / 20fee19dc83c8a694bd48e2b0674e084895076b95650c59d765db8e8c86b5a28`
- 候选 migration：`363 / a45387bc4b928cf8964b9cba71e0f3dccc2f165bf09fda716fbf0b14cb9b72bd`
- 工作区：73 项（批次 A+B），未提交、未推送、未部署
- 本轮新增迁移：`140/223`、`141/224`、`142/225`、`143/226`、`144/227`、`145/228`、`146/229`

## 已完成

- TAX03 运输主数据：车辆/驾驶员、稳定外部 ID、历史别名、项目/往来挂接、合同/经营权、有效期、车辆班次；SQLite/HTTP 7 passed，PG16/17 各 2 passed，真实桌面 1 passed/390 截图。
- TAX03 期初承接：复用既有 opening/subledger；新增应收/资产/预收期初回归，`test_opening_service.py` 5 passed；未新建第二套期初。
- TAX04 来源适配：多工作表选择、文件 hash/版本/页签/行号、采用/排除/待核对、金额数量分列、版本幂等与 supersede、来源→输出绑定与冻结保护。
- TAX04 贡献契约：贡献持久化、一源拆多行、多源绑定同一输出版本、按 `output_version` 隔离、贡献聚合 API 与历史冻结保留。
- TAX04 逐行核定：`review_row` + PATCH 路由 + 差异表“改为采用/排除”；仅 manager、活动版本；排除必须原因；写审计并失效未冻结绑定。
- TAX04 局部失败：批量导入 API 对单个坏文件返回逐项失败，成功项独立保留；有服务测试。
- 前端：`TransportMasterPanel`、`TaxSourcePanel`；真实 GoTrue/API，无 route mock；桌面/手机截图与状态断言。
- 类型检查/构建/预算：通过；构建产物与上一候选一致。

## 验证结果（同一候选）

| 层 | 结果 | 证据 |
| --- | --- | --- |
| TAX03 SQLite/HTTP | 7 passed / 13.89s | `tmp_test/tax03.xml` |
| TAX03 PG17 / PG16 | 2 / 2 passed | `tmp_test/ak05-pg-runs/c0147958…`、`ddcdb1a8…` |
| TAX03 浏览器 | 1 passed | `tmp_test/tax03-transport-1280.png`、`-390.png` |
| TAX04 SQLite | 16 passed / 27.26s | `tmp_test/tax04-final.xml` |
| TAX04 PG17 / PG16 | 1 / 1 passed | `tmp_test/ak05-pg-runs/7465b6a1…`、`db088bb0…` |
| TAX04 浏览器 | 2 passed / 24.4s | `tmp_test/tax04-source-1280.png`、`-390.png` |
| 组合（TAX01–04+期初+恢复） | 46 passed / 85.63s | `tmp_test/tax-batch-b-combined.xml` |
| SQLite 全库恢复 | 1 passed / 4.31s | `test_tax04_recovery.py` |
| P01 PG16/17 全量恢复 | passed | `tmp_test/tax-b/batch-b-p01-2087-final3/reports/summary.json` |
| P01 明细 | regression 188/167.761s、joint 2/90.789s、identity 4/61.140s，0 skipped | 九项 checks 全 true；migration_max 229 |

## 未完成 / 明确不在本轮

- TAX04 尚未把贡献自动回写正式底稿行，也没有贡献差异 UI；当前是可复用的来源贡献契约，不是完整消费者闭环。
- PG 全量恢复已通过；新增表的对象级专项恢复断言只在 SQLite 完成，P01 未单独 seed B 批新表。
- 期初承接是既有 opening 服务回归，不是新的运输行业期初台账导入器。
- 客户真实原始台账未提供；本轮只使用合成 xlsx。
- 真实 T+、OA、出管、通知、模型、真人、真机、远端 CI、生产发布均未接入/未验证。
- `test_stage13_migration_workflow.py` 仍有 8 个既存失败，与本批路径无关，未计入通过。

## 环境

- P01 bridge/Auth 转发/WSL 代理已停止；临时 PG 25496–25499 已停止。
- 保留 `tmp_test/tax-b/batch-b-p01-2087-final3`、`tmp_test/tax04-final.xml`、`tmp_test/tax-batch-b-combined.xml` 及私有运行配置作为证据。
- 未触碰其他任务目录、数据库或进程。

## 下一步

1. 若要宣称 TAX04 完整消费者闭环，需补贡献自动回写正式底稿行与差异 UI，并增加 PG 新表 seed/恢复断言。
2. 申请客户脱敏原始文件后，复验真实多工作表、旧月残留、公式/外链/损坏文件路径。
3. 由用户明确授权后再提交、推送或进入 C 批；本轮不执行。
