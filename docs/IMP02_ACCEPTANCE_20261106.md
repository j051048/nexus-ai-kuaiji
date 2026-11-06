# IMP02 验收记录：岗位外壳与首屏压缩

- 日期：2026-11-06
- 工程状态：**本地通过**
- 候选：HEAD `143d4aea…`，source 1909 / `7886e47a…`，migration 338 / `2c96acd5…`（本轮新增测试与基线脚本后）
- 外部：真人/真机 pending，不影响本包本地结论

## 1. 改前基线

- 截图：`tmp_test/imp/imp-20261106-a/shell-before/{desktop,wide,mobile}-{employee,department,finance,accountant,reviewer}.png`
- 计算样式/布局：`tmp_test/imp/imp-20261106-a/shell-before/metrics.json`
- 1366×768 员工：heading 72px、首条主动作 top=468；部门：heading 94px、首条主动作 top=381。
- 390×844 员工：heading 44px、首条主动作 top=517；财务首条主动作 top=588。
- 所有角色均无横向溢出；身份条始终在关闭态显示“所属事务所/所属企业”和岗位。

## 2. 红测

`src/workspace-home-focus-render.test.ts` 新增：

```text
removes decorative English and the duplicated 当前工作台 label while keeping role help
```

首次运行失败：

```text
AssertionError: expected '<main ...' not to contain 'HENGZHANG · ACCOUNTING AGENT'
```

同一断言随后检查 `<span>当前工作台</span>` 不存在，且保留“岗位说明与权限”“当前职责与授权范围”。

## 3. 最小修改

- `src/WorkspaceHome.tsx`
  - 删除装饰英文眉题 `<p class="workspace-kicker">HENGZHANG · ACCOUNTING AGENT</p>`。
  - 岗位说明折叠条首个标签由“当前工作台”改为“岗位说明与权限”，保留岗位名、展开说明和 `aria-label="当前职责与授权范围"`。
- `src/AgentActivityWorkspace.tsx`
  - 删除“ACCOUNTING AGENT · OPERATIONS”装饰眉题；后台运行页仍保留标题与进度说明。
- 未改 `AccountIdentity` 的授权/撤权逻辑、后端身份 API、导航或角色优先级。
- 未删除岗位帮助；详情仍需用户展开，且为原生 `<details>`，键盘可操作。

## 4. 验证

| 检查 | 命令 | 结果 |
|---|---|---|
| 单元/渲染 | `npx vitest run src/workspace-home-focus-render.test.ts src/account-identity-render.test.ts` | **13 passed** |
| 真实浏览器 | `npx playwright test --config=playwright.imp02.config.ts --reporter=line` | **8 passed / 53.9s**，4 个视口 × 2 用例 |
| 类型 | `npm run typecheck` | exit 0 |
| 构建 | `TMP=tmp_test/tmp TEMP=tmp_test/tmp npm run build` | exit 0 |
| 体积 | `node scripts/frontend_budget.mjs dist` | `status=passed`；initialJs 565606、gzip 174862；homeJs 593105、gzip 185410 |

浏览器用例覆盖：

- 员工、部门审批、企业财务、会计、独立复核五个身份。
- 1366×768、1440×900、390×844、375×812 四个视口。
- 身份条关闭态可见“所属事务所/所属企业”和“权限与范围”。
- 无装饰英文；无横向溢出；主动作/入口存在。
- 身份条可用键盘 Enter 展开；展开期间 **0 个 POST/PUT/PATCH/DELETE**。

## 5. 改后布局

- 截图：`tmp_test/imp/imp-20261106-a/shell-after/`；数据：`shell-after/metrics.json`。
- 1366×768 员工 heading 72→44px，首条主动作 top 468→440（上移 28px）。
- 部门 heading 94→67px，首条主动作 381→353（上移 28px）。
- 财务 heading 67px 不变，但去掉了装饰眉题；手机部门 heading 87→60px。
- 没有缩小正文字号来换密度；身份、单位和办理范围仍在关闭态可见。

## 6. 02-T 对照

| 用例 | 证据 | 结论 |
|---|---|---|
| 02-T1 多身份可辨认单位/岗位 | 浏览器五身份 × 四视口；`account-identity-render.test.ts` | 通过 |
| 02-T2 所属事务所 ≠ 办理企业 | 身份条对事务所角色显示“所属事务所”；现有 `accountIdentity` 单测 | 通过（未改逻辑，复验） |
| 02-T3 多企业/401/撤权不显示旧能力 | `account-identity-render.test.ts` 的 unavailable 用例；`account-identity.test.ts` | 通过（复用） |
| 02-T4 刷新/返回/唯一高亮 | 现有路由/工作区历史测试；本包未改路由 | 通过（复验范围） |
| 02-T5 重复抬头消除、四视口可达 | 红测 + `shell-before/after` 截图/metrics + 浏览器 8 项 | 通过 |
| 02-T6 展开不发写请求、键盘可操作 | 浏览器键盘用例，0 写请求 | 通过 |

## 7. 限制

- 本包只治理岗位首页/后台页外壳；未重排 `IntakeWizard` 内部（IMP04）。
- 员工“需要我处理”的未知计数仍存在，属 IMP03。
- 真机、屏幕阅读器人工、真实撤权 401 未在本包新增；仍按外部/后续包记录。
- 未提交、未推送、未部署。
