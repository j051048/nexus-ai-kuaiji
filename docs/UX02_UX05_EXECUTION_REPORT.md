# UX02–UX05 收敛执行报告

> 历史阶段报告：本文的 351 项前端测试和 UX02–UX05 状态早于 R1 修复。当前最终候选为 44 个文件 / 354 项前端测试，状态见 [UX_R1_CLOSURE_REPORT.md](UX_R1_CLOSURE_REPORT.md)；本文件保留当时的证据，不冒充最终候选。

## 1. 结论

| 批次 | 状态 |
|---|---|
| UX01 角色/上下文 | 通过 |
| UX02 导航/入口 | 通过（一级导航 4–6 项 + 旧入口保留） |
| UX03 同屏复核 | 核心路径通过；批量部分失败未专项验证 |
| UX04 客户提交/补件 | 桌面+手机核心路径通过；管理员开通未重跑 |
| UX05 助手/视觉/完整验收 | 部分完成：桌面/手机截图、助手入口；空/错/权限矩阵未全 |

不能宣布 UX02–UX05 全部完整体验验收通过。

## 2. 本轮改动

- `src/workspace-presentation.ts`：9 角色、4 工作空间、默认首页、4–6 导航、上下文、客户端 tabs。
- `src/AgentNavigation.tsx`：支持集中 `presentationNav`；旧入口收进“更多工具”，保留路由。
- `src/App.tsx`：按 profile 传导航与视角；`briefing` 兼容。
- `src/ClientPortalShell.tsx`：按角色过滤客户 tabs。
- `nexus_backend/app/workspace/service.py`：只读 `display_role`。
- `docs/UX_ROLE_EXPERIENCE_MATRIX.md`。
- `e2e/ux02-ux05.spec.ts` + `docs/ux-screenshots/`。

## 3. 角色—首页—导航

详见 [UX_ROLE_EXPERIENCE_MATRIX.md](UX_ROLE_EXPERIENCE_MATRIX.md)。

一级导航：
- 员工 5：首页、我的事项、资料、消息、报告；
- 审批 4：待审批、部门事项、资料、消息；
- 财务 4：待核验、业务事项、资料、消息；
- 企业负责人 4：企业首页、报告、资料、消息；
- 记账 6：今日工作、跨企业批量复核、客户事项、异常与补件、月度交付、资料；
- 复核 4：跨企业批量复核、异常与补件、月度交付、资料；
- 事务所负责人 5：全所概览、跨企业批量复核、客户、团队与效率、交付；
- 管理员 1：管理后台；
- 只读 3：首页、资料、报告。

旧导航仍在“更多工具”details，避免删除深链接/底层功能。

## 4. 测试与浏览器

前端：

```text
npm test -- --run
44 files / 351 tests passed
```

UX01 专项：

```text
workspace-presentation + role-home: 9 passed
```

后端：

```text
test_stage12_workspace.py test_stage12_workspace_api.py: 27 passed
```

浏览器：

```text
stage12-intake.spec.ts: 2 passed（桌面上传/草稿恢复/部门审批 + 手机录入）
stage12-inline-review.spec.ts: 1 passed（同屏复核、手机原件、返回进度）
b06-role-home.spec.ts: 1 passed（员工/会计角色首页）
ux02-ux05.spec.ts: 1 passed（1280/375、一级导航、助手入口、无横向溢出）
```

截图：

- [accounting-desktop.png](ux-screenshots/accounting-desktop.png)
- [accounting-mobile.png](ux-screenshots/accounting-mobile.png)
- [client-mobile.png](ux-screenshots/client-mobile.png)

静态：

```text
tsc --noEmit: pass
eslint src --quiet: pass
npm run build: pass
```

## 5. UX03/UX04/UX05 复用与缺口

复用：
- ReviewWorkbench 的筛选/分页/批量包/版本冲突；InlineReviewDrawer 的原件、证据、编辑、未保存保护；
- IntakeWizard 三模板、草稿恢复、附件、提交/补件；
- AdminWorkspace 企业/人员/开通；
- “问会计助手”统一入口与上下文切换。

未完成：
- UX03 批量部分成功/逐项失败专项浏览器场景；
- UX04 管理员在指定企业开通账号的浏览器重跑；
- UX05 空/加载/错误/权限不足/二次验证全矩阵；助手范围视觉收敛；
- 多企业慢请求晚到、A/B 企业串数据、权限撤销浏览器证据；
- 生产 RLS/鉴权验收。

## 6. 候选哈希

```text
workspace-presentation.ts
3CAE9F897316A9DC767ABB11A3DBC3889EE924713B997F98B5BA0CAD8620F99F

AgentNavigation.tsx
9152F2168E5995DEDDED4873702A216A8B5E733DB6C67D84EA71670A347E4BDA

App.tsx
7497D6A71014D8F8060B9C614915F5ABEEA920F892652276AD3B0B3A27D29FFF

ClientPortalShell.tsx
9A7D2E0827AD3F7576947FC8EE40823DB2214B7A6916A0294825C026B234ED3D
```

## 7. 边界

未修改 C01、账务内核、迁移、生产权限、复核/导出；未提交、推送、部署。

结论：UX01/UX02 完成；UX03/UX04 核心路径通过；UX05 部分完成。完整五批体验验收仍需补上述浏览器矩阵。
