# P0–P2 迁移、推送与远端核验记录

本记录区分本地验收、生产运行和远端 CI；服务健康不代表整体验收通过。

## 数据库与首个提交

- 本轮主提交：`8df541688ea1c4ac7499fc9a6db2025a3ddf1e20`，43 个相关文件。
- 推送前已将生产 PostgreSQL 从 220 更新至 222（新增 221、222）。
- 210 条迁移登记的 checksum 和 compatibility 均与本地对应文件一致；历史 SQL、checksum 未修改。
- SQLite 137–139 已在本地验收使用；生产数据库为 PostgreSQL。
- 推送后 Zeabur API、业务 worker、助手 worker、迁移 worker、OCR 五服务均为 RUNNING，新部署为 READY，提交均对应 `8df54168`。
- Vercel 生产部署 READY；前端、直连 API health 和前端代理 API health 均返回 200。未运行真实模型、通知或客户业务写入。

## 首轮远端 CI 的实际结论

| 门槛 | 结果与证据 |
|---|---|
| 快检 | [通过](https://github.com/j051048/nexus-ai-kuaiji/actions/runs/42587386513) |
| 完整恢复、PG16/17 混合容量、真实本地身份业务浏览器链 | [通过](https://github.com/j051048/nexus-ai-kuaiji/actions/runs/42587386690) |
| AK05 PG16/17 | [两个 job 均通过](https://github.com/j051048/nexus-ai-kuaiji/actions/runs/42587386634)，包括 C01、独立复核、结算、任务协同及本轮 finance 专项 |
| Accounting foundation 整体 | 未通过：格式、N02/N03 门槛契约、浏览器失败，因此同候选汇总也未通过 |
| QA | [未通过](https://github.com/j051048/nexus-ai-kuaiji/actions/runs/42587386594)：三浏览器各两条 `.intake-file` 断言失败；fault-security、mobile-webkit 通过 |

已比较上一提交 `dfa8c450` 的远端日志：三处格式失败、N02/N03 的 SPECIALISTS 清单遗漏、QA 两条附件选择器失败及核心浏览器的八条失败在上一提交已存在。不能将所有红项归因于本轮，也不能因此忽略这些红项。

## 推送核验发现的本轮问题与补修

1. `dcp01-close-actions`、`finance-operations` 共四条桌面/手机用例仍假设办理面板和其他账簿面板同时可见。本地先复现 `4 failed, 2 passed`，再让用例点击真实的“返回账簿（保留筛选）”，断言 URL 与范围保持、余额仍可查询。保留原独立复核、正式记账和撤权断言。
2. 账套工具栏补载共用样式后，手机事务所“客户进度”按钮被通用按钮规则覆盖为 41.5px。原浏览器用例先失败；仅增强 cockpit 自身的触控规则选择器，恢复原有 44px 门槛，不提高通用规则优先级、不降低断言。
3. 本轮相关 13 条用例在开发构建通过。新预览构建的扩展 17 条中，这 13 条及另 3 条通过；另 1 条手机企业财务用例在 `查看待核验事项` 选择器处失败。开发构建的扩展结果同为 `16 passed, 1 failed`，此项单独保留，不记为通过。
4. 补修只涉及两份浏览器用例、cockpit 的局部样式及本记录，不改账务、权限、数据库或其他智能体工作。
5. 普通生产构建、TypeScript、相关文件格式和原体积门槛通过：首屏 JS gzip 175037 < 176000，首页 185607 < 195000 字节。首次构建遇到 Windows 公共临时目录占用，改用本轮独立临时目录完成。浏览器专用多入口构建的体积不作为生产包体积结论。

本记录是首轮运行及补修本地结果的时间点快照。补修后的远端结果应按其新提交查看 Actions，不能直接沿用首轮通过项作为新候选验收。

## 仍需独立处理

- `src/task-presentation.ts`、`src/workspace-home-focus-render.test.ts`、`src/workspace.css` 的既有格式红项。
- `test_n02_n03_ci_contract.py` 的专项目录与 workflow 委派清单不一致，阻断其后续数据库步骤。
- 附件录入、企业财务、任务直达、分析页、税务和全所页面等既有/扩展浏览器红项，需逐项复现区分产品缺陷和旧断言。
- 所有必需 CI 门槛通过前，仍不能标记完整发布验收通过。

本地详细记录：`tmp_test/finance-refinement/20261109/release/`。该目录不提交，不包含用于发布的数据库凭据。
