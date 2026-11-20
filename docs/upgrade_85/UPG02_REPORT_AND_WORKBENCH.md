# Batch 02：报表合规封堵与多法人高频工作台三件套

- **先读**：`docs/upgrade_85/UPG00_MASTER.md`、`docs/rework/RWK03_OPT09_MISSING.md`、`docs/rework/RWK04_REPORT_MODE.md`、`docs/fsc_opt/OPT09_FRONTEND_WORKBENCH.md`、`nexus_backend/app/reports/service.py`、`src/WorkspaceRouteContent.tsx`、`src/MonthlyClientWorkbench.tsx`、`src/FormalClosePanel.tsx`
- **前置**：Batch 01 完成
- **提分目标**：彻底封堵报表静默回落小企业与历史 `formal` 合规漏洞，补齐 5 人高频管理 5 家企业最急需的三大前端工作台件（拉升维度 1、2、4，推动维度 2 突破 8.5）

---

## 1. 实施任务

### 1.1 完成 RWK04-A（彻底消除小企业模板回落）
- 先写失败测试证明：无生效政策或政策准则缺模板时，当前 `reports/service.py::create` 仍静默回落至 `small_enterprise` 模板出数；
- 修改 `nexus_backend/app/reports/service.py::create`：无生效政策时拒绝生成记录并返回 409“核算政策未核定，无法出表”及建档入口指引；政策准则缺对应模板时拒绝生成并返回明确错误；
- 全面清理仓库其余隐式 `"small_enterprise"` 默认回落（重点核查 `reports/profit.py`、`assistant/delivery_details.py`、月度简报、期初与开账服务，种子与枚举除外）并逐项列清单；
- 更新前端报表与关账页：无政策时显示明确提示与政策入口，不展示空表或小企业默认表。

### 1.2 完成 RWK04-B（历史报表 `legacy` 模式前向迁移）
- 查询当前 SQLite 与 PostgreSQL 最新迁移号，新增双库前向迁移（不修改历史迁移 292）：
  1. 扩展 `report_runs.mode` 的 `CHECK` 约束加入 `'legacy'`；
  2. 将 292 迁移前生成且缺少 `policy_version` 与 `policy_approver` 的历史行回填为 `'legacy'`（记录统计口径与受影响行数）；
  3. 将 `mode` 默认值改为非 `'formal'` 安全值（如 `'trial'`）或移除默认值并确认全部插入点显式赋值；
  4. 确保未发布的 `legacy` 报表不可新发布，已发布的 `legacy` 报表仍可读可下载；
- 在接口、前端模式标签、交付包、导出、助手与集团汇总中将 `legacy` 与 `formal` 醒目区分（标注“门槛建立前发布”）。

### 1.3 完成 RWK03-A（常驻“单位 / 身份 / 期间”顶部切换条）
- 在工作区壳层顶部实现常驻切换条，在所有工作页面可见：分开展示“我所属单位”与“当前办理单位”、当前身份、当前期间与锁期状态；
- 仅列出当前用户有权单位；切换时复用未保存离开拦截、中止在途请求并更新 URL 参数支持浏览器后退恢复；
- 逐页审查并移除与顶部同义的重复月份输入框（保留区间查询框并写清标签），在状态台账列移除/保留清单；窄屏（390px）折叠为一行摘要但单位与期间常驻可见。

### 1.4 完成 RWK03-B（不可逆操作二次输入确认对话框）
- 基于 `src/AppPrimitives.tsx` 的 `Modal` 实现统一确认对话框组件：
  - **不可逆类**（锁期、正式过账、发布、对外发送）：展示对象、单位、期间与后果说明，**必须输入期间或对象名称方可启用确认按钮**，默认焦点在取消；
  - **可恢复类**（放弃未保存编辑）：普通确认，默认焦点在取消；
- 替换 `src/` 业务代码中全部 `window.confirm`（含 `FormalClosePanel.tsx`、`FormalBooksWorkbench.tsx`、`DeliveryFollowupPanel.tsx` 等）；
- 复核抽屉打开后 400ms 内忽略快捷键 `A`，防止连按误审核。

### 1.5 完成 RWK03-C（月度交付“单位 × 办理环节”一屏总览矩阵）
- 改造 `src/MonthlyClientWorkbench.tsx`：桌面端（1366×768）改为“行=单位、列=现有办理环节”矩阵，5 家合成单位全部在首屏可见，单元格读取接口权威字段，点击直达对应对象；保留现有筛选与窄屏卡片布局，支持与 `AnnualMatrixPanel` 互相切换。

---

## 2. 验收命令与通过标准

```text
.venv/Scripts/python.exe -m pytest nexus_backend/tests -q -k "report or policy or close or delivery"
.venv/Scripts/python.exe -m pytest nexus_backend/tests/test_pg_sql_hygiene.py -q
grep -rn "window\.confirm" src --include=*.tsx --include=*.ts
npm run typecheck && npm run lint && npm test -- --run && npm run build && npm run test:frontend-budget
npx playwright test e2e/role2-firm-period.spec.ts e2e/v09-navigation-history.spec.ts e2e/monthly-batch.spec.ts e2e/stage17-monthly.spec.ts --reporter=line
```

- 另须运行本批新增的 Playwright 用例（1366 与 390）：6 个页面切换条一致性、人为延迟旧单位响应防串数、锁期输入解锁与 `A` 键 400ms 防误触、5 企一屏矩阵直达；
- `window.confirm` 仅允许测试文件命中；前端体积预算维持 `passed`；
- 更新 `docs/rework/RWK_STATUS.md` 与 `docs/rework/UPGRADE_85_STATUS.md`。
