# N04 税务底稿前后端契约

本次只复用 N02 的计算、独立复核和 CSV 规则，并新增一个只读准备接口。未改变税率、核算、迁移、权限策略；未调用真实模型、通知或线上服务。

## 路由与权限

| 路由 | 权限 | 请求与返回 |
| --- | --- | --- |
| `GET /api/tax/context` | 当前账套 read | `book_id`、可选 `period`；返回下述 scope、permissions、profile、accounts、blocking_reasons |
| `GET /api/tax/profile` | read | `book_id`、可选 `period`；返回 `{profile: object|null}` |
| `PUT /api/tax/profile` | manage | `book_id,taxpayer_type,vat_rate_bp,surtax_rate_bp,filing_cycle,effective_from,reason`；返回原始 profile 对象 |
| `POST /api/tax/workpapers` | edit | `book_id,period,reason,vat_accounts`；201 返回原始底稿对象 |
| `GET /api/tax/workpapers` | read | `book_id,period`；返回 `{workpapers:[...]}` |
| `GET /api/tax/workpapers/{id}` | read | `book_id`；返回原始底稿对象 |
| `POST /api/tax/workpapers/{id}/review` | review | `book_id,expected_version,reason`；返回复核后底稿；生成者与复核者必须不同 |
| `GET /api/tax/workpapers/{id}/csv` | export | `book_id,expected_version,source_digest`；只导出最新、来源仍有效且已独立复核的底稿 |

权限来自服务端当前成员授权。没有账套可见权限返回 404；只读不推导成可复核。权限、来源、版本在执行操作时再次核验，准备接口并不授权后续操作。

## 只读准备接口

```json
{
  "scope": {"firm_id":"…","company_id":"…","book_id":"…","period":"2026-10","book_name":"…"},
  "permissions": {"edit":true,"review":true,"export":true,"manage":false},
  "profile": null,
  "accounts": [{"account_id":"…","account_version":1,"code":"1002","name":"银行存款"}],
  "blocking_reasons": ["缺少2026-10生效的纳税人档案，请先登记税务档案"]
}
```

- 所有数据在同一个当前授权读事务中取得。显式 `period` 必须等于所选月份账套；省略时使用服务端账套月份。
- `accounts` 是本期正式 posted 凭证实际发生的全部 `(account_id,account_version)`，按冻结标签展示，净发生额为零也保留。当前科目已升版不能把历史凭证改成新版本。
- 账户不预选税务角色。用户必须逐项明确 `vat_payable` 或 `excluded`；生成时服务端再次核验精确覆盖。超过 1,000 个发生版本受控拒绝，不截断。
- 未使用 `/api/opening/accounts`：该接口需要 manage 且只有当前科目版本，不能承载已有正式凭证的历史版本映射。
- `blocking_reasons` 复用既有底稿期间、档案和来源校验，包含闭期、未支持纳税人/周期、缺发票或不支持的来源。准备检查的内部排除探针不保存映射、不返回金额或最终来源摘要，不能当作会计确认。
- 未新增“冻结正式期初才可生成”规则：沿用 N02 的实际约束，不在展示层凭空增加核算契约。

## 已有支持范围与事实绑定

当前生成支持 `small_scale`、`monthly`；`general` 或 `quarterly` 可登记档案但不得生成底稿。红字、作废、重复、跨期及坏形状发票行保持阻断。税率使用严格整数 bp，服务层接受 0–10,000；前端不得以空串、布尔或浮点冒充整数，也不得自动推断税率或已申报。

底稿金额输出为十进制字符串；账面金额与差异可为负数。`source_snapshot` 是 `n02-tax-v2` 冻结证据，包含 scope、档案、发票和正式凭证来源。`source_digest` 绑定整个快照。所有底稿都保持 `filing: "not_filed"`。

**列表和详情只显示冻结底稿，不返回 source_stale/current/can_review 标志。** `status=reviewed` 不证明来源至今有效。复核/导出会重新检查最新底稿、当前档案及来源指纹；409 后清除可操作态并让用户重新加载确认，不自动复核或导出。闭期、旧稿、旧版本、被改动来源均继续拒绝。

CSV 返回 `X-Content-Sha256`、`X-Workpaper-Version`、`X-Source-Sha256` 以及 `Cache-Control: no-store`。下载请求必须携带当前已展示对象的版本与摘要，范围变化或响应身份不符时不得下载。

## 共享真实 HTTP 样本与离线验证

`nexus_backend/tests/fixtures/n04_tax_contract.json` 来自隔离 SQLite 虚构企业，使用真实期初冻结、独立复核、正式凭证、底稿生成/复核/导出服务。包含 `context/profile_response/create_request/draft/reviewed/list_response/review_request/export_query`。ID 与时间属于固定样本，不能用于业务；SHA-256 与内部来源绑定是真实计算值。

`test_n04_tax_contract.py` 在每次测试中重新生成真实 HTTP 响应并比较键/类型结构、金额、状态及样本指纹，不靠手写 mock 冒充后端合同。其他测试覆盖读权限、撤权、跨企业、错月份、发生额归零、历史科目版本和不支持来源。

新增接口前，接口用例为 8 failed / 2 passed（两个负向 404 在缺路由时也通过）；实现后首批 10 passed。共享样本文件缺失曾使新契约测试失败，生成样本后复验。

- 税务 API / N02 相邻 7 个文件组合：80 passed，187.38 秒（包含当时的 11 个 N04 用例）。
- 最后追加“当期使用旧科目版本”后，完整 N04 专测：12 passed，28.40 秒。
- 两次存在同一条既有 Starlette TestClient `httpx` 弃用警告；Ruff format/check 通过。
- 原始证据分别为 `tmp_test/n04-tax-adjacent-green.xml` 和 `tmp_test/n04-tax-context-final.xml`，不要相加为独立用例数。测试只使用隔离 SQLite / 虚构 HTTP 身份，不宣称真实登录、PG 或生产已验证。

```powershell
.venv/Scripts/python.exe scripts/test_accounting_offline.py `
  nexus_backend/tests/test_n04_tax_contract.py `
  nexus_backend/tests/test_tax_api.py nexus_backend/tests/test_tax_workpaper.py `
  nexus_backend/tests/test_n02_source_contract.py `
  nexus_backend/tests/test_n02_voucher_snapshot.py `
  nexus_backend/tests/test_n02_snapshot_consistency.py `
  nexus_backend/tests/test_n02_input_boundaries.py -q
```
