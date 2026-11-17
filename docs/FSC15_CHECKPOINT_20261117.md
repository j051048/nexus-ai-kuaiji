# FSC15 检查点（2026-11-17）

## 本批实际完成

- 费率卡从“直接 active”升级为版本化生命周期：draft → confirmed → active → superseded/inactive。
- 旧 `add` 接口保留兼容：直接 active v1，审计 `tax.rate.legacy_active`，不破坏既有期初/费率测试。
- 独立确认：创建人不能确认自己的草稿；生效、续期、停用均校验 expected_version。
- 同车型生效期间重叠 409；同一逻辑卡新版本必须从旧版本结束日之后开始。
- 历史版本保留，不覆盖旧行；停用后 `resolve` 不再返回，来源复核/记账按 `rate_version` 重新校验。
- 前端补齐草稿/确认/生效/续期/停用/影响；修复旧列表响应覆盖新状态的竞态。
- 迁移 197/282；PG 布尔列 `tax_included` 使用 `bool()`。
- 恢复用例覆盖 status/version/logical_id/fingerprint/confirmed/activated。

## 证据

- 验收：`docs/FSC15_ACCEPTANCE_20261117.md`
- SQLite：20 passed/37.65s
- PG17：3 passed/1.61s `tmp_test/ak05-pg-runs/4e247e0a9a8a4252b04d4f3b741ac764`
- PG16：3 passed/1.58s `tmp_test/ak05-pg-runs/697f821c687f4ca3af7f8cfe8777736d`
- 浏览器：2 passed/38.5s；`tmp_test/fsc15-rates-1280.png`、`tmp_test/fsc15-rates-390.png`
- 恢复：1 passed/7.54s
- 候选：HEAD `767f7cdf7c717c98d3516a0e31b53bd8cd9ae1a9`；source `2346 / 49a5d9878c1fd1fdda313ff893c5d9b91a1be4fa9ab92e8be68e2397b08efcba`；migration `465 / 1776b62a44345a74743485eeb6c1dd55343084eac2f27e7071d54988a6027d5f`

## 未完成

- 两企业 PG 隔离链。
- 真实客户费率合同。
- FSC16 包干维修结算冲回联动。

## 边界声明

- 未提交、未推送、未部署、未执行线上迁移、未接真实模型/通知/OA/T+。
- 本地虚构数据与合成费率；不代表真实客户费率生效。
