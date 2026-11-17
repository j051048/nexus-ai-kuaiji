# FE06：长尾页面扩面与联合验收

先读 `00_EXECUTION_MASTER.md`。前置：FE01–FE05 各模块已有状态记录。

## FE06-A  全路由清单

**做法。** 只读。从 `src/workspace-route.ts`、`src/WorkspaceRouteContent.tsx`、`src/workspace-navigation.ts` 和可直达的链接生成页面清单，写入 `docs/design/frontend-redesign/ROUTE_COVERAGE.md`。每行包含：

- 路由与对应组件文件。
- 可进入的角色。
- 是否已在 FE02–FE05 处理（写明模块号）。
- 是否使用公共 `Button`、`Modal`、页面级状态、`MoneyCell`。
- 该组件样式文件在 FE02-A 守卫基线中的十六进制颜色数和小字号数。
- 覆盖它的 Playwright 用例。

**验收。** 清单存在，行数与路由定义中的视图数一致（2026-11-16 时为 36 个视图，以实际为准）。

## FE06-B  长尾页面统一

**做法。** 按清单处理 FE02–FE05 未覆盖的页面：后台设置、成员管理、通知与消息、迁移工作区、学习工作台、无权限页、错误页等。每次只处理一个页面或一组紧密相关的页面，各记一条状态。

1. 换用公共 `Button`、`Modal`、页面级加载、空、错误状态。自己实现的弹层和焦点陷阱改用 `Modal`（2026-11-16 时约 10 个文件）。
2. 样式中的硬编码颜色和小字号改为变量，下调守卫基线。
3. 移除标语式大标题。
4. 金额显示改用 `MoneyCell`。
5. 只删除有证据显示无引用的样式（搜索类名在 `src/` 和 `e2e/` 中均无命中）。不确定就保留。
6. 不改这些页面的功能。

`src/LegacyMigrationWorkspace.tsx`（约 3,100 行）、`src/AdminMembers.tsx`、`src/App.tsx` 体量大：只做上述替换，不做拆分重构；拆分建议记入"顺带发现"。

**验收。** 每个页面：门槛通过，覆盖它的现有 Playwright 用例通过，桌面与手机各截一张。全部完成后在状态文件报告守卫基线的总数变化（十六进制颜色总数、小字号总数，改版前后各一个数字）。

## FE06-C  联合验收

在**同一份代码**上一次跑完下表。任何一项之后又改了代码，受影响的项重跑。

| 项 | 必须给出的证据 |
| --- | --- |
| 构建与静态 | `npm run typecheck`、`npm run lint`、`npm run format:check`、`npm test`、`npm run build`、`npm run test:frontend-budget` 的输出 |
| 核心链路 | 本地合成数据与真实本地接口：员工交票 → 企业财务核验 → 会计独立复核 → 正式记账或导出 → 月度交付 → 客户签收。输出所用用例名和结果 |
| 反例 | 跨单位、跨期间、部分成功、版本失效、权限撤销，各至少一条现有用例通过 |
| 浏览器与视口 | Chromium 下 375、390、平板、1366、1920 的核心场景；Firefox 与 WebKit 跑复核、原件、员工提交、返回这四个子集（按现有 Playwright 项目配置） |
| 无障碍 | `e2e/qa-accessibility.spec.ts`、`e2e/imp06-readability.spec.ts`、`e2e/workbench-focus.spec.ts`：关键页面无新增 serious 或 critical 问题 |
| 减少动态效果 | `e2e/reduced-motion.spec.ts` 通过 |
| 品牌一致 | 登录页和五种角色首页，桌面与手机各一张，同一份代码截取 |
| 权限与导出 | 工作台、附件、跨单位、来源版本、会计复核、导出相关的后端用例复跑；PG 本机无库时写"PG 未验证" |
| 体积 | 改版前后首屏脚本与样式体积各一个数字 |

**验收。** 上表每一项在状态文件有命令和输出。跑不了的项写明原因，不留空、不写"通过"。

## FE06-D  交付记录

写一份 `docs/design/frontend-redesign/ACCEPTANCE.md`，包含：

1. 每个模块的最终状态，直接引用 `FE_STATUS.md` 的结论，"部分完成"和"阻塞"逐条列出。
2. 改动的文件清单和新增的测试清单。
3. 守卫基线的前后对比。
4. `TOKEN_MAP.md`、`MOTION.md`、`BRAND_INVENTORY.md`、`ROUTE_COVERAGE.md` 的最终位置。
5. 所有"留给人评审"事项的汇总清单。
6. 所有"顺带发现"的汇总清单。

不写总结性评价，不写完成百分比。

## 留给人评审

- 页面级 200% 浏览器缩放下的可用性。
- 横屏和矮视口。
- 人工读屏。
- 真实手机与微信内浏览器。
- 弱网下的实际体验。
- 五名财务人员的试用反馈。
