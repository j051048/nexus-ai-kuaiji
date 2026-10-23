# V01 视觉基础与本地验收

日期：2026-10-23。基线：`b5bedc9c5303d937b1fd27d5de64fe3b0b81b3f9`。

V01 本地验收通过：统一基础视觉 token，修复共享变量的作用域，改善主要工作区的字体、字号、按钮、输入框和导航。保留现有角色工作台、企业隔离、人工确认和会计审核后导出。未提交、未推送、未部署，未访问线上或调用真实模型。

## 修改范围

- 新增 `src/tokens.css`，由 `main.tsx` 在基础样式和主壳样式之前导入。
- 调整 `styles.css`、`agent-shell.css` 的基础排版、公共按钮、表单、焦点、状态与错误外观。
- 迁移八个主要页面样式：`workspace`、`cockpit`、`client-portal`、`intake`、`workflow-guide`、`journal-grid`、`review`、`queue-automation`。
- 新增 `visual-foundation.test.ts`、`visual-pages.test.ts` 和 `e2e/v01-visual-foundation.spec.ts`。
- 不修改 React 业务组件、API、权限、会计领域、队列、模型调用或迁移文件；入口只增加 CSS 导入。

起始工作区存在其他任务的未提交改动。`tmp_test/v01/baseline.json` 保存原始状态和哈希。除本轮 11 个已有文件外，1535 个受保护路径与开始时一致，包含 C01、PG 持续验收、OCR 等并行工作；Git 索引保持原状。

## 视觉契约

1. 松绿品牌配合中性表面、清晰正文和独立的成功、提示、警告、错误语义。旧 `--ink`、`--green`、`--muted` 等保留兼容别名。
2. `--surface`、`--surface-soft`、`--border`、`--text-muted` 等在根节点可用，删除 review/queue 对公共变量的局部重复定义。单独打开分录、弹窗和其他页面不依赖先访问复核或队列页面。
3. 工作区标题和表单统一中文无衬线字体，保留品牌专用字体；使用本机字体回退，不新增字体下载或依赖。
4. 基础正文 14px、辅助文字 12–13px、桌面输入 14px、手机编辑输入 16px。主要按钮 13px，紧凑按钮 12px；手机主要操作至少 44px 高。八个重点样式文件不再用 8–11px 压缩可读文字。
5. 浅色内容和暗色侧栏分别使用适合背景的焦点色。保留键盘焦点、原有减少动态效果支持、checkbox/radio 几何及禁用行为。
6. 分录保留金额格式、右对齐、等宽数字和手机卡片布局。增大金额及操作列空间，调整手机金额输入内边距，验证 `123456789.01` 无末位裁切；未改动金额计算或精度。
7. 待审、异常、已审、已导出仍为独立标签。公共错误和成功回执使用可读的语义颜色，不用颜色替代文字状态。

## 先失败再修复

原始证据位于忽略目录 `tmp_test/v01/`，不将测试原件或浏览器产物提交进仓库。

| 证据 | 修复前结果 | 实际问题 |
| --- | --- | --- |
| `foundation-red.xml` | 5 failed | token 层缺失、主壳重复覆盖、公共变量与控件规格缺少统一契约 |
| `pages-red.log` | 11 failed | 重点页小字号、标题字体、公共变量局部定义及金额列规格 |
| `pages-contrast-red.log` | 1 failed | 页面普通说明继续使用浅灰文字 |
| `pages-actions-red.log` | 3 failed | 手机主要操作及导航沿用 12px |
| `search-product-red.xml` | 1 failed | 搜索框的 13px `!important` 压过手机字号 |
| `state-focus-red.xml` | 2 failed | 深侧栏缺少独立焦点色，共享草稿标签对比度约 3.37:1 |
| `browser-edge-product-red.xml` | 2 failed | 手机面包屑换成两行，大额分录输入末位无法完整容纳 |
| `journal-action-red.xml` | 1 failed | 操作列只有 30px，无法容纳可读表头及触控按钮 |

初始浏览器基线保存在 `tmp_test/v01-baseline.json` 和 `tmp_test/v01/baseline/`，来自修改前构建，复现输入框/按钮/导航字号偏小、标题 serif 回退和 root token 缺失。基线全所截图处于加载状态，不作为完整工作台验收图；红测中的 teardown 超时不计作产品缺陷。

两项测试夹具问题已修正，不冒充产品修复：Node 文件读取仅用于测试并显式声明类型；CSS raw glob 在当前 Vitest 配置下返回空内容的中间结果不作产品红测证据。概览计数按既有契约可以未知，截图等待真实任务分页完成，不强制把未知计数当作 0。

## 最终本地回归

| 检查 | 结果 | 证据 |
| --- | --- | --- |
| 前端完整 Vitest | 540 passed，0 failed/skip | `tmp_test/v01/frontend-final.xml` |
| 新增视觉基础与页面契约 | 24 项，包含在上述 540 项内 | 两个新增单元测试文件 |
| 浏览器组合 | 52 passed，0 failed/skip | `tmp_test/v01/browser-final.xml` |
| TypeScript 与生产构建 | 通过 | `npm run build` |
| ESLint、差异空白检查 | 通过 | `npm run lint`、`git diff --check` |

52 项为原有 47 项核心路径加 5 项 V01 专项，包含员工独立审批、原件上传、财务移交、会计复核及导出、跨企业负向、撤权、报告范围、手机操作与工作进度。使用本机 API、独立 SQLite 库和虚构资料；测试初始化现有迁移，没有迁移持久开发库或线上数据库。

V01 专项还验证根变量在复核 CSS 懒加载及 SPA 返回后不变、键盘焦点、禁用状态、长企业名、长金额、手机搜索框及整页溢出。大额分录在未勾选核对确认时审核仍禁用，未发生审核/导出 POST，服务端保持草稿且导出记录为空。

浏览器展示矩阵沿用现有 local persona 和部分受控 profile 投影；不将其称为 Supabase 生产认证验收。没有重跑后端或 PG 全仓套件，也不据此宣布 C01 验收通过。构建仍有既有主包超过 500kB 的提示，构建成功。

## 截图验收

Chromium：桌面 1366×900、手机视口 375×812。已人工检查员工首页与录入、企业财务、事务所首页和大额同屏复核；截图在 `tmp_test/v01/green/`。

- `employee-home-*`、`employee-intake-*`、`employee-fields-*`：岗位主任务、步骤、输入和底部操作。
- `finance-home-*`、`firm-home-*`：导航、标题、长企业名称和主操作。
- `review-amounts-*`：分录全文、金额对齐、操作列与审核禁用。

这些是桌面浏览器的手机视口截图，不冒充手机真机、操作系统字体矩阵或完整 WCAG 认证。模态框截图采用当前视口，避免把下层整页误计为弹窗内容。

## 后续维护

新增界面优先使用语义 token，公共变量不要仅定义在某个业务容器中；深色容器的前景和焦点色需单独验证。不要用 `table td:last-child` 猜测金额列，不批量按色相替换状态颜色，不用全局 `!important` 压字号。

本批未重写全站历史 CSS、增加全局密度开关或重建组件库。低频页面、卡片节奏和复杂作业布局继续按 V02/V03 渐进处理，保持已完成的岗位收敛及业务边界。
