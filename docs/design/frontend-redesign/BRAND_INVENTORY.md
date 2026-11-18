# FE01-B 标志与品牌用法盘点

仅只读盘点，不修改 `src/`。测量记录见 `tmp_test/frontend-redesign/FE01-B/measurements.json`（1366×768，浏览器计算值）。

## 1. 出现位置

| 位置 | 图形来源 | 实测尺寸 | 颜色 | 中文名 / 副标 |
| --- | --- | --- | --- | --- |
| `index.html` | 无图形、无 favicon 文件或 `<link rel="icon">` | — | `theme-color #183f35` | 标题 `Nexus 会计 · 事务所工作台`；描述写 `Nexus AI 会计工作台` |
| `src/AuthScreen.tsx:33` | 内联 DOM，三个斜切条 `.brand-mark` | 31×33；品牌名 81.6×45.7 | 条 `#d8e3a2 / #f6f5df / #a9c19f`；左栏底色实测 `rgb(23,61,48)` | `衡账`，27px 宋体栈、字距 4；副标 `HENGZHANG · AI` 8px |
| `src/AgentNavigation.tsx:173` | 同一组三个斜切条 | 桌面 31×33；手机 20×24；品牌名桌面 126×49.9、手机 126×42.8 | 条同上；侧栏渐变 `#193e31 → #153529` | `衡账` 27px（手机 24px）；副标桌面 8px、手机 12px：`ACCOUNTING AGENT` |
| `src/ClientPortalShell.tsx:57` | 只有文字 | 文字 `衡账`（14px 基准、字重 800） | 继承正文色；头部底色 `#fffefa`，下边线 `#dce4d7` | `衡账` + 事务所名（13px、`#6c786c`）；无图形、无英文副标 |
| `src/AdminWorkspace.tsx:79` | 圆角方块内单字 `衡` | 方块 36×36；品牌整体 176.3×36 | 方块底 `#214d3c`、字白；小字 `#67776d` | 方块 `衡`（23px STSong/SimSun）+ 文字 `衡账`（21px）+ `组织管理`（13px，左边线分隔） |

其他相关入口：

- `AuthScreen.tsx:184` 的管理员入口复用同一登录壳，副标为 `WORKSPACE ACCESS`，页脚仅文字“管理员入口”，没有独立标志。
- `AppErrorBoundary.tsx` 与应用加载失败页不使用品牌图形，只出现普通标题。
- `BookWorkspaceShell.tsx:209`、`CatalogWorkbench.tsx:225`、`EfficiencyWorkbench.tsx:226`、`BusinessWorkspace.tsx:811`、`DeliveryWorkbench.tsx:378` 等页面使用英文 eyebrow，但没有第二个图形标志。

## 2. 不一致

1. **同一标志四种画法。** 登录/侧栏是三条斜切条 + 宋体字；客户门户只有 `衡账` 文字；管理后台是方形 `衡` 字；`index.html` 完全没有图标。管理后台的方块 `衡` 与后面 `衡账` 并排，读起来是“衡衡账”。
2. **英文副标三种。** `HENGZHANG · AI`（登录）、`ACCOUNTING AGENT`（侧栏）、`组织管理`（后台，中文）；客户门户没有副标。
3. **字号不一致。** `衡账` 在登录/侧栏 27px、手机 24px、后台 21px、客户门户 14px；副标在登录 8px、侧栏桌面 8px、侧栏手机 12px、后台 13px。
4. **字栈与 token 不一致。** `src/tokens.css` 把 `--font-display`、`--serif` 都定义为无衬线，但 `.brand-name` 硬编码 `Noto Serif SC/Songti SC/SimSun`，登录与侧栏实际落到宋体，后台 `衡` 落到 `STSong/SimSun`；`--font-display` 并未被使用。
5. **绿色五套值。** `--brand #245441`、`theme-color #183f35`、登录左栏实测 `#173d30`、侧栏渐变 `#193e31/#153529`、后台方块 `#214d3c`。同时存在 `--brand-hover #173d30`，登录底色与 hover 值相同但用途不同。
6. **主题色与页面标题不一致。** `index.html` 的浏览器/系统色是 `#183f35`，应用主 token 是 `#245441`；页面标题仍写 `Nexus 会计`，界面品牌写 `衡账`。

## 3. 长单位名与手机

- 侧栏 `.firm-block strong` 只有 `max-width:152px`，`.firm-block select` 固定 `width:148px`，没有 `text-overflow` 或换行策略；长所在原生下拉里会被截断，且测量中名称宽 96px，未触发实际截断。**结构风险，不是已观察到的线上截断。**
- 手机 `client-portal.css` 把 `.client-portal-firm` 直接 `display:none`，单位名在客户手机端完全不显示；这不满足原型要求的“顶部固定显示当前单位”。
- 管理后台 `.admin-brand` 为 `white-space:nowrap`，小字 `组织管理` 同样 nowrap；长单位名没有出现在品牌区，但页头右侧动作区在 390 宽下可能被压缩。
- 现有基线截图未见客户/侧栏单位名换行破版；长名称场景需在 FE01-C 原型中主动构造并检查。

## 4. 标语式大标题与占用高度

| 组件/位置 | 文案 | 实测占用 | 备注 |
| --- | --- | --- | --- |
| `AuthScreen.tsx:46` | `专注判断。让每一笔账，都有据可循。` | h1 504.4×**236px**，位于 768 高首屏的 30.7%；`.auth-story-copy` 整块 **516px**，占 67.2% | 49.18px 字号、78.68px 行高、三行；上方另有 9px 全大写 eyebrow 和 80px 上下 padding |
| `BookWorkspaceShell.tsx:209` | `ACCOUNTING, WITH CLARITY` + `每一笔，都有据可循。` | eyebrow 8px + 12px 间距 + 28px 标题（36.4px 行高）+ 说明；标题区约 118px 高（含 26px 下间距） | 首屏纵向占用明显，但小于登录页 |
| `AuthScreen` 左栏 | `A CLEARER WAY TO ACCOUNT` | 9px、14.4px 高，位于 h1 上方 27px | 全大写英文 |
| 页面级 eyebrow | `ACCOUNTING, WITH CLARITY`、`A CHART THAT FITS THE BUSINESS`、`HUMAN EFFORT / MEASURED`、`BUSINESS, WITH EVIDENCE`、`DELIVERY / RECEIPT / RECONCILIATION` 等 | 8px（手机 7px）+ 2.4px 字距 | 用英文口号替代业务标签，且 7–9px 低于 12px 下限 |

## 5. 结论（供 FE01-C 原型约束）

- 原型不重画标志：沿用三条斜切条与 `衡账` 二字，只统一比例、对齐与副标位置。
- 不再使用 8–9px 英文 eyebrow；页面标题直接写业务对象（如“复核队列”“月度交付”）。
- 单位名必须在桌面和手机顶部可见；长名称需折行或可访问的完整名称，不靠 `display:none`。
- 新原型颜色集中在方向 CSS 顶部变量；不得直接复制上述五套绿色散值。
