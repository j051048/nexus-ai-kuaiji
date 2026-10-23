# 岗位身份与工作台统一：本地验收报告

日期：2026-10-23。基线 HEAD：`cf07ef0874c76b37f55c364e42b08cce9852c46e`。

本轮范围：修正身份优先级，统一首页、导航、身份说明及主界面选择；保留兼任职责入口。未提交、未推送、未部署，未执行迁移、访问生产或调用真实模型。

结论：本轮范围本地验收通过；后端全仓验收仍须单列下述两条旧查询次数断言，不能混报全绿。

## 实现

1. 后端主身份顺序为事务所岗位 → 企业管理 → 企业财务 → 部门审批 → 员工。企业管理员具备提交、审批或附加账套权限时仍是 `manager`；事务所负责人、管理员、经办、复核分别提供 `firm_owner/admin/accountant/reviewer` 展示身份。能力位、授权 SQL、资源范围没有改变。
2. 前端 `presentationRole` 为统一入口，`roleHome` 为岗位文案来源；首页标题、引导、导航和用户身份一致。移除未再使用的旧 `roleCopy` 字典。旧 API 没有 `display_role` 时保留已有明确岗位，附加账套能力不会自动替换客户主身份。
3. 员工、审批人、财务和企业管理默认使用客户门户。兼任账套权限通过显式“会计工作区”进入专业页面，返回首页仍是原岗位。企业账号管理入口仍检查真实管理能力。仅有展示标签不会获得新权限。
4. 客户导航共用岗位定义，例如员工“首页／我的事项”、审批人“待审批／部门事项”、财务“待核验／业务事项”、管理人员“企业首页／报告”。简单首页保留可主动展开的列表选项、保存视图以及实际获授权的财务、复核等筛选条件。
5. 复核及草稿任务弹窗在两种界面中共用，避免财务门户点击同屏复核后锁住页面而没有弹窗。没有复制审核或导出业务逻辑。

## 先失败、再修复

| 证据 | 修复前结果 | 内容 |
| --- | --- | --- |
| `tmp_test/workspace-role-identity-red.xml` | 10 failed / 9 passed | 企业管理身份被提交或审批能力覆盖 |
| `tmp_test/workspace-role-finance-priority-red.xml` | 1 failed | 管理身份被另一企业的财务职责覆盖 |
| `tmp_test/role-contract-red.xml` | 9 failed / 17 passed | 附加能力替换主身份、首页说明及体验口径不一致 |
| `tmp_test/role-capability-filters-red.xml` | 1 failed | 简单界面需要保留合法兼任工作筛选 |
| `tmp_test/role-workspace-red.xml` | 13 failed | 真实 React 渲染仍用旧首页文案和统一客户菜单 |
| `tmp_test/role-simple-options-red.xml` | 1 failed | 简单首页需保留列表视图和进度功能入口 |
| `tmp_test/role-finance-inline-red.log` | 1 failed | 财务门户点击同屏复核后没有弹窗 |

早期浏览器冷启动超时、本地验证身份刷新回到默认 owner，以及测试夹具初始化竞态均未计作产品红测。未配置模型的合成图片会产生识别阻断；旧报销全流程用例已补上“重新核对 → 会计记录人工处理依据 → 审核 → 导出”的真实步骤，没有跳过检查。

## 验收证据

- 前端完整 Vitest：`tmp_test/role-frontend-final.xml`，386 passed，无失败、错误或跳过。
- 后端身份专项：`workspace-role-identity-green.xml`，20 passed。
- 后端现有 workspace HTTP：`workspace-role-http-regression.xml`，10 passed。
- 后端业务及管理隔离：`workspace-role-business-admin-regression.xml`，46 passed。
- 后端既有 workspace / reads：`workspace-role-existing-regression.xml`，19 passed，另有两条基线查询条数断言失败，详见下节。
- 浏览器最终同轮组合：`tmp_test/role-browser-accepted.xml` / `.log`，27 passed（58.5 秒），无失败、错误或跳过。包含角色呈现、深链接、切身份、财务兼任复核弹窗、即时撤权、桌面及手机报告范围、报销至会计审核及 CSV 导出，以及费用管理入口隔离。早期失败及中间复跑报告保留，不冒充最终验收。
- TypeScript、ESLint、定向 Ruff 通过。生产构建通过（`role-build-final.log`）；默认 Windows TEMP 下 esbuild 删除临时文件被拒，改用本轮子进程专属 TEMP/TMP 后构建成功，未修改系统设置。

浏览器使用正式构建的本机 preview 和隔离 SQLite 库。真实员工、审批人、财务及附加复核授权由本地服务提供；其余岗位的额外浏览器展示矩阵采用受控 profile 投影，权限正确性由后端真实角色测试验证。local 演示模式原本在刷新后重置默认账号，测试仅把默认 `/api/session` 固定到同一个真实 persona 的 session 响应，没有修改认证代码，也未冒充 Supabase 登录验收。

## 已知基线问题和边界

`test_stage22_workspace_reads.py` 两条旧断言分别期望 3/4 次查询，实际为 2/3：

- `test_home_task_page_and_totals_share_one_authorized_projection`
- `test_connection_helpers_share_snapshot_but_not_authorization_between_requests`

用 HEAD 原版 `WorkspaceService` 在内存替换后同样失败（`workspace-role-query-baseline.xml`、`check_workspace_role_baseline.py`），本轮未改其测试或增加查询来满足旧计数。因此不能宣称后端全仓套件全部通过。

本轮仍使用现有事务所范围聚合主身份；同一人在多个企业担任不同岗位时按上述主身份优先级展示，各企业实际数据和操作仍逐次授权。企业粒度的工作视角、三个主要工作台的进一步功能收敛及新手引导属于后续工作。

工作区原有 C01、持续 PG 门槛与 OCR 等未提交修改单独保留。起始基线记录于 `tmp_test/role_identity_baseline.json`；21 个 C01 / PG 门槛相关已有修改路径的内容 hash 与本轮开始一致。没有重置、清理或纳入这些文件。
