# 独立复核政策：前端本地接入与验收

范围：本地工作区 `3fc1ab6e` 基线上的本轮改动；未提交、未推送、未部署，不连接真实模型。本文只说明前端与本地浏览器证据，数据库及 PG 16/17 由本轮后端验收报告独立说明。

## 已接入

- 管理后台既有“账套分工”内显示长期账套复核政策。变更须填写原因，携带读取到的政策版本；管理能力以服务端 `can_manage` 为准。默认独立政策版本 `0` 合法。
- 单笔、同屏、普通批量及同类复核包均显示当前待审版本的制单责任与政策；单笔请求提交草稿版本、政策版本和审核理由，批量确认提交审核理由。
- 批量确认首次发出的审核理由随原 command key 保存在当前授权范围的内存会话中，重试保持一致；修改理由须重建预览。切换授权范围或撤权时同时清空，不写入浏览器持久存储。
- 缺失/非法/过期责任投影不放行审核；独立模式阻止制单本人审核；单人模式明确“非独立复核”且要求办理说明。纯复核能力与编辑能力分开判断。
- 无归属旧草稿只提供显式认领操作，不把当前查看者猜成制单人。历史复核证据缺失或责任矛盾不宣称独立复核。
- 正式账簿页增加折叠的手工凭证办理区，使用正式科目的稳定 ID/版本，执行“提交待复核 → 会计审核 → 正式记账”。记账复用原提交 command_key，显示已记账凭证，未自动导出。
- 待审手工凭证仅制单本人且仍有编辑权时可填写原因后撤回；显示“已撤回，保留历史”，保留内容和责任人，移除审核/记账入口，不重写原件。
- 通用工作流固定“独立复核”文案改为“会计复核/按政策复核”，具体独立性以冻结责任证据显示。

## 红—绿证据

新增契约测试先运行，初次因新模块不存在而失败，随后实现。
继续补充有真实断言的红测再修复：

1. `review-policy.test.ts`：矛盾历史凭据（制单人与审核人为同一人，或单人政策却声明 independent=true）曾显示“已独立复核”；失败断言后改为拒绝该投影。
2. `review-policy.test.ts`：后端默认政策版本为 `0`，初版前端误拒；失败断言后接受非负政策版本，草稿/请求版本仍要求正整数。
3. `manual-review.test.ts`：缺失原 command_key 和矛盾手工审核分类曾被接受；失败断言后拒绝，避免生成无法绑定原请求的记账操作或误标独立性。
4. 原单纯 `review=true` 的显示测试补齐真实责任投影，并增加“缺责任投影不放行”用例，没有放宽新规则。
5. `manual-review.test.ts`：已撤回待审请求曾仍被判断可审核；失败断言后拒绝。
6. `review-policy.test.ts`：首次发送理由与之后编辑内容冲突时必须保留原理由；红测后加入统一冻结函数，并通过 `review-session.test.ts` 验证命令与理由同范围恢复、独立副本及撤权清空。
7. CI core 定向 20 项复跑得到 17 passed / 3 failed，确认三个旧 fixture 仍依赖同人制单复核。其中 `role3-guidance` 改由真实企业财务制单、会计发起故障复核；`stage12-workspace` 改由不同真实身份及实时政策版本审核；`stage12-inline-review` 保留未改凭证选择，但阻断本人改过的版本，并由另一身份核对。修复后定向三项通过，原手机、返回进度、错误和未自动导出断言保留。
8. 新增手工预检条件后，整文件浏览器曾出现月度交付桌面/手机两项 409：数据库旧完整性触发器只接受原六项检查，服务已返回七项。保留真实失败后交由数据库负责人修复 164/102，不删除检查、不绕过校验。后续完整 core 中月度交付两项均通过。

## 最终本地验证

| 检查 | 结果 |
| --- | --- |
| 前端全量 Vitest | 91 files / 919 passed |
| TypeScript | 通过 |
| ESLint src | 通过 |
| Prettier src + 本轮四个 e2e 文件 | 通过 |
| Vite build | 通过；第一次系统 TEMP 中 esbuild 删除临时文件被 Windows 拒绝，改用本轮独立临时目录后通过，未改构建代码 |
| 正式账簿/月度交付 + 手工办理及撤回 | 最终候选新空库 11 passed / 0 failed，77.7 秒；完整 JUnit + 退出码 0。两个旧手工造数调用改用公共准备→不同身份审核→记账 API |
| 组件 mock 浏览器 | 桌面1280与手机390：政策版本提交、本人独立审核禁用、单人说明必填、手工三步、原command_key；跨账套晚到响应、服务端只读能力、403不登出均通过；外部请求0 |

真实本地浏览器文件：`e2e/formal-books-monthly-package.spec.ts`。
真实身份：`local-accountant` 制单，`local-business-finance` 复核；额外检查本人审核403、无授权员工404、记账后没有自动导出。
使用本机 Vite + 新隔离 SQLite 环境，不是生产。

组件截图与临时 harness：`tmp_test/review-policy-ui-evidence/`、`tmp_test/review-policy-browser.mjs`。它们是本地辅助证据，不替代已入测试文件的真实流程验收。

### 完整核心回归的候选记录

- 候选 1 的完整 core 106 项已取得退出码 1 与完整 JUnit：104 passed / 2 failed，288 秒。失败仅为 `business.spec.ts` 开头两项原 30 秒总时限超时（首项导航等待 `load`，第二项业务暂存尚未完成）。没有调整通用超时，也不把它们计通过。
- 证据：`tmp_test/review-policy-core-final.log`、`tmp_test/review-policy-core-final.xml`，XML SHA-256 `C29CF90111F42CF7243AAE073D8D01AE9BE974E6B05B20D2CEE099FA5CB36293`。
- 更早一轮被对话输入中断，没有完整 XML，未纳入验收。
- 候选 2 source freeze 后使用新空库完整重跑：105 passed / 1 failed，325.4 秒，退出码 1。唯一失败为员工报销多角色链到最后导出导航时达到原 30 秒总时限。不能将 105/106 写成全量通过。证据为 `tmp_test/review-policy-core-candidate2.{xml,log}`。
- 同一候选随后独立运行正式账簿/月度交付/手工办理与撤回，11 passed，退出码 0。证据为 `tmp_test/review-policy-final11-candidate2.{xml,log}`，真实桌面/手机截图在 `tmp_test/review-policy-final11/`；已人工查看独立复核后记账与撤回保留历史的可读性。

### 单用例超时诊断（不替代原门槛）

仅对报销多角色链临时使用 CLI `--timeout=90000 --trace=on`，没有修改源代码、原 30 秒时限或 CI 配置。新空库诊断完整通过，实际 testcase 为 **24.605 秒**（包含服务启动/退出的套件总时间 32.173 秒）。

- 初次 `page.goto('/')` 8.574 秒；最长网络请求是 Vite 开发模式的 `tokens.css`、`styles.css`、`agent-shell.css`，各约 8 秒，其他首屏 CSS 约 6.8–6.9 秒。
- 133 个 API 请求，中位数 143 ms，p95 561 ms，最大 779 ms（显式导出）。请求累计耗时含并发重叠，不能与用例总耗时直接相加。
- 四次真实身份切换、五次 session 获取符合员工→部门→财务→会计的操作路径；没有额外的复核政策 GET 或手工请求列表 GET，不存在本次政策面板新增 N+1 的证据。没有外部网络主机。
- 证据：`tmp_test/review-policy-business-diagnostic.{xml,log}`、同名目录内 `trace.zip`、`tmp_test/review-policy-business-diagnostic-summary.json`。

此诊断证明完整业务链可走通，不证明原 30 秒整批门槛通过。保持原阈值，待并发测试停止后用同候选新空库独占重跑完整 106 项。

## 修改归属

本子任务仅修改 `src/` 下复核政策显示/命令接入、上述 e2e 文件、`e2e/role3-guidance.spec.ts`、`e2e/stage12-inline-review.spec.ts`、`e2e/stage12-workspace.spec.ts` 和本报告。未修改后端、SQL 迁移、C01、PG 工具或其他智能体的代码。

核心新增文件：`review-policy.ts`、`ReviewResponsibility.tsx`、`ReviewPolicyPanel.tsx`、`manual-review-types.ts`、`ManualVoucherReviewPanel.tsx`、`review-policy.css` 及两个对应单测。
集成点：`AdminMembers`、`VoucherReviewer`、`InlineReviewDrawer`、`ReviewWorkbench`、`ReviewPackageWorkbench`、`FormalBooksWorkbench`、`useBookWorkspace`、`BookWorkspaceShell` 及相关类型。

边界：前端校验用于解释与防误操作，实际权限、版本一致性、独立性、记账和导出仍须由后端事务与数据库约束强制执行。此报告不代表生产发布或真实企业试点验收。
