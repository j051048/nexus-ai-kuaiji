# 员工首页、票据确认与退回补件验收

日期：2026-11-05。结论：本轮本地验收通过；未提交、未推送、未部署。

## 基线与修改归属

- HEAD：`c0a9931471e79f5d20e2c572ff8c957be522c22f`，本轮未改变。
- 开始时有 124 个既有修改/未跟踪路径，原始摘要保存在 `tmp_test/employee-flow-20261105/baseline.json`。
- 本轮在 8 个已有改动文件上追加界面及测试调整，修改前副本保存在同目录 `before/`；其余 116 个路径 SHA-256 未变。不存在白名单外修改或缺失。
- 本轮没有修改后端、迁移、C01 用例、权限、复核及导出规则。
- 本轮精确文件清单、文件摘要及证据摘要见 `EMPLOYEE_FLOW_ACCEPTANCE_20261105.json`。工作区其他未提交成果不属于本轮交付。

## 最终行为

1. **员工首页**：新建报销、采购/费用、日常收支放到清单之前，突出报销主入口。默认清单展示前三项，保留真实总数、展开、筛选、分页和返回选中项；未取得的数量不冒充零。重复的下一项按钮、无期限提示和岗位长说明不再挤占首屏。具体状态、金额、办理入口仍可见，处理说明可展开。
2. **票据录入确认**：复用原有解析、预填和人工确认。模板及识别候选默认收起；“核对已填写信息”直接定位金额。需要核对的票据问题保持可见，未确认不能提交，采用字段或人工改值后重新要求确认。手机表单保留固定操作区和可用的触控尺寸。
3. **退回补件**：详情和修改窗顶部显示最近一次真实退回说明，提供“补充附件”“修改信息”和“修改后重新提交”。沿用原事项 ID、企业、原件和历史版本。缺少原因时明确说明，不推测缺失字段或审批人。旧审批记录仍可展开。
4. **异常保护**：补件请求遇到 401/403/404 后清除当前原件、原因及内容并停止编辑；不降级成新建提交。修改旧事项遇到版本冲突或结果不确定时，先由用户读取同一企业的同一事项最新版本，再确认提交；不自动重发。新草稿原有幂等恢复方式保留。

## 先失败后修复

| 证据 | 修复前结果 | 对应缺陷 |
|---|---|---|
| `red-unit.txt` | 5 failed / 6 passed | 首页顺序、短清单、退回原因及说明布局 |
| `red-browser-valid.txt` | 三个页面行为失败 | 新建入口不在首屏、候选默认展开、缺少直接补件动作 |
| `red-recovery.txt` | 2 failed / 1 passed | 撤权仍留原件/原因、冲突后无读取最新事项入口 |
| `red-first-task-viewport.txt` | 手机首条操作不可见 | 重复说明挤占高度；后续以布局修复，未降低可见性断言 |

早期附件状态码及测试身份切换的夹具错误未计入产品缺陷证据。原来以“模板/识别依据全部展开”和“底部直接重提”为前提的浏览器定位器按新交互更新；确认、权限、版本及审核后导出的断言未放宽。

## 最终回归

| 检查 | 结果 | 本地日志 |
|---|---|---|
| 前端全量单测 | 130 files / 1242 passed | `frontend-complete.txt` |
| 业务、API、准备检查、录入及上传绑定 | 74 passed | `backend.txt`、`backend.xml` |
| 桌面/手机浏览器组合 | 46 passed | `browser-accepted.txt` |
| TypeScript + 构建 | passed | `build-complete.txt` |
| ESLint | passed | `lint-complete.txt` |
| 本轮文件 Prettier、git diff --check | passed | 本地命令复核 |

日志均位于 `tmp_test/employee-flow-20261105/`。浏览器组合包括新员工流程、原业务全链路、角色引导、UX04–06、V02/V03、C01 录入 stale/running 恢复及原件失败场景。

```powershell
npm run test
npm run lint
# Windows 构建使用本轮独立 TEMP/TMP，避免默认临时目录文件占用。
$env:TEMP = Join-Path $PWD 'tmp_test/employee-flow-20261105/build-temp'
$env:TMP = $env:TEMP
npm run build

# 先把本地 Python 3.11 环境的 Scripts 目录加入 PATH。
$env:E2E_WEB_PORT = '18780'
$env:E2E_API_PORT = '18700'
npx playwright test e2e/employee-flow.spec.ts e2e/role3-guidance.spec.ts e2e/business.spec.ts e2e/batch1-submission.spec.ts e2e/v02-role-workspaces.spec.ts e2e/v03-entry-review.spec.ts e2e/ux04-06-journey.spec.ts e2e/c01-intake-recovery.spec.ts --reporter=line --trace=off

python scripts/test_accounting_offline.py nexus_backend/tests/test_business.py nexus_backend/tests/test_business_api.py nexus_backend/tests/test_business_mapping_visibility.py nexus_backend/tests/test_business_preparation.py nexus_backend/tests/test_stage12_intake.py nexus_backend/tests/test_upload_binding_http.py -q
```

## 截图与边界

已人工查看桌面 1280×844 和手机 390×844 的首页、确认表单、退回修改窗共六张截图。首页自动断言新建入口和首条办理按钮在初始视口内、展开能查看其余任务、无横向溢出。兼容回归另含 375px、740px 和 1440px 场景。

- `tmp_test/employee-flow-20261105/home-1280.png` / `home-390.png`
- `tmp_test/employee-flow-20261105/confirm-1280.png` / `confirm-390.png`
- `tmp_test/employee-flow-20261105/returned-1280.png` / `returned-390.png`

页面正常路径使用本地 API、隔离 SQLite、虚构身份和本地附件。票据确认使用合成 XML 的确定性解析；撤权/冲突及部分既有故障路径使用显式拦截响应，不冒充真实供应商或 PG 动态验收。本轮未调用模型、通知通道、生产服务或数据库，也未做手机真机和新增 PG16/17 验收。

现有 Starlette 弃用提示、构建静态/动态 import 提示和大于 500 kB 的主包提示仍存在；不影响本轮通过，但不属于本轮性能治理范围。Windows 默认 TEMP 曾出现文件占用，改用任务独立临时目录后构建通过。
