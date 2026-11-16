# FDE P0 改良检查点（2026-11-15）

## 结论

本轮只完成一项可验证的 P0 工程增量：**在既有客户开通清单上增加上线就绪度评分与阻断摘要**。没有重建已有模块，也没有把 P0 整体写成完成。

## P0 现状

| 项 | 状态 | 说明 |
| --- | --- | --- |
| P0-1 冻结候选、重跑 P01 | 已完成（本地） | 在 HEAD `50893961` 上重跑 P01：`tmp_test/fde-p0/p01-50893961/reports/summary.json` passed；regression 188、joint 2、identity 4，0 skipped；PG16/17 九项 checks 全 true。 |
| P0-2 上线体检 | 部分完成 | 既有 `setup_checklist` 已覆盖模板、科目、控制科目、银行、期初、岗位、首笔验证；本轮补 `score/blockers` 与前端评分展示。 |
| P0-3 L1/L2/L3 路由与判定记录 | 部分完成 | L1 只读硬规则阻断 + 逐次判定记录已通过 SQLite/HTTP/PG16/17；L2 记忆命中尚未接入，L3 模型仍沿用既有预算。 |
| P0-4 价值基线 | 部分完成 | 新增确定性 `GET /api/firm/observability/value`：自动映射率、首次通过率、例外率、复核转化率、系统耗时中位数、AI 单笔成本；`labour_time_verified=false`，不计人工工时。SQLite/HTTP 空数据契约 1 passed/6.21s；PG 测试已加入 `test_tax_f_postgres.py`，但本机 PG 已停，未执行；前端未做。 |
| P0-5 真实 T+ 模板认证 | 未完成 | 当前是合成 54 列适配；真实客户模板、字段含义和逐行回执仍未验证。 |

## 本轮实现

- `nexus_backend/app/onboarding/setup.py`
  - 返回 `score`：`completed/required/percent/blocking`。
  - 返回 `blockers`：未完成的必要步骤，含 `id/title/next_action/action_target`。
  - `ready` 仍由必要步骤是否全部完成决定，不改变既有开通判定。
- `src/AdminOnboarding.tsx`
  - 显示“上线就绪度 N% · 已完成 x/y”。
  - 未就绪时显示阻断数与下一阻断项。
- 测试：
  - `nexus_backend/tests/test_onboarding_setup.py` 增加 score/blockers 契约断言，并修复旧 Starlette 0.35 `TestClient(client=...)` 夹具不兼容；3 passed/8.77s。
  - `nexus_backend/tests/test_stage25_onboarding.py` 同样修复夹具；4 passed/5 skipped/10.24s。
  - `npm run typecheck` 0。

## 候选

- HEAD：`50893961edcf85a62238d9f8a7b0acfa980702c8`
- source：`2181 / 4b41cca93c13a4fe0728e15c770065cb0340fde9855b990852fa03ce2abb1caf`
- migration：`386 / 86b11b3b90cd79e57e0f73c5e4c13d3121dc1ff83b3850081d10d6f83789d1d5`
- 该候选已包含 P0-3 迁移 `158/241` 与 P0-4 服务；P01 仍绑定旧候选 `a8c7eee1…`，对新候选失效。

## P01 结果（HEAD 50893961）

- 候选与报告绑定同一 source `a8c7eee1…`、migration `7cc65c3d…`。
- regression 188 passed / 150.966s；joint 2 passed / 88.301s；identity 4 passed / 57.040s；skipped 0。
- PG16/17 migrated 227，九项 `p01_checks`（database/originals/identity/permissions/hold/review_export/tax_delivery/no_replay/finance_resume）全 true。
- 这是本地隔离恢复证据，不是生产发布授权。

## P0-3 本地证据

- `test_assistant_routing.py` 2 passed/7.06s。
- `test_stage15_conversations_api.py` + routing 5 passed/11.89s（含真实 HTTP 决策查询）。
- 阻断请求返回 409，审计行已提交，`assistant_turns` 不创建；只读请求创建 turn 并记录 `model_call_pending`。
- 审计只保存问题 SHA-256，不保存原文；企业成员可读本企业审计元数据。
- PG17/16 路由测试各 1 passed/2.87s、3.05s；`241` RLS/不可变触发器已过；补 `242` 给应用角色授权。
- 未做 L2 记忆命中、前端审计查看。P0-4 已补最小价值基线。

## P0-4 本地证据

- `nexus_backend/tests/test_value_baseline.py` 1 passed/6.21s：空数据 counts 全 0、rates 全 None、`labour_time_verified=false`、AI cost 0、`scope=ai_model_only`、非法月份 422。
- 只记录系统从映射到复核的耗时中位数，明确“不代表人工工时”，不能用于人效考核。
- PG 用例 `test_value_baseline_is_empty_and_never_claims_labour_time` 已加入 `test_tax_f_postgres.py`；本机 PG 集群已停，未执行，不冒充 PG 通过。

## 明确边界

- 本会话未执行 commit、push、部署、线上 SQL、真实模型、真实通知或真实 T+。
- 外部提交 `50893961` 的出现属于其他执行者/流程；不能据此宣称本会话完成 A–M 全量验收。
- 下一步优先级：先在当前 HEAD 上重跑 P01 与关键回归，再补 L1/L2/L3 判定记录；真实 T+ 模板认证仍属外部门槛。
