# 前端组件使用说明（批次 I 基线，2026-11-10）

本文件是 J–L 的共同参考，记录仓库中已经存在、可直接复用的组件与约定。设计资料（Astryx / motion-prompt）只作参考，未安装、未复制源码。

## 1. 页面与壳层

| 场景 | 使用 | 说明 |
| --- | --- | --- |
| 事务所专业办理 | `AgentShell` / `AgentSidebar` + `WorkspaceHome` | 左侧导航只保留一个当前项；顶部品牌、面包屑和身份条常显。 |
| 客户/员工办理 | `ClientPortalShell` | 顶部身份与“所属企业”常显，员工不显示内部账套术语。 |
| 单账套专业工作区 | `BookWorkspaceShell` | 承载范围、账套、期间和页签；不另造第二套壳。 |
| 完整复核/原件对照 | `InlineReviewDrawer` | 桌面宽幅抽屉内左右分栏；手机改为单列并保留同一来源、版本、输入。 |
| 批量复核/导出 | `ReviewWorkbench` + `ReviewPackageWorkbench` | 选择绑定 `draft_id`，不是数组下标；导出仍由服务端复核门禁决定。 |
| 空/错误/加载 | `EmptyState`、`.loading-block`、`role=alert` | 空列表、加载中、读取失败、未知必须分开表达，不能用 0 代替未知。 |

## 2. 基础交互

- 按钮：`button.primary`、`button.secondary`、`button.small`、`.text-button`、`.icon-button`。主行动每屏只保留一个。
- 字段：原生 `input/select/textarea` + `<label>`；移动输入字号使用 `--font-size-input-mobile`（16px），避免 iOS 自动放大。
- 弹窗：`Modal`（`AppPrimitives.tsx`），使用 `<dialog>`、自动聚焦并在关闭时把焦点还给 opener；不新建平行 dialog。
- 抽屉：`InlineReviewDrawer`；只有复杂证据对照使用，普通详情不要都改成抽屉。
- 分页：`PageControls` 或既有 `.business-pagination`；必须显示总数/当前页，不把当前页合计写成全部合计。
- 状态：`.badge` 的语义类（`approved/draft/rejected/unknown` 等）只表达状态，不承担权限判断；权限始终来自服务端 profile 和每次请求。

## 3. 金额、表格与选择

- 金额右对齐、`font-variant-numeric: tabular-nums`，使用服务端返回的字符串/分值格式化；前端不重算会计结果、不自行凑平。
- 表格列保持任务所需最小集；来源、金额、差额、状态、动作可见。长摘要用可展开文本，不依赖 hover tooltip。
- 选择使用稳定业务 ID（如 `draft_id`、`source_file_id`）。筛选/分页变化不清空已选，但换企业/账套/期间立即清空；提交时逐项重核版本与授权。
- “选择本页”只选择当前页；服务端不支持跨页冻结集合时，不显示“全选全部筛选结果”。
- 列宽/密度偏好可保存到本地，但只保存偏好，不保存权限、复核资格或敏感正文。

## 4. 身份、范围与返回

- 顶部身份条常显“所属单位 + 主要身份”；完整权限说明折叠在页面末尾的“岗位与权限” `<details>`，不占用首屏任务空间。
- 企业/账套/期间在需要它们的专业页面常显；员工页面只显示企业和业务日期/月份。
- 页面切换/撤权后立即清除旧企业、旧原件、旧选择；请求带 scope key，迟到响应不得复活旧内容。
- 返回使用现有路由 query（book/company/period/focus）和 `history`；不静默把旧深链接改指向最新版本。

## 5. 状态与反馈

| 状态 | 表达 |
| --- | --- |
| loading | `.loading-block` + spinner，保留已有内容或明确“正在读取” |
| empty | `EmptyState`，说明当前筛选下没有事项 |
| error | `role=alert` + 重试按钮；不把错误显示为 0 |
| forbidden | 服务端 403/404 语义；前端不显示可操作入口，也不本地推导授权 |
| stale | 显示旧版本/来源已变化，要求重新读取；不自动换版本重试 |
| unknown | 明确“结果未知，先核对”，不自动重发、不写 0 |
| success | 只在服务端确认后显示，并与来源版本绑定 |

## 6. 本批保留与未迁移

- 保留 I 批次前已存在的 H5/TAX23 视觉与交互改进，不换名重做。
- 本批仅把岗位权限说明从首屏上方移到工作内容之后，减少重复标题/说明占用；未改业务状态机、权限合成或全局路由。
- 旧页面仍存在少量散落间距/字号值，后续 J–L 按实际页面替换为已有 token，不整仓格式化。
