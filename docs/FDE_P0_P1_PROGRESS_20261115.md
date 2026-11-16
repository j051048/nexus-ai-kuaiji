# FDE P0/P1 改良进度（2026-11-15）

状态：**进行中，未整体完成**。本文件只记录本会话实际完成并验证的部分；不得把未验证的外部能力写成通过。

## 已完成并有本地证据

| 项 | 实现 | 证据 | 状态 |
| --- | --- | --- | --- |
| P0-1 恢复 | 对 HEAD `50893961` 运行 P01 全量恢复 | `tmp_test/fde-p0/p01-50893961/reports/summary.json` passed：regression 188、joint 2、identity 4，九项 p01_checks 全 true | 本地通过 |
| P0-2 上线体检 | 现有 `setup_checklist` 增加 `score` 与 `blockers`；`AdminOnboarding` 显示就绪度/阻断项 | `test_onboarding_setup.py` 3 passed；`test_stage25_onboarding.py` 4 passed/5 skipped；typecheck/build | 本地通过 |
| P0-3 路由守卫 | L1 显式写操作硬阻断；L2 有历史轮次时的指代追问标记 `memory/conversation_context`；L3 其余 `model`；`ai_route_decisions` 记录判定；阻断不创建 turn | `test_assistant_routing.py` 4 passed/8.63s；HTTP 组合 5 passed/11.89s；PG17 1/2.87s、PG16 1/3.05s；审计写独立事务 | 本地/PG 通过；L2 只带当前企业历史，不做跨会话记忆 |
| P0-4 价值基线 | 自动映射率/首次通过率/例外率/复核转化率/AI 单笔成本；系统耗时明确非人工工时 | 后端 1 passed/6.21s；前端 2 passed；浏览器 1365/390 2 passed/14.6s；PG17/16 各 1 passed | 本地/PG/浏览器通过 |
| P1 持续结账雷达 | 按账套列出资料未验收、草稿待复核、税费核定待复核；只读、不自动关账 | `test_closing_radar.py` 1 passed；浏览器 1365/390 2 passed/14.6s；PG17/16 各 1 passed | 本地/PG/浏览器通过 |
| P1 共享中心 SLA | 队列年龄>900s、拒绝行>0、通知 unknown>0 明示 breach；`automatic_action=false`；页面红色告警 | `test_firm_observability.py` 2 passed；浏览器 1365/390 故障注入 2 passed/17.9s；截图 `monitor-sla-*.png`；typecheck/build | 本地服务层/浏览器通过 |
| P1 FDE 适配器/实体标签 | `GET /api/firm/fde/adapters` 只读目录：6 类适配器、车辆/驾驶员/项目/别名/税源/OA 计数；明确 live_external=false，不授予写权限 | `test_fde_adapters.py` 1 passed/6.41s，manager 200、employee 403/404 | 本地/HTTP 通过；无前端页面 |
| P1 路由固定评测 | 开发集 21 条 + 独立验证集 v3 10 条；固定分母，写漏拦失败 | v1 21/21；v3 10/10；`test_assistant_routing_eval.py` 2 passed；v2 首跑 9/12，暴露 3 个漏拦后已修为回归集 | 本地通过；v3 未参与调参 |
| P1 安全闭环 | 新端点权限/脱敏/不回显原文回归；既有管理员、认证、范围策略套件 | `test_admin_security.py` 34/0.35s；`test_access_scope_policy.py + test_auth_supabase.py` 125/18.95s | 本地回归通过；非渗透认证 |
| P1 事务所级撤员 | 删除事务所成员后，原复核人不能再读取/审批大凭证；制单人仍可读；浏览器进入“尚未获得授权”且无凭证行 | `test_large_voucher.py` 7 passed/10.06s；`tax05-large-voucher.spec.ts` 撤员 1 passed/2.6s | 本地/服务层/浏览器通过 |
| P1 确定性偏差归因 | 同企业/同事务所两账簿对比，返回差异与凭证/来源证据锚点；`causal_claim=false` | `test_tax15_profit.py` 3/9.66s；`test_profit_variance_api.py` 1/6.52s；PG17/16 各 7/3.18s、3.21s；TAX-F 浏览器 2/32.5s | 本地/HTTP/PG/浏览器通过 |

组合服务测试：`test_closing_radar + test_value_baseline + test_assistant_routing + test_assistant_routing_eval` → **6 passed/12.11s**。

PG 补充：`test_tax_f_postgres.py -k "value or closing"` 在 PG17/16 各 **2 passed**。修复了 `to_char(text)` 方言错误，改为 `substr(CAST(... AS TEXT),1,7)`。

## 未完成/外部门槛

- P0-4 PG 已在 25496/25498 隔离库验证。
- P0-5 真实 T+ 模板认证：无客户模板与授权，保持外部待验证；合成 54 列不得写成已接通。
- P1 安全闭环：本地回归矩阵见 `docs/FDE_P1_SECURITY_CLOSURE_20261115.md`；`test_admin_security.py` 34 passed、`test_access_scope_policy.py + test_auth_supabase.py` 125 passed；上传绑定 HTTP 因本机 Starlette 0.35 夹具不兼容，未计通过。独立扫描/生产边界仍未验证。
- 偏差归因已加 `TaxFPanel` “04 确定性偏差归因”入口，无独立页面。
- M 批次未启用；`package.json`/lockfile 未改。
- 未提交、未推送、未部署、未接真实模型/通知/T+/OA。

## 当前候选

- HEAD `50893961edcf85a62238d9f8a7b0acfa980702c8`
- source 2196 / `9256af545b018fb0ce090834ab9186bfcc34ce1df1e40e57f1155ea0c1906835`
- migration 386 / `86b11b3b90cd79e57e0f73c5e4c13d3121dc1ff83b3850081d10d6f83789d1d5`
- 候选含并行任务迁移，工作区约 253 项未提交；P01 绑定的是旧候选，当前候选未重跑 P01。
