# DCP02 验收：开账准备向导（只读事实驱动）

日期：2026-11-01　基线：`4800c10c`（DCP01 已推送）

## 1. 范围与契约

新增只读端点 `GET /api/firm/onboarding/setup?company_id&book_id&period`，返回 7 步开账准备清单：

| 步骤 | 事实来源 | 完成条件 |
| --- | --- | --- |
| `template` 核算模板与适用范围 | `accounting_policies`（`status='active'` 且 `effective_from<=period<=effective_to`） | 存在覆盖本月的有效政策 |
| `accounts` 科目与辅助项 | `ledger_accounts` | 有效科目数 > 0 |
| `control` 控制科目与辅助项 | `subledger_control_settings` | `configured` 或 `not_applicable` |
| `bank` 银行账户与覆盖证据 | `bank_accounts` + `bank_statement_coverages` | 每个有效账户本月都有经核验的覆盖 |
| `opening` 期初校验与冻结 | `opening_package_versions`（approved/frozen）+ `opening_bindings`（active） | 冻结包存在且绑定本账套本月 |
| `staffing` 岗位分工 | `rr_book_policies` + `service_book_user_permissions` | 单人办理政策，或存在不同的经办人与复核人 |
| `first` 首笔业务验证 | 复用公司级清单的 `trial`（`accounting/onboarding.py`） | 本月已有提交→审批→导出记录 |

每一步都带 `owner_role`、`next_action`、`action_target{kind,book_id,period}`。`ready` 为全部必填步骤完成的推导结果，不缓存、不写入。

服务端在 `nexus_backend/app/onboarding/setup.py:37` 先走 `service.admin.onboarding()`（公司状态守卫与首笔业务事实），再在事务内 `access.company(manage=True)` 与账套归属校验（`:58`），跨企业/不存在账套一律 404。

## 2. 交付清单

- `nexus_backend/app/onboarding/setup.py`（新增，只读）
- `nexus_backend/app/onboarding/service.py`：`setup()` 委托
- `nexus_backend/app/onboarding/router.py`：`GET /api/firm/onboarding/setup`
- `nexus_backend/tests/test_onboarding_setup.py`（新增，3 例）
- `src/OnboardingSetupWizard.tsx`（新增：`SetupStepList` + 容器）
- `src/FormalBooksWorkbench.tsx`：在正式账簿页新增折叠区「开账准备」，与月结面板同页，按需加载
- `src/onboarding-setup-wizard.render.test.ts`（新增，3 例）
- `src/formal-close.css`：向导样式（桌面/移动单列）

没有迁移、没有权限模型改动、没有任何写路径；向导只读取已登记记录。

## 3. 证据

| 项 | 命令 | 结果 |
| --- | --- | --- |
| 后端单测 | `pytest nexus_backend/tests/test_onboarding_setup.py` | 3 passed |
| 相邻回归 | `pytest test_b04_api.py test_onboarding_service.py test_stage25_onboarding.py` | 10 passed / 5 skipped |
| Ruff | `ruff check`（三个后端文件 + 测试） | All checks passed |
| 类型 | `npx tsc --noEmit` | 无输出 |
| 组件 | `vitest run src/onboarding-setup-wizard.render.test.ts` | 3 passed |
| 浏览器 | `npx playwright test e2e/dcp01-close-actions.spec.ts` | 2 passed（1440×900 / 390×844） |

浏览器步骤在既有月结夹具上展开「开账准备」：断言 `开账准备 N/7 项已完成`、存在 `待办理` 项、显示「负责岗位：」、无横向溢出，并截图：

- `test-results/dcp01-close-actions-月结阻断直达办理并在返回后保留账套与月份：desktop/close-action-desktop.png`
- `test-results/dcp01-close-actions-月结阻断直达办理并在返回后保留账套与月份：mobile/close-action-mobile.png`

夹具下向导显示 4/7：模板、科目、期初、岗位已完成；控制科目、银行覆盖、首笔业务待办理，前两者给「去月结面板办理」并跳到同页 `#formal-close`。

## 4. 已知缺口（未在本包关闭）

1. `opening`、`staffing`、`trial` 三类步骤只有负责岗位与下一步文字，尚未给出跨视图深链入口（目标视图需要先确认现有面板路由，避免猜测页面）。
2. 没有「只重查这一项」；整份清单整读，10 个步骤量级下开销可接受，但缺单项刷新。
3. 向导只出现在正式账簿页；公司级开通页仍是原 7 步清单，两者口径一致但入口分散。
4. 未做 PostgreSQL 实测（本轮为 SQLite 夹具 + SQLite HTTP 测试）；SQL 均无方言特有函数，风险低但未验证。

## 5. 判定

开账准备向导达到「只读、事实驱动、分角色、可解释」的可验收状态；跨视图直达与单项刷新留待下一包。
