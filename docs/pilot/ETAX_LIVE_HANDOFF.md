# 电子税务局 / 税企直连接入交接（骨架，Batch 04 未完成）

> 本文件当前只记录**接入所需清单**与**尚未实现的部分**，不代表已具备申报能力。

## 1. 真实接入需要的材料
- 各省电子税务局或服务商（航天信息 / 百望 / 税友等）的接口规范与签名算法；
- 纳税人识别号、CA 证书或数字签名密钥、代理开户授权书；
- 客户税种核定清单（哪些税种、申报周期、是否汇总纳税）；
- 回执报文样例（受理 / 成功 / 失败 / 结果未知）与重试规则。

## 2. 系统内已具备
- `app/tax/engines.py`：增值税、附加税、企业所得税预缴、印花税的确定性计算（整数分）；
- `app/tax/risk_checks.py`：申报前风险分级（block / warn，含 unknown 回执阻断）；
- `app/tax/policy_items.py`：税目政策条目与缺项 409；
- `app/tax/filings.py`：人工登记外部办理结果（append-only）。

## 3. 系统内已具备（本轮新增）
- `app/tax/etax_connector.py`：`Connector` 协议（`submit` / `poll` / `resolve_unknown`）、`build_package()` 确定性导出包（canonical JSON + SHA-256 + manifest 免责声明）、`validate_declaration()` 严格校验（整数分、期间、税种白名单）、`SandboxConnector` 状态机（`unknown` 禁止重报，只能人工核定）。真实连接器只需实现同一协议。

## 4. 系统内尚未具备（接入前必须补）
- 申报状态机 `prepared → approved → submitted → accepted / rejected / unknown → paid / corrected` 与 `unknown` 期间禁止重报的硬约束；
- 受控导出包（JSON/CSV + 内容 SHA-256 + 「未经电子税务局核定，仅作复核底稿」标识）；
- 缴款凭证与申报单的关联表与对账；
- `fetch_input_invoices`（进项发票拉取）与省级报文格式映射；
- 前端「法人 × 四税种 × 期间 × 办理状态」矩阵。

## 5. 结论
在拿到真实授权与回执样例之前，系统只能做**本地确定性测算 + 人工登记**，不得对外宣称「自动申报」。
