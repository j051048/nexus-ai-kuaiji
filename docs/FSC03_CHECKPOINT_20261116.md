# FSC03 检查点（2026-11-16）

## 状态

- 工程状态：本地通过（SQLite + 真实浏览器 1365/390）；PG16/17 未跑。
- 外部验收：缺少客户真实组织/排班/权限数据，未验证。
- 未提交、未推送、未部署。

## 实现

- `nexus_backend/app/fsc/organization.py`
  - `FscOrganizationService.matrix(actor)` 仅 manager 可读。
  - 只读投影：企业、账套、当前 `book_access` 角色、最新 `service_plan_versions`、人员目录。
  - 缺口：`missing_service_plan`、`missing_lead`、`lead_reviewer_same`、`backup_without_book_access`、`unstaffed`。
  - 不写权限、不创建任务、不改变复核责任。
- `nexus_backend/app/fsc/router.py`
  - `GET /api/fsc/organization`。
- `src/FscOrganizationPanel.tsx` + `src/firm-observability.css`
  - 挂载于运行可观测性页“五行业档案”之后。
  - 显示企业/账套/计划版本/主办/复核/备援/截止、当前账套角色与缺口。
  - 明确“指派不会授予权限”。
- `nexus_backend/tests/test_fsc03_organization.py`
  - 五家合成企业 + 种子客户。
  - manager 可读；employee 403/404。
  - 无账套权限的备援插入被数据库触发器拒绝（`Assignments require existing current permissions`）。
  - 合法计划（lead/backup/updated_by 均有账套权限，reviewer 为空）可读；备援不因计划获得 `book_roles`。
  - 缺计划警告存在。

## 执行结果

- `PYTHONPATH=.. ../.venv/Scripts/python.exe -m pytest tests/test_fsc03_organization.py tests/test_fsc01_profiles.py -q`
  - **2 passed / 10.11s**。
- `npm run typecheck`：**exit 0**。
- `npx playwright test e2e/tasks0712.spec.ts --reporter=line`
  - **6 passed / 42.3s**（1365 与 390 各 3 条）。
  - 覆盖组织投影标题、只读语义、“指派不会授予权限”、企业数。
- 截图：
  - `tmp_test/tasks0712-20261107/screenshots/monitor-1365.png`
  - `tmp_test/tasks0712-20261107/screenshots/monitor-390.png`

## 已知边界

- 投影中的 `book_roles` 是当前权限；计划本身不授予权限。
- 真实客户组织、实际排班、五人共享中心工时/质量未验证；不能据此宣布减员。
- 尚未做组织/计划的编辑入口；本轮只读投影，写入继续走既有人员与授权/月度服务单。

## PG16/17（2026-11-16）

- `test_fsc03_organization_postgres.py` 使用自有隔离库 `tmp_test/fsc02-pg/env16|17`，PG16 **1 passed/0.68s**、PG17 **1 passed/0.66s**。
- 覆盖：本所 manager 只读投影、缺服务计划告警、operator 403、外部事务所查询不返回目标企业。
- 修复夹具：firm 角色使用 `operator`，book 角色使用 `editor`；`AccountingService.create_book` 已自动写入 owner 的 book_access，不再重复插入。
