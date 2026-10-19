# UX01 执行报告：角色体验与企业上下文统一

## 1. 结论

**UX01 展示契约本批完成；UX02–UX05 未开始，不能宣布五批体验验收通过。**

本轮只收敛角色/上下文展示，不改服务端授权、账务、复核、导出、数据库或 C01。

## 2. 基线

- HEAD：`59764a9b`
- C01/DL 未提交修改保留；本轮未回退。
- 本轮修改：
  - `src/workspace-types.ts`
  - `src/workspace-presentation.ts`
  - `src/workspace-presentation.test.ts`
  - `src/ClientPortalShell.tsx`
  - `nexus_backend/app/workspace/service.py`
  - `docs/UX_ROLE_EXPERIENCE_MATRIX.md`

## 3. 角色矩阵

9 个展示身份：

员工、部门审批、企业财务、企业负责人、记账经办、会计复核、事务所负责人、管理员、只读。

四类工作空间：

- 客户协作；
- 会计作业；
- 事务所管理；
- 系统管理。

默认首页/4–6 项导航/主要操作见 [UX_ROLE_EXPERIENCE_MATRIX.md](UX_ROLE_EXPERIENCE_MATRIX.md)。

## 4. 实现

- 集中 `workspace-presentation.ts`：展示身份、工作空间、导航、默认首页、上下文模式、客户端 tabs。
- `WorkspaceProfile.display_role` 为可选服务端展示字段；缺省按已有能力回退。
- 后端 `/workspace/profile` 仅新增只读 `display_role`，不改变 `role` 或授权。
- 客户门户 tabs 按展示身份过滤，不再固定同一菜单。
- 多企业/单企业/全所/系统上下文有明确模式。
- 未知/缺 profile 回退 viewer，不默认管理员。

## 5. 测试

```powershell
npm test -- --run src/workspace-presentation.test.ts src/workspace-role-home.test.ts
# 9 passed
```

后端：

```powershell
pytest test_stage12_workspace.py test_stage12_workspace_api.py -q
# 27 passed
```

类型/构建：

```text
tsc --noEmit: pass
eslint src --quiet: pass
npm run build: pass（首次因系统 esbuild 临时目录 Access denied 失败；改用本地 TMP 后通过）
```

## 6. 未完成

- UX02：导航/首页/统一待办入口归并；
- UX03：同屏复核、批量、进度保留；
- UX04：客户提交/补件/账号管理流程；
- UX05：助手入口、视觉规范、桌面/手机完整验收；
- 截图、真实浏览器多角色路径、请求数量对比；
- 生产权限/RLS 验收未做。

## 7. 边界

前端隐藏只用于展示，服务端仍逐次授权；本轮未新增权限或业务状态。

未提交、未推送、未部署。
