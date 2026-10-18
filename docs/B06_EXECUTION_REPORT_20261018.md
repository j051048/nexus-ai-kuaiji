# B06 执行报告：角色首页、同屏复核、批量交接与手机回归

日期：2026-10-18
基线：`a9daee5b` + 未提交 B03/B04/B05
性质：本机真实前后端（SQLite）验证；**未提交、未推送、未部署、未真机验证**

---

## 0. 结论

B06 在现有工作台上完成三处实际收敛：

1. **角色首页**新增服务端能力驱动的“当前职责与授权范围”，并保留既有待办/进度。
2. **会计工作区**新增“会计工作台”入口；员工继续走 ClientPortal，不显示会计入口。
3. **同屏复核**新增审核/外部导出/事实版本状态轴；**工作交接**预览与执行绑定 `plan_fingerprint`，范围变化不能静默执行另一批。

未完成/未验证：

- 不是全量 UI 改版；上传、月度批量、任务轮询、跨标签会话未重写。
- 工作交接 UI E2E 已通过；此前失败是冷启动懒加载超过测试 30 秒超时，不是权限缺陷。真实生产 MFA 仍未联调。
- 未做真机验证、自动可访问性扫描、大文件上传回归、PostgreSQL 权限验收。
- B02-B05 的正式账簿/报表/AI 管理界面仍只有 API 或未接前端。

---

## 1. 前置能力及依赖核对

| 能力 | 结论 | 证据/缺口 |
|---|---|---|
| 登录/企业/账套/期间 | 已有 | `useAccountingAuth`、`Session.scope_key` |
| 服务端权限 | 已有 | `/api/workspace/profile` 能力布尔；API 逐次校验 |
| 员工/部门/企业范围 | 已有 | Workspace/Business API；E2E 角色切换 |
| 撤权/停用/会话失效 | 已有 | 401/403 清理；scope 变化清空 |
| 员工提交/附件/补件 | 已有 | `IntakeWizard`、`upload-client`；本批未重写 |
| 审批/复核/导出 | 已有 | `ReviewWorkbench`、`InlineReviewDrawer`、E2E |
| 异常/任务/月度/资料 | 已有 | 既有工作台；本批复用 |
| 批量预览/幂等/逐项回执 | 部分 | Queue/Monthly 已有；交接原未绑定计划 |
| B02 科目 | 依赖未完成 UI | 只有 API，不伪造页面 |
| B03 正式凭证 | 依赖原型 | 复核页不显示“已记账” |
| B04 正式报表 | 依赖原型 | 参考简报不升级为正式报告 |
| B05 AI 端点/政策 | 依赖原型 | 助手沿用现有工具白名单 |

---

## 2. 角色 × 范围 × 导航 × 操作矩阵

| 角色 | 默认入口 | 首页主任务 | 导航边界 |
|---|---|---|---|
| 客户员工 | ClientPortal | 本人提交/退回/补件 | 无会计配置/事务所运营 |
| 部门审批人 | WorkspaceHome | 本部门待审批 | 不显示其他部门明细 |
| 客户财务/对接人 | WorkspaceHome | 财务核验、资料、移交 | 账套复核仍需单独授权 |
| 客户领导 | ClientPortal/简报 | 有权访问的已发布结果 | 不显示未审内部数字 |
| 记账会计/复核人 | WorkspaceHome/Review | 异常、凭证复核、导出 | 审核/导出分开展示 |
| 事务所负责人 | Firm/Operations | 客户进度、风险、负荷 | 不默认展开技术日志 |
| 运维/技术管理员 | Admin | 账号、配置、费用、运行 | 不自动获得财务读取/复核 |
| 多角色账号 | 服务端 profile | 当前企业当前能力 | 不合并跨企业权限 |

前端只展示服务端能力；直接 URL、改 ID、下载、批量接口仍由 API 拒绝。

---

## 3. 改造前后主要操作路径

### 3.1 首页

**改造前**：角色标题 + 通用待办/概览；会计工作区导航里没有直接“会计工作台”入口。

**改造后**：

- 首页显示“当前身份 / 当前可操作 / 服务端校验边界”；
- 会计工作区新增“会计工作台”入口；
- 员工切换后仍进入 ClientPortal，不出现会计/运营入口；
- 待办、筛选、企业选择、批量复核路径未改。

### 3.2 同屏复核

**改造前**：一个状态 badge（待审核/已审核/已导出）。

**改造后**：状态轴明确区分“审核 / 外部交付 / 事实版本”，并保留原有分录、证据、助手和未保存拦截。

### 3.3 工作交接

**改造前**：预览后执行，后端重新计算计划；确认的不是预览的那一份。

**改造后**：预览返回 `plan_fingerprint`；执行携带并校验；变化返回 409，要求重新预览；前端选择接替人变化会清空旧预览。

---

## 4. 同屏复核与批量确认机制

- 复用 `InlineReviewDrawer` 的同屏原件/分录/证据/助手。
- 新增状态轴：`draft`→待会计审核、`approved`→已审核、`exported`→已导出；外部交付另列“已导出，待回执核验”；事实版本显示 `vN`。
- dirty 时确认审核仍禁用；修改后旧审核失效沿用后端规则。
- 同屏复核窄屏（390×844）在既有 `stage12-inline-review` 中通过，不新增“一键通过”。
- 交接：预览对象、版本、接替人、指纹绑定；参数变化使旧确认失效（前端清空预览 + 后端 409）。
- 其他批量（QueueBatch/MonthlyBatch）未改业务范围，沿用既有幂等/逐项回执。

---

## 5. 工作进度、敏感存储与作用域隔离

- 导航偏好：沿用 `WorkspaceHome` localStorage，仅筛选/分页/密度，不含 token、原件、分录正文。
- 复核命令标识：`review-session.ts` 仅内存保存，scope 变化清空。
- 查询竞态：`useScopedQuery` + WorkspaceHome epoch/AbortController；scope 变化中止、清空旧数据、丢弃迟到响应。
- 登出/换账号/scope 变化清空页面数据；E2E 覆盖晚回 bootstrap 不恢复旧身份。
- 未保存正文不写长期 localStorage；离开时有保存/放弃/继续修改。
- 未新增离线财务资料缓存。

---

## 6. 手机布局、上传和下载验证

| 项 | 结果 |
|---|---|
| 390×844 同屏复核 | **通过**（既有 `stage12-inline-review`） |
| 角色首页桌面 | **通过**，截图 `artifacts/b06-role-home-desktop.png` |
| 手机上传/两段式直传 | **本批未重跑** |
| 大文件复核预览/下载 | **本批未验证** |
| 真机 | **未验证** |
| 自动可访问性扫描 | **未验证**；仅保留现有键盘/焦点/aria 结构 |

---

## 7. 必要后端契约改动及兼容性

**工作交接** `HandoverRequest` 新增可选 `plan_fingerprint`：

- `preview` 返回 64 位稳定 JSON 哈希；
- `execute` 重新计算计划，若提供指纹且不一致 → 409“交接范围已变化，请重新预览”；
- 未提供指纹的旧客户端仍按 legacy 执行（兼容），新 UI 必须携带；
- 无迁移；现有授权、职责分离、逐项跳过规则不变；
- 新增测试：错误指纹拒绝、正确指纹执行。

---

## 8. 修改文件和关键位置

**新增**

- `src/workspace-role-home.ts` / `.test.ts`
- `e2e/b06-role-home.spec.ts`
- `docs/B06_ROLE_HOME_UI_CONTRACT.md`
- `docs/B06_EXECUTION_REPORT_20261018.md`

**修改**

- `src/WorkspaceHome.tsx`：角色职责/授权面板。
- `src/AgentNavigation.tsx`：会计工作区“会计工作台”入口。
- `src/InlineReviewDrawer.tsx` / `src/inline-review.css`：审核/导出/事实版本状态轴。
- `src/MemberHandoverDialog.tsx`：发送预览指纹。
- `nexus_backend/app/accounting/handover.py`：指纹生成/校验。
- `nexus_backend/tests/test_member_handover.py`：指纹回归。
- `e2e/ui-navigation.ts`：ClientPortal 不展开管理层；会计列表等待冷启动。
- `src/workspace.css`：角色面板响应式样式。

---

## 9. 实际测试命令、结果及证据层次

### 9.1 逻辑/组件单测

```powershell
npm test
```

结果：**41 files / 320 tests passed**（含新增 `workspace-role-home.test.ts` 3 项）。

### 9.2 类型、Lint、构建

```powershell
npm run typecheck
npm run lint
npm run build
```

全部通过。构建提示主 chunk 约 560 kB，仅警告，未做 B07 级拆包。

### 9.3 真实本地前后端 E2E

```powershell
npx playwright test e2e/b06-role-home.spec.ts e2e/stage20-home-loading.spec.ts
# 10 passed

npx playwright test e2e/b06-role-home.spec.ts e2e/stage12-inline-review.spec.ts
# 2 passed
```

- 角色首页/员工切换/服务端能力展示：通过。
- 首页只读待办、scope 切换、晚回响应丢弃、员工无管理入口：通过。
- 桌面/手机同屏复核、未保存拦截：通过。
- 截图：`artifacts/b06-role-home-desktop.png`（虚构数据）。

### 9.4 后端契约

```powershell
pytest nexus_backend/tests/test_member_handover.py -q
# 4 passed
```

### 9.5 未通过/未验证

```powershell
npx playwright test e2e/member-handover.spec.ts
# 1 passed（冷启动等待已延长）
```

- 交接 UI 真实 E2E 已通过；后端指纹契约也通过服务测试。
- 未跑全量 Playwright；未跑 PostgreSQL 权限/并发；未做真机、上传大文件、a11y 扫描。

---

## 10. 本机截图及未验证场景

- 截图：`artifacts/b06-role-home-desktop.png`（仅虚构企业/账号）。
- 未保存手机截图；未做真机。
- 未验证：真实生产 MFA、手机上传两段式、大文件下载、跨标签会话、自动键盘/焦点审计。

---

## 11. 点击、往返、耗时或误操作比较

本轮**没有**做严格的前后耗时/点击次数实验，不能给出量化提升。

可确认的行为变化：

- 会计工作区多一个直达“会计工作台”入口；
- 首页角色能力/边界在一屏可见；
- 同屏复核状态从单 badge 变为三轴；
- 交接执行从“重新计算”变为“预览指纹绑定”，减少误执行另一批范围；
- 员工切换后不会出现会计/运营入口。

不用“页面更少/更现代”推断管理数百家企业的效率。

---

## 12. 剩余问题及对 B07 的依赖

1. 真实生产 MFA 仍未联调；本地 E2E 已通过（此前为冷启动超时）。
2. MonthlyBatch/QueueBatch 仍无统一“服务端预览对象数量”契约；本批未改。
3. 移动上传/大文件/授权下载未重跑；依赖真实附件链路。
4. 跨标签会话失效/撤权同步未新增验证；依赖现有 auth 机制。
5. B02-B05 正式能力仍无对应前端；不应在本批伪造。
6. 首页角色面板是展示层，不是权限来源；服务端仍需逐次校验。
7. 无 PostgreSQL/真机/a11y 自动验收；B07 前需补。

**B06 是本机可控的界面收敛，不是全面 UI 改版，也不是生产上线验收。**
