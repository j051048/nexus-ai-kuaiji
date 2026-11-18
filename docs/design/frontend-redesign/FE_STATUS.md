# FE01 执行状态

日期：2026-11-18  
范围：只执行 `01_BRAND_AND_PROTOTYPES.md`；未进入 FE02；未修改 `src/`。  
HEAD：`7cce49a57543f8ec3a02c68114eb1500b308eb12`。产品候选 source `2415 / c256fd54…`、migration `483 / cc997bbc…` 未因 FE01 改变。

## 总览

| 模块 | 状态 | 说明 |
| --- | --- | --- |
| FE01-A 基线截图 | 本模块通过 | 16 张真实本地页面截图；脚本 `1 passed`；无控制台错误 |
| FE01-B 品牌盘点 | 本模块通过 | 盘点与浏览器实测尺寸已记录；未改 `src/` |
| FE01-C 两方向原型 | 本模块通过 | 8 个 HTML + 1 个共用 CSS；8 条原型检查 `passed`；对比度、行数、外链、控制台均通过 |
| FE01-D 数字与动效 | 本模块通过 | 数字样张 `1 passed`，两视口无横向溢出；`MOTION.md` 完成 |
| 共享发布门禁 | **未全绿** | `test:frontend-budget` 失败：`homeCss 145755 > 145000`，超出 755 字节；本阶段未改 `src/`，不是本阶段新增 CSS 造成 |
| FE01 整体 | **未完成（共享门禁阻断）** | 设计产物与自身验收完成；按“只在实际全通过时写完成”，不写完成 |

## FE01-A 基线截图

### 现状核实

- 登录 `AuthScreen`；员工首页 `WorkspaceHome`；复核队列 `ReviewWorkbench`；同屏复核 `VoucherReviewer`；正式账簿 `FormalBooksWorkbench`；全所概览 `FirmCockpit`；月度交付 `MonthlyClientWorkbench`；客户端首页 `ClientPortalShell`。
- `/workspace/home` 是任务页；`/workspace/firm` 才是全所页。复核抽屉入口是队列行“查看证据”。
- 本地自动会话会绕过登录页，必须用 route 让 `/api/auth/config` 返回 Supabase 模式且 `/api/session` 返回 401。

### 验收目标

桌面 1366×768、手机 390×844 各 8 页；只截图不断言；截不到写原因。

### 修改文件

- `tmp_test/frontend-redesign/playwright.fe01.config.ts`
- `tmp_test/frontend-redesign/fe01-baseline.spec.ts`

### 门槛命令与输出

```text
npx playwright test -c tmp_test/frontend-redesign/playwright.fe01.config.ts fe01-baseline.spec.ts --reporter=line
1 passed (58.4s)
```

复核队列额外填入“复核期间” `2026-10`，等待 7 行；全所页等待统计；正式账簿等待“科目余额表”。首轮因自动会话、错路由、空期间失败，修正后重跑通过；失败现场图已删除，最终目录恰好 16 张。

### 截图

`tmp_test/frontend-redesign/FE01-A/run-20261118/`：

- desktop：`login`、`employee-home`、`accountant-review-queue`、`review-drawer`、`formal-ledger-balances`、`firm-overview`、`monthly-delivery`、`client-home`（1366×768）
- mobile：同 8 页（390×844）

### 留给人评审

- 正式账簿基线是“本期暂无正式余额”的真实空状态，不是截图失败。
- 队列 7 行全部“高风险”是当前 seed 的制单/复核同人数据，不是 UI 把风险都标红；FE01-C 原型另有 5 高/5 中/5 低。

### 未做/未验证

- 未做真实客户数据、权限变更和写操作。
- 未做 Firefox/WebKit 基线截图。

### 顺带发现

- 页面英文 eyebrow 仍为 7–9px，低于 12px 可读下限。
- 客户手机端隐藏事务所名，见 FE01-B。

## FE01-B 品牌盘点

### 现状核实

- 同一标志四种画法：登录/侧栏三斜条、客户纯文字、后台方块“衡”、`index.html` 无图标。
- 副标三种：`HENGZHANG · AI`、`ACCOUNTING AGENT`、后台“组织管理”；客户无副标。
- 登录 h1 实测 `504.4×236px`，`.auth-story-copy` 高 516px，占 768 首屏 67.2%。
- 侧栏品牌行 200×73.9；手机 mark 20×24；后台方块 36×36、`衡` 23px。
- `tokens.css` 的 `--serif` 是无衬线，但 `.brand-name` 硬编码宋体。

### 验收目标

只读盘点标志位置、尺寸、颜色、名称/副标，指出不一致、长名称、大标题占用；不修改 `src/`。

### 修改文件

- `docs/design/frontend-redesign/BRAND_INVENTORY.md`
- `tmp_test/frontend-redesign/fe01-measure.spec.ts`
- `tmp_test/frontend-redesign/FE01-B/measurements.json`

### 门槛命令与输出

```text
npx playwright test -c tmp_test/frontend-redesign/playwright.fe01.config.ts fe01-measure.spec.ts --reporter=line
1 passed
```

### 截图/测量

- 测量 JSON：`tmp_test/frontend-redesign/FE01-B/measurements.json`
- 盘点正文：`docs/design/frontend-redesign/BRAND_INVENTORY.md`

### 留给人评审

- 是否统一为“三斜条 + 衡账”单一口径，或保留后台方块字作为管理域标识。
- 单位名在手机端应折行、可展开还是放到上下文菜单。

### 未做/未验证

- 未在 macOS/iOS/Android 真机核对实际字体落字；字栈落字体为推断。
- 未测超长单位名在所有页面的真实截断像素。

### 顺带发现

- `theme-color #183f35` 与 `--brand #245441` 不同。
- 客户门户手机 `display:none` 隐藏单位名。
- 品牌名在后台读作“衡衡账”。

## FE01-C 两方向原型

### 现状核实

- 现有页面首屏被英文 eyebrow、大标题和状态摘要占用；复核表原为稀疏行，缺少三档风险和借贷不平示例。
- 现有品牌/颜色散值多，需用 tokens 变量收敛。

### 验收目标

两方向各四屏，共 8 个 HTML + 共用 CSS；固定单位/身份/期间；金额右对齐/等宽/千分位/两位/零空可区分；状态文字+图形；无大标语；A 暖纸宋体标题，B 冷灰全无衬线；无外链、无控制台错误；正文对比度≥4.5；复核 1366 首屏≥12 行。

### 修改文件

- `docs/design/frontend-redesign/prototypes/shared.css`
- `a-login.html`、`a-employee.html`、`a-review.html`、`a-owner.html`
- `b-login.html`、`b-employee.html`、`b-review.html`、`b-owner.html`
- `tmp_test/frontend-redesign/build-prototypes.mjs`
- `tmp_test/frontend-redesign/fe01-prototypes.spec.ts`

### 门槛命令与输出

```text
npx playwright test -c tmp_test/frontend-redesign/playwright.fe01.config.ts fe01-prototypes.spec.ts --reporter=line
8 passed (14.5s)
```

脚本实际检查：

- 控制台错误 0；外链 0；`href` 内部链接 0。
- 全部直接文本对比度：A 最低 **5.37:1**，B 最低 **5.57:1**（均 ≥4.5）。
- 字号 <12px 元素 0。
- 复核 1366×768：队列 **15 行**，首屏完整可见 **15 行**（要求≥12）；高/中/低各 5；借贷差额 `¥1,700.00`。
- 负责人：5 张企业卡，5 个不同状态。
- 390 视口无文档横向溢出。

### 截图

`tmp_test/frontend-redesign/FE01-C/screens/`，共 22 张：8 张按规格的主图（登录/复核/负责人为 1366×768，员工为 390×844）；6 张桌面屏另存 390×844 视口图；每个 HTML 另存一张 390×844-full 整页图。主要 8 张：

- `a-login-1366x768.png`、`a-employee-390x844.png`、`a-review-1366x768.png`、`a-owner-1366x768.png`
- `b-login-1366x768.png`、`b-employee-390x844.png`、`b-review-1366x768.png`、`b-owner-1366x768.png`

### 留给人评审

- A 的暖纸 + 3px 细线更像账页；B 的冷灰 + 9px 圆角更像通用工作台。
- A 在 390 下复核队列隐藏“来源”列；B 同样隐藏。是否接受手机端以“查看证据”再读来源。
- 两方向负责人页在 390 都变成纵向 5 卡，首屏只能看到 1–2 家企业；是否需要在手机端改成“列表 + 状态筛选”。

### 未做/未验证

- 未接真实数据、权限、写操作；原型只用于视觉方向选择。
- 未做真实 Firefox/WebKit 截图、真机、读屏、200% 缩放。
- 390 的视口截图只覆盖首屏；同一屏另有 `390x844-full` 整页图用于查看后续内容。复核桌面首屏行数已单独测量。

### 顺带发现

- 方向 A/B 的 390 复核队列都隐藏来源列，不能在同一屏看到来源时间；这是有意的密度取舍，需人选确认。
- 负责人长单位名会占两行，卡片高度自动对齐，未截断。

## FE01-D 数字与动效

### 现状核实

- 现有金额格式化散落；`--font-num` 为等宽栈；按钮动效已用 `--motion-fast: 120ms`，但 `monthly.css` 有 220ms/1.4s、`delivery.css` 有 150ms，超出 120/180 规格。

### 验收目标

一个数字样张 HTML 渲染两方向；`¥`、`0/O`、`1/l`、负数、千分位、九位以上、小数、空/零、20+字单位、生僻字、四种字号；列出字栈落字体；MOTION 表覆盖按压/聚焦/选中、抽屉/原件/分步、加载/刷新/切单位、成功/错误/未知、减少动态，时长只用 120/180ms。

### 修改文件

- `docs/design/frontend-redesign/prototypes/numbers.html`
- `docs/design/frontend-redesign/MOTION.md`
- `tmp_test/frontend-redesign/fe01-numbers.spec.ts`

### 门槛命令与输出

```text
npx playwright test -c tmp_test/frontend-redesign/playwright.fe01.config.ts fe01-numbers.spec.ts --reporter=line
1 passed (12.6s)
```

检查：两方向各 4 个字号；长金额 8 处；`0 O 0 O` 8 处；`1 l 1 l` 8 处；空值 8 处；无外链；控制台 0；390 无横向溢出。

### 截图

- `tmp_test/frontend-redesign/FE01-D/numbers-1366x768.png`
- `tmp_test/frontend-redesign/FE01-D/numbers-390x844.png`

### 留给人评审

- 空值用长破折号、零用 `¥0.00`；是否再增加“未填”文字以避免破折号被当成负号。
- 生僻字在 Windows 可能回退到 SimSun；是否接受。

### 未做/未验证

- 未在四类操作系统实机测落字体，只列字栈与推断。
- 未做屏幕阅读器朗读金额/负号/空值。

### 顺带发现

- 现有 `monthly.css` 1.4s 无限扫光、220ms 进度条、`delivery.css` 150ms hover 与 120/180ms 规格不一致，已记入 `MOTION.md`，留 FE02-E。

## 共享门禁

```text
npm run typecheck
exit 0

npm run lint
0 errors, 4 warnings（均为既有 unused eslint-disable）

npm run format:check
All matched files use Prettier code style!

npm test -- --run
154 files passed / 1416 tests passed

TMP=tmp_test/build-tmp TEMP=tmp_test/build-tmp npm run build
exit 0
initial: JS 567.46 KB / gzip 175.87 KB；CSS 99.95 KB / gzip 20.32 KB
home:    JS 596133 B / gzip 186824 B；CSS 145755 B

npm run test:frontend-budget
status: failed
violations: ["budget:homeCss"]  (145755 > 145000，超 755 B)
其余 initial/home JS 与 initial CSS 均在预算内。

npx playwright test -c tmp_test/frontend-redesign/playwright.fe01.config.ts --reporter=line
11 passed (1.2m)
```

说明：FE01 只新增 `docs/` 与 `tmp_test/` 文件，未编辑 `src/`。因此该失败不是 FE01 新增代码造成，但本阶段没有改动 `src/`，无法据此判断它最初由哪次改动引入，也没有修复。按本阶段约束未提高阈值、未删除预算检查。该失败使 FE01 不能写“完成”。

## 待选方向（停顿点）

### 八张原型截图

- A 登录 1366：`tmp_test/frontend-redesign/FE01-C/screens/a-login-1366x768.png`
- A 员工 390：`tmp_test/frontend-redesign/FE01-C/screens/a-employee-390x844.png`
- A 复核 1366：`tmp_test/frontend-redesign/FE01-C/screens/a-review-1366x768.png`
- A 负责人 1366：`tmp_test/frontend-redesign/FE01-C/screens/a-owner-1366x768.png`
- B 登录 1366：`tmp_test/frontend-redesign/FE01-C/screens/b-login-1366x768.png`
- B 员工 390：`tmp_test/frontend-redesign/FE01-C/screens/b-employee-390x844.png`
- B 复核 1366：`tmp_test/frontend-redesign/FE01-C/screens/b-review-1366x768.png`
- B 负责人 1366：`tmp_test/frontend-redesign/FE01-C/screens/b-owner-1366x768.png`

对应 390/1366 对照图均在 `tmp_test/frontend-redesign/FE01-C/screens/`。

### 方向 A 的具体问题

1. 390 复核队列隐藏“来源”列，无法同屏确认来源时间；必须进证据抽屉。
2. 390 负责人页五张卡纵向排列，首屏只能看到 1–2 家，失去“五企业一屏比较”。
3. 暖纸底色与 3px 细线在表格密集区边界较弱，15 行队列的分隔依赖行距而非线。
4. 数字样张 20px 长金额接近容器右边界，放大字号时余量小。

### 方向 B 的具体问题

1. 复核队列分隔线 `#e1e7ea` 在白底上更淡，15 行高密度阅读时行边界不如 A 明确。
2. 9px 圆角 + 阴影让负责人五卡更像独立卡片，横向比较时列对齐感弱于 A。
3. 登录卡片阴影在浅灰画布上边界较淡，390 下表单与页面背景分离度低。
4. 390 复核同样隐藏来源列；与 A 相同的密度取舍。

**不推荐、不打分。** 请选择 A、B 或提出修改；未收到选择前不进入 FE02。
