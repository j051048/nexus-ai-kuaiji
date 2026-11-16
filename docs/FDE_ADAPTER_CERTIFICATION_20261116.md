# FDE 适配器本地契约认证（2026-11-16）

## 定位

这是**本地合成契约认证**，不是供应商认证，也不是客户接口已接通。

`GET /api/firm/fde/adapters?company_id=` 现在为每个适配器返回：

- `contract_version`：当前 `v1`。
- `contract_sha256`：由 code/label/live_external/native_format/contract_version 规范化 JSON 计算。
- `certification`：`local_contract_only`。
- `catalog_fingerprint`：全部适配器 code + contract_sha256 的稳定指纹。

适配器仍全部 `live_external=false`；`tplus54_synthetic.native_format=false`。该接口不授予写入、映射或过账权限。

## 覆盖适配器

- tax_xlsx
- transport_master
- oa_snapshot
- lease_contract
- tplus54_synthetic
- sync_stub

## 验证

`cd nexus_backend && python -m pytest tests/test_fde_adapters.py -q` → **1 passed/6.28s**。

断言：6 个适配器 code、fingerprint 64 位、每项 contract_sha256 64 位、重复请求稳定、T+ 非原生格式、全部非真实外部、员工 403/404。

## 未完成

- 客户真实模板/供应商版本认证。
- 适配器签名密钥、版本迁移/撤销、运行时契约探测。
- 真实 T+ 接通与回执验证。

## UI 视觉证据补充（2026-11-16）

- 修复 `tasks0712` 截图时机：重载后等待“运行可观测性”标题与“FDE 适配器契约”区域，再截图。
- `e2e/tasks0712.spec.ts` 6 passed/39.7s；截图 `tmp_test/tasks0712-20261107/screenshots/monitor-1365.png`、`monitor-390.png` 显示六类契约、`local_contract_only`、未接真实外部与非原生 T+。
- 最终候选：HEAD `9517ddb4`；source 2211/`f68f8abd3668fe8c11a0a679f3d3bd6b63bc91d0826428a7ce893df2d9b3493e`；migration 388。
