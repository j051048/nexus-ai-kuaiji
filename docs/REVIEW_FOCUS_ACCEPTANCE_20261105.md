# 企业财务核验与会计复核界面验收

日期：2026-11-05。结论：本轮本地验收通过。本任务没有提交、推送、部署或访问生产，也没有接真实模型及通知通道。

## 修改归属与候选

- 开始时 HEAD 为 `c0a9931471e79f5d20e2c572ff8c957be522c22f`，工作区已有 132 个修改/未跟踪路径；清单及摘要保存在 `tmp_test/review-focus-20261105/baseline.json`。
- 工作期间其他任务将既有成果提交为 `dd5369277b5bf55205cb3496ca46e3d73efd77df`。本任务未执行提交、撤销、覆盖或合并操作。
- 132 个既有路径中，本轮仅在 `BusinessWorkspace.tsx`、`InlineReviewDrawer.tsx`、`WorkflowGuide.tsx` 上追加修改，修改前副本保存在 `before/`；其他 129 个路径内容摘要未变，无缺失。
- 本轮另修改 `IntelligencePanel.tsx`，新增 `review-focus.css`、`review-focus-render.test.ts`、`e2e/review-focus.spec.ts` 及本报告两份文件。
- 最终源码和测试摘要在 `REVIEW_FOCUS_ACCEPTANCE_20261105.json`。最终浏览器组合开始前冻结摘要，结束后重新比对。未修改后端、SQL、C01、费用、attempt、权限或审核导出规则。

## 最终行为

1. **企业财务核验**：以“财务核验”及当前事项为主，桌面给详情更多宽度。增加“核对原件”“办理本事项”直接定位；当前状态、下一步、金额和原件直接可见。完整步骤、识别字段、审批历史按需展开，操作区提前到历史记录之前，退回原因按需展开且仍必填。当前核对阻断、交接准备、操作错误不随历史折叠。
2. **财务待办范围**：从事项链接进入时复用 `initialBusinessScope` 选岗位范围，不再一律进入“会计接收”而显示空列表。财务展示依据本企业的可信能力投影，交接完成后不因单个操作权限消失而切换界面；操作权限仍由原服务端字段决定。
3. **会计同屏复核**：保留一组审核/外部交付/事实版本摘要，去掉重复状态和版本；助手由用户主动展开。增加“查看原件”“核对分录”“前往审核确认”，定位只滚动和聚焦，不勾选、不调用处理、不写入。只有实际修改后显示“保存草稿”。独立复核责任、借贷检查、原件失败、人工确认和版本约束保持原样。
4. **手机失败处理**：选中事项时，错误和成功反馈显示在当前办理区，避免落在手机上已隐藏的列表中。409 后读取同一事项最新状态，错误仍显示，不自动再次核验、交接或签收。

## 失败测试与修复

| 本地证据 | 修复前结果 | 说明 |
|---|---|---|
| `red-unit.txt` | 1 failed / 1 passed | 紧凑引导缺少财务专用说明标题 |
| `red-browser.txt` | 2 failed | 财务说明未收敛；宽屏会计助手默认展开挤占核对区域 |
| `red-conflict.txt` | 1 failed | 手机详情隐藏列表后，版本冲突提示不可见 |
| `red-stable-role.txt` | 1 failed | 交接后财务标题随事项权限消失而改变 |
| `red-finance-scope.txt` | 1 failed | 财务事项链接选中了会计接收范围 |
| `browser-regression.txt` | 1 failed / 50 passed | 组合回归发现本轮重排引入同级重复 key，刷新后重复核对面板；改为不同前缀，保留原人工处理功能 |

另有一条退回用例最初用事务所账号读取未交接的退回事项，正确收到 404；改用原申请人读取，不放宽产品权限。中间运行不计最终通过，最终计数来自冻结后重新启动的 `browser-final.txt`。

## 最终回归

| 检查 | 结果 | 日志 |
|---|---|---|
| 前端全量 | 131 files / 1244 passed | `frontend-final.txt` |
| 业务、API、准备检查、录入、上传绑定 | 74 passed / 0 skipped | `backend.txt`、`backend.xml` |
| 浏览器组合 | 51 passed | `browser-final.txt` |
| 类型检查与构建 | passed | `build-final.txt` |
| ESLint | passed | `lint-final.txt` |
| 本轮文件格式 | passed | `format-final.txt` |
| `git diff --check` | passed | 本地复核 |

日志均在 `tmp_test/review-focus-20261105/`。新增 6 个浏览器用例，包含桌面/手机财务核验与交接、会计原件/分录定位与人工审核、退回必填及原事项保留、阻断可见和冲突不重发。

组合回归同时覆盖：员工提交→部门审批→财务核验→会计独立复核→实际 CSV 下载；未保存修改及返回进度；会计助手按需打开、换凭证清空；原件 503；员工权限拒绝；退回补件撤权与版本冲突；C01 stale/running 恢复等已有路径。原有浏览器测试文件本轮未改动。

```powershell
npm run test
npm run lint
# Windows 构建使用本轮独立 TEMP/TMP。
$env:TEMP = Join-Path $PWD 'tmp_test/review-focus-20261105/build-temp'
$env:TMP = $env:TEMP
npm run build

# 将本地 Python 3.11 环境 Scripts 目录加入 PATH 后运行。
$env:E2E_WEB_PORT = '18880'
$env:E2E_API_PORT = '18800'
npx playwright test e2e/review-focus.spec.ts e2e/v03-entry-review.spec.ts e2e/role3-guidance.spec.ts e2e/business.spec.ts e2e/batch1-submission.spec.ts e2e/stage12-inline-review.spec.ts e2e/v07-role-help.spec.ts e2e/stage14-context-assistant.spec.ts e2e/employee-flow.spec.ts e2e/ux04-06-journey.spec.ts e2e/c01-intake-recovery.spec.ts --reporter=line --trace=off

python scripts/test_accounting_offline.py nexus_backend/tests/test_business.py nexus_backend/tests/test_business_api.py nexus_backend/tests/test_business_mapping_visibility.py nexus_backend/tests/test_business_preparation.py nexus_backend/tests/test_stage12_intake.py nexus_backend/tests/test_upload_binding_http.py -q
```

## 桌面、手机与验证边界

已检查 1440×900、390×900 的财务入口、原件、办理区、会计原件与分录截图，并检查手机版本冲突提示。新增用例断言定位目标进入视口、无横向溢出、只读定位不产生写请求、审核前必须人工确认、审核不自动导出。既有组合另覆盖 375px、740px 等宽度。

- `finance-entry-1440.png` / `finance-entry-390.png`
- `finance-original-1440.png` / `finance-original-390.png`
- `finance-action-1440.png` / `finance-action-390.png`
- `finance-conflict-390.png`
- `accounting-original-1440.png` / `accounting-original-390.png`
- `accounting-facts-1440.png` / `accounting-facts-390.png`

截图位于同一日志目录。正常路径使用本地身份 API、隔离 SQLite、真实本地业务 API 和合成原件；409 与部分已有故障场景使用明确的响应拦截。未新增 PostgreSQL 16/17 动态验收、真实票据准确率、真实手机或跨浏览器结论。本轮没有改动这些环境或服务。

既有 Starlette 弃用、构建静态/动态 import 和主包大于 500 kB 提示仍在，不属于本轮性能治理范围。
