# DCP 第 4 批：批量办理与人工接手前端

日期：2026-11-02。基线 HEAD：`ab83f49f91859ff799577cb43f5dd0ebad75a6cd`。
范围：本地前端接线、离线回归及 Chromium 桌面/手机视口验收。未提交、推送或部署；未接真实模型或通知。

## 已接通

- 现有「月度交付」页增加「批量办理」「人工接手」，没有新增导航栏目。批量操作和指派仅向 `manage_firm` 岗位显示；其他事务所岗位可读取授权范围内的接手队列，不能指派。
- 从现有开期结果直接进入同一批次的逐户管理。历史批次分页查询，可刷新、下载失败清单；排队项可以取消/跳过并填写原因，运行项仅登记取消申请。
- 从当前月度列表这一页显式勾选企业，预检已复核凭证导出/交付包草稿，确认后提交冻结 manifest。逐户展示阻断或完成结果；凭证 CSV 通过既有鉴权接口下载，返回导出 ID 不一致时拒绝下载。交付包链接携带准确包 ID。
- 接手复用当前唯一适配器：税务底稿。选择负责人、期限及理由，排除制单人，不默认选中候选人。指派绑定当前版本，回执同时核对来源/版本/指纹/账套/接手人；409 后重新读取，再由人确认。
- 状态不会暗示额外操作：包草稿不是已发布/已送达；取消申请不是已取消；结果未知不提供重发；指派不是授权或复核。保留企业隔离、独立复核和复核后导出。

## 先红后绿

1. 先新增 `batch-operations.test.ts`：模块不存在时失败；随后补齐预检范围、严格版本、重复企业与未知状态、回执绑定及结果链接校验。新增非法状态测试曾实际失败（1 failed / 3 passed），收口后该文件 6 passed。
2. 真实组件浏览器复现：从开期窗口切到逐户管理后，旧 800ms 轮询仍在运行，读取次数期望 2、实际 3。修复为卸载时取消请求和计时器，验收转绿。
3. 下载入口浏览器测试先在找不到「下载凭证 CSV」时失败，再复用 `get_export` + `downloadCsv` 接通；增加错导出 ID 不得触发下载的负向断言。

## 本地验证

```powershell
npm test -- --reporter=default
# 117 files / 1165 passed
npm run lint
# passed

# 为 Windows esbuild 提供本轮专用临时目录，不修改机器全局设置。
$env:TEMP = (Resolve-Path tmp_test/dcp-r4/temp).Path
$env:TMP = $env:TEMP
$env:PATH = (Join-Path (Get-Location) '.venv/Scripts') + ';' + $env:PATH
$env:E2E_FRONTEND_MODE = 'preview'
npx playwright test e2e/dcp-r4-operations.spec.ts e2e/monthly-batch.spec.ts e2e/p0-batch-partial-ui.spec.ts --reporter=line --max-failures=1
# preview 门槛包含 tsc + vite production build
# 6 passed / 48.4s

.\.venv\Scripts\python.exe -X utf8 scripts/test_accounting_offline.py nexus_backend/tests/test_dcp04_batch_control.py nexus_backend/tests/test_dcp05_handoff.py nexus_backend/tests/test_dcp06_review_export.py nexus_backend/tests/test_dcp06_package_drafts.py nexus_backend/tests/test_dcp_r123_regressions.py -q
# 32 passed / 76.56s
```

浏览器组合 6 项：新 4 项 + 原有开期/部分失败 2 项。覆盖 1280×844、390×844 的取消/跳过/未知禁止重发、导出/交付包草稿成功、409 不自动重提、错批次拒绝、撤权清空、指派独立性和实际文件下载。真实本地 API 验证没有已复核凭证/已发布报表时不能提交；独立复核身份不能看到管理按钮。成功导出/包草稿/指派的浏览器响应使用固定夹具，业务写入由上述后端回归验证，不能将其描述为真实客户全链路验收。

已人工查看截图：

- `artifacts/dcp-r4-batch-1280.png`、`artifacts/dcp-r4-batch-390.png`
- `artifacts/dcp-r4-package-1280.png`、`artifacts/dcp-r4-package-390.png`
- `artifacts/dcp-r4-handoff-1280.png`、`artifacts/dcp-r4-handoff-390.png`

初次开发服务器预热未完成，后改用统一 preview 门槛；初次默认 Windows TEMP 构建遇 esbuild 临时文件拒绝访问，使用本轮独立 TEMP 后构建通过。既有 SSR `useLayoutEffect` 提示仍存在，无新增测试跳过。

## 协作边界与剩余限制

- 开始时对 28 个既有未提交/未跟踪路径保存 SHA-256，结束时逐项比对无变化。本轮未改后端、SQL、CI、C01 或前 3 批内容；它们必须与本轮前端共同评审，不能将整份脏工作区视为本轮归属。
- 并行任务在本轮期间将 HEAD 推进到 `03b7d45b85068b71fb6ec0d973cce9c851cd3ae7`（另有 `47b63319`）。这些提交不是本轮操作；已核对涉及现有后端文件的工作区内容与开始摘要一致，新的 P01 CI 资源/hash 调整未纳入本轮验收。JSON 记录结束 HEAD 和本轮逐文件摘要，不将旧 HEAD 当作最终提交点。
- 本轮没有新增 PostgreSQL 结构或重跑 PG16/17；前 3 批 PG 证据见 `DCP_R123_ACCEPTANCE_20261102.md`，不冒充本轮动态 PG 结果。
- 人工接手只接现有税务底稿适配器，不声称批量/交付 unknown 的调查动作或统一时间线已实现。
- 批量选择范围明确为当前页，历史批次可分页读取；未声称 200 企业容量验收。
- 未发送邮件、通知或模型请求；没有自动审核、过账、发布交付包或签收。
