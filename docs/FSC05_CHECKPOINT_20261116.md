# FSC05 检查点：分动作证据政策（部分通过）

日期：2026-11-17
状态：本地核心与补件建单通过；财务验收→重查→回原队列、五行业反例仍未通过。

## 已完成

- sqlite 172/postgres 258：`fsc_evidence_policies`（企业/行业/版本/状态、要求/例外 JSON、指纹、确认/生效字段）+ `fsc_evidence_policy_events`；PG JSONB、RLS、单一生效索引；确认后内容不可修改。
- `nexus_backend/app/fsc/evidence.py`：
  - 要求项按 `action`、`business_type`、`min_amount_cents`、`max_age_days` 适用；动作限定收件/草稿/暂估/收入确认/付款准备/独立复核/月结/导出。
  - 合法例外需原因≥5字、独立审批人、可选到期日/金额上限；例外不放宽独立复核和正式记账。
  - `evaluate` 返回 `allowed`、`policy_version`、`policy_fingerprint`、`blockers`（missing/pending/expired/conflict）和 `exceptions_applied`；无生效政策一律阻断。
  - 创建/独立确认/生效/停用；生效要求同企业同行业已确认 profile，单一生效。
- API `GET/POST /api/fsc/evidence/policies`、`GET /api/fsc/evidence/active`、confirm/activate/deactivate、`POST /api/fsc/books/{book_id}/evidence/evaluate`。
- `FscEvidencePanel` 只读展示生效政策、动作/证据、有效期和例外；空态明确“未核定政策的办理保持阻断”。

- sqlite 174/postgres 259：`fsc_evidence_request_links` 把证据政策阻断绑定到现有 `material_requests`；只记录来源政策/动作/证据种类，不复制待办状态。
- `FscEvidenceService.request_missing(actor, book_id, EvidenceRequest)`：
  - 先生成 `evaluate`；已有证据满足则 409；无生效政策/无资料联系人/无长期账本均明确 409。
  - 只使用本月服务单 `contact_user_id`；模板项与请求的标题、说明、期限、受理人逐项一致，满足既有 `material_request_guard`。
  - 同一政策/动作/业务类型/证据种类重复调用返回 `reused`，不重复建单；新政策版本生成新请求。
- 路由 `POST /api/fsc/books/{book_id}/evidence/request-missing`。

## 验证

- `test_fsc05_evidence.py` **4 passed/9.25s**：无政策阻断、自制确认 409、缺件/待核验/已验证、过期、金额受限例外、跨所 403、员工不可列策略、无联系人 409、补件建单/复用/受理人/政策绑定。
- PG17 **1 passed/0.87s** `tmp_test/ak05-pg-runs/710ef89d3e5b4f5d897ec3c8e5267be2`；PG16 **1 passed/0.87s** `tmp_test/ak05-pg-runs/ab9d52b9ce06474dbeb2d8123ec98d09`。
- 新增 174/259 后重跑：PG16 **1 passed/0.89s** `tmp_test/ak05-pg-runs/160f215c522f47c6a4b427a70f07289c`；PG17 **1 passed/0.83s** `tmp_test/ak05-pg-runs/8c5fd7954f294313b4f23ec2e39102e6`。
- 组合 FSC01/03/04/05 SQLite **12 passed/22.65s**；浏览器 tasks0712 **6 passed/53.5s**（1280/390）。
- 候选：HEAD `767f7cdf7c717c98d3516a0e31b53bd8cd9ae1a9`；source **2266 / `2af52a082d8e90fd1a35f777c8e4d40f1fffdeec028bc015a48b055090ab34d2`**；migration **419 / `de393bc13722d52c4307eabcb467535cab07205f5071cdace09c8546da9da5c0`**。

## 真实缺陷修复

- 发现已应用 SQLite 162 的复合外键列序错误：`FOREIGN KEY(company_id,firm_id) REFERENCES firm_clients(firm_id,company_id)` 在 SQLite 中被解释为 `company_id→firm_id`，导致 FSC profile 无法插入。
- 恢复 162 原文件（不改历史 SQL），新增 sqlite **173** 重建表并修正为 `FOREIGN KEY(firm_id,company_id) REFERENCES firm_clients(firm_id,company_id)`；保留数据、索引、确认不可变/禁删触发器。
- 修复 FSC01 测试夹具未 `commit` 的合成企业开户；PG 246 原本列序正确，无需改。

## 未完成

- 补件建单已完成；仍缺财务验收、重查受影响事项、回原队列。
- 复核页逐项“已验证/缺失/待核验/过期/冲突”和直达原件。
- 出租车/广告/物业/贸易/医院各一个成功+缺件反例；当前只有出租车付款合成策略。
- 前端策略创建/确认/生效；PG 并发生效；恢复用例。
- 不声称交易真实性或法规合规已认证。

未提交、未推送、未部署。
