# 前端第 4–8 项收口记录

日期：2026-11-05。范围：财务／会计清单、事务所首页、月度工作、视觉层级与移动端验证。

结论：**第 4–7 项本地工程验收通过；第 8 项自动化和截图检查通过，真实员工独立试用与手机真机检查未完成。不能将本记录解释为真实用户满意度或生产验收。**

## 候选与修改归属

- HEAD：`dd5369277b5bf55205cb3496ca46e3d73efd77df`，叠加本地未提交修改。
- 测试候选指纹：`17b8660717d895132fb238d25897cc420ee62d081d09c868d9c3ccc8e60aead3`。
- 冻结清单：`tmp_test/workbench-4-8-20261105/candidate.json`，覆盖 1,822 个源码、测试、脚本、依赖及配置文件。验收结束再次逐文件核对，无漂移。
- 开始时已有 9 个未提交路径，记录于 `baseline-status.txt`。上一轮 7 个源码／样式／测试文件与其验收 JSON 中的 SHA-256 完全一致；上一轮两份报告未编辑。
- 本轮没有修改后端、数据库迁移、C01、身份授权或记账／复核／导出规则；没有提交、推送、调用真实模型、通知或线上服务。

## 逐项结果

| 项 | 实际变化 | 验收边界 |
| --- | --- | --- |
| 4 财务／会计清单 | 去掉财务下一项的重复小卡和重复按钮；岗位引导后置；清单更紧凑；批量复核未预览时不占用空白侧栏，流程说明折叠 | 原件仍通过现有同屏复核打开；返回保留期间和选择；缺证据不能审核；未取消原确认流程 |
| 5 事务所工作台 | 短运营概览前置；默认展示 3 家需协调客户，其余可展开；搜索紧贴分页客户列表；查询后展开结果并定位 | 搜索不改变全所汇总；既有撤权、范围拒绝清空行为通过回归 |
| 6 客户月度工作 | 企业名只出现一次；先展示当前任务、负责人、期限、下一步；六组指标与服务明细按需展开 | 保留原有批量、人工接手、开期入口及全部阶段下钻；未知进度不显示为完成；下一步遵守已有权限与目标验证 |
| 6 返回进度 | 展开状态复用已有每次登录的进度内存，最多保存 20 个合法行 ID | 返回保留月份、搜索、页码、展开状态；不同期间不复用；不保存金额、原件、授权或响应 |
| 7 视觉与操作 | 复用现有颜色、字号、间距、触控尺寸；减少重复卡片；复核清单使用可用宽度；月度明细展开为分组网格 | 没有新设计系统或新的业务导航；修复月度说明文字对比度及手机月份输入裁切 |
| 8 手机容错 | 上传失败会定位到处理提示；保留填写；显式读取最新草稿，再由用户重试 | 后台自动保存失败不抢焦点；失败后不自动重传或提交；只验证模拟断网、文件上传和缩小视口 |

本轮功能源码：`WorkspaceHome.tsx`、`FirmCockpit.tsx`、`MonthlyClientWorkbench.tsx`、`ReviewWorkbench.tsx`、`IntakeWizard.tsx`、`workspace-progress.ts`、`workbench-focus.css`。

新增 `src/workbench-focus.test.ts`、`e2e/workbench-focus.spec.ts`。既有测试仅调整与新布局冲突的旧断言（重复卡、默认排序、折叠明细入口），保留权限、范围、金额、复核和导航断言。

## 先失败再修复

证据均位于 `tmp_test/workbench-4-8-20261105/`：

| 失败证据 | 复现的问题 | 最终验证 |
| --- | --- | --- |
| `red-unit.txt`：7 failed | 概览顺序、搜索区域、折叠指标、下一步权限、未知状态、展开恢复及岗位清单契约缺失 | 新增 7 项与全部前端测试通过 |
| `red-browser.txt` | 搜索与客户结果分离、不能直接看到结果 | 桌面／手机搜索实际本地数据通过 |
| `mobile-red.txt` | 手机月度主行动被冗余内容挤出可视区 | 新样式下下一步在 390×900 首屏可点击 |
| `browser-first.txt` | 月度边界说明对比度不足 | axe 无违规 |
| `browser-second.txt` | 上传断网处理提示位于屏幕外 | 错误定位、读取最新、一次人工重试通过 |

`browser-regression.txt` 曾包含旧布局断言失败及一个上传恢复测试同步问题，不能用作最终通过证据；已更新过时的界面期望，并等待恢复完成后再选择文件，没有取消产品上传锁。最终结果见下表。

## 同候选回归

| 检查 | 结果 | 证据 |
| --- | --- | --- |
| 全量前端单测 | 132 文件，1,251 passed | `final-unit.txt` |
| 后端业务、准备检查、录入、上传绑定 | 74 passed，0 skipped | `backend.txt`、`backend.xml` |
| 开发模式组合浏览器回归 | 62 passed | `browser-final.txt` |
| 构建预览新增浏览器场景 | 5 passed | `browser-preview.txt` |
| Mobile WebKit 三岗位键盘／无障碍 | 3 passed | `mobile-webkit-final.txt` |
| TypeScript、lint、构建 | passed | `final-lint.txt`、`final-build.txt`；构建命令包含类型检查 |
| 本轮文件格式、diff 空白检查 | passed | `final-format.txt` |
| 候选及上一轮文件核对 | 1,822 个候选文件、7 个上一轮文件均无漂移 | `final-ownership.json` |

浏览器使用隔离本地 API、SQLite、虚构身份和原件；部分既有权限／冲突场景与新增断网采用显式故障注入。没有新增 PostgreSQL 16/17 验收。5 个构建预览用例是开发模式对应场景的重复验证，不应计为新的独立覆盖。

构建仍有主包约 564 kB 的既有体积提示及一个动态／静态导入提示；不是本轮验收失败。后端有现有 Starlette/httpx 弃用提示。

## 复跑

```powershell
npm test -- --run
npm run lint
npm run build
npx playwright test e2e/workbench-focus.spec.ts e2e/review-focus.spec.ts e2e/employee-flow.spec.ts e2e/ux07-09-journey.spec.ts e2e/ux10-12-operations.spec.ts e2e/v02-role-workspaces.spec.ts e2e/v06-business-journey.spec.ts e2e/stage17-monthly.spec.ts e2e/stage8-review-learning.spec.ts e2e/qa-accessibility.spec.ts --reporter=line --trace=off
```

本次使用仓库已有的本地 Python 3.11 虚拟环境（`tmp_test/ci-fix-20261102/venv311`），给 Playwright 设置独立 API／前端端口；构建临时目录设为本轮目录。构建预览额外设置 `E2E_FRONTEND_MODE=preview`。测试服务已退出。

## 截图与未完成项

截图：`firm-1440.png`、`firm-390.png`、`firm-search-1440.png`、`firm-search-390.png`、`monthly-1440.png`、`monthly-390.png`、`review-list-1440.png`、`review-list-390.png`、`mobile-reduced-viewport.png`、`mobile-upload-recovered.png`，均在本轮产物目录，最后一份来自构建预览。

仍待实际执行：手机相机、iOS／Android 系统键盘与微信内置浏览器；不熟悉系统的员工独立完成任务。Mobile WebKit 不是真机 Safari，缩小视口不等于真实键盘，合成文件上传不等于相机拍摄。实际试用步骤与记录模板见 [WORKBENCH_USABILITY_TRIAL.md](WORKBENCH_USABILITY_TRIAL.md)。
