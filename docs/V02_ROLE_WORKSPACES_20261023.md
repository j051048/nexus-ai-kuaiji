# V02 三个主要工作台精修与本地验收

日期：2026-10-23。Git 基线：`b5bedc9c5303d937b1fd27d5de64fe3b0b81b3f9`，在既有 V01 未提交成果上增量修改。

## 范围与交付

本批仅调整员工、企业财务、事务所三个工作台的展示：复用原有入口、卡片、指标和操作，不增加首页模块或请求。没有新增依赖、API、后台任务、权限规则或数据库迁移，没有调用真实模型、提交、推送或操作线上。

| 工作台 | 改善内容 | 保留的业务边界 |
| --- | --- | --- |
| 员工 | 白底主卡、三个带装饰图标的原有模板入口；缩短手机主卡和标题留白，进度清单更早可见 | 仍通过同一 `onNew` 进入录入；不自动提交，不增加财务或会计入口 |
| 企业财务 | 当前资料的下一项独立显示，真实逾期有文字和警示边线；同一事项只突出一个主按钮，重复入口降为次操作 | 下一项仍受当前企业、筛选、分页、加载和实时权限约束；移交不等于审核或导出 |
| 事务所 | 白底概览和协调卡片、客户名和阶段独立排列、真实协调原因单独强调、手机三个指标纵向完整展示；空态只突出新增客户 | 原有月份、客户进度、人员和开通回调不变；未把风险分或“待复核”阶段冒充异常 |

概览计数未知仍显示 `—`，新增“数量暂未汇总，以当前清单为准”，不将未知改成零。已知零保留为零；选中筛选同时提供 `aria-pressed`。普通待办使用信息色，真实逾期、退回使用警示色，业务状态文字继续保留。键盘焦点使用根级 token，手机主要操作保留至少 44px 触摸高度。

## 文件归属

已有文件仅修改四个：

- `src/WorkspaceHome.tsx`
- `src/workspace.css`
- `src/FirmCockpit.tsx`
- `src/cockpit.css`

新增三个测试文件与本文：

- `src/role-workspaces-visual.test.ts`
- `src/firm-cockpit-visual.test.ts`
- `e2e/v02-role-workspaces.spec.ts`

开始时记录 `tmp_test/v02/baseline.json`，覆盖 1551 个路径。除上述四个已有文件外，1547 个受保护路径按 SHA-256 核对；最终结果见 `tmp_test/v02/acceptance.json`。既有 V01 其他文件、C01、后端及其他任务成果不纳入本批，Git 索引保持原状。

## 红绿证据

原始日志、XML、截图保存在忽略目录 `tmp_test/v02/`。

| 证据 | 修复前结果 | 复现的问题 |
| --- | --- | --- |
| `home-red.xml` | 4 failed / 1 passed | 岗位展示类、模板图标、未知数量解释及下一项逾期说明缺少契约 |
| `firm-red.log` | 4 failed | 空态重复主操作、阶段与异常语义、卡片 token 和手机完整指标布局 |
| `browser-red.xml` | 3 failed | 手机员工主卡约 399px、未知数量缺少说明、事务所手机指标仍挤在一行 |
| `browser-green-first.xml` | 6 passed / 1 failed | 首轮手机员工主卡仍为 343.53px，继续收紧按钮内间距，未缩小字号 |
| `finance-primary-red.xml` | 1 failed / 4 passed | 同一个下一项在主卡和清单工具栏重复使用主色按钮 |

浏览器产品断言失败与失败追踪收尾的超时分开记录，不把测试工具问题计为产品修复。

## 回归与截图

| 检查 | 结果 | 证据 |
| --- | --- | --- |
| 前端完整 Vitest | 549 passed，0 failed/skip | `frontend-final.xml` |
| 新增视觉契约 | 9 项，包含在上述 549 项内 | 两个新增单元测试文件 |
| 新增 V02 浏览器专项 | 7 passed，包含在整组中 | `browser-final.xml` |
| 核心/V01/V02 浏览器组合首跑 | 55 passed / 4 failed，共 59 项 | `browser-final.xml` |
| 原失败场景定向重测 | 3 passed；另 1 项手机表单最终通过 | `browser-recheck.xml`、`browser-focus-final.xml` |
| TypeScript 与生产构建 | 通过 | `build-final.log` |
| ESLint、差异空白检查 | 通过 | `lint-final.log`、`git diff --check` |

59 个独立浏览器场景均取得通过结果，**不是一次整组 59/59 全绿**；没有隐藏首跑失败或修改既有测试断言、时限。首次失败中记录了本机 Chromium 的 `ERR_ADDRESS_IN_USE`，涉及 API、bootstrap 和上传组件静态资源，细节在 `network-failure-diagnosis.md`；手机录入首轮失败的专属网络证据不足，不笼统归因为同一种故障。

独立进程重测后，上传审批、会计复核导出、采购退回重提和身份切换通过。手机表单曾在重测中出现一次焦点断言失败；再用 `--trace on` 排查时，焦点、字号、全局 token、禁用等业务断言与 `context.close` 均正常完成，但测试 runner 的 trace 归档收尾超时，不能把那一轮算作 passed。最后只设置本进程独立 `TEMP/TMP` 目录，使用原有 `retain-on-failure` 配置及原断言，整条用例 7.9 秒通过。未修改产品或全局网络设置来掩盖故障；这些记录仍应作为本机浏览器测试稳定性的维护依据。

最终候选哈希和受保护路径检查见 `tmp_test/v02/acceptance.json`。浏览器组合包含原有 52 项核心/V01 路径和新增 7 项 V02 路径。

截图目录：`tmp_test/v02/green/`。桌面 Chromium 1366×900，手机视口 375×812；这不是手机真机认证。已检查三类工作台的卡片层级、长企业名称、大额显示、状态、焦点、禁用和横向溢出，并通过原有入口继续录入、资料核验、月度进度和客户开通。

- `employee-home-desktop.png`、`employee-home-mobile.png`
- `finance-home-desktop.png`、`finance-home-mobile.png`
- `firm-home-desktop.png`、`firm-home-mobile.png`、`firm-empty-mobile.png`

专项使用真实本地 API、独立 SQLite 测试库和虚构身份；财务异常/未知状态、事务所长名称/协调项与空态使用受控只读展示投影，沿用已授权企业 ID 和角色能力。没有把这些展示夹具称为真实企业数据或 Supabase 生产认证验收。

原有回归覆盖员工上传、独立审批、财务移交、会计复核后导出、企业范围、撤权和原件阻断。没有重跑后端/PG 全仓套件，也不据此宣布 C01 通过。构建仍提示既有主包超过 500kB，不影响本轮构建成功。
