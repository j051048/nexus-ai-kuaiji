# 核验页面减负与当前事项办理卡片：本地验收

日期：2026-11-09。结论：本轮本地验收通过；未提交、未推送、未发布。

## 范围与候选

- 起点为干净工作区，HEAD：`065629b3d310bf6cc255c7a36ceb9222ef09be69`。
- 本轮源码指纹：`e1ad3dd2b9d9cfa4930efb353996a199f6e424170d3f256ffeaafe5f6ce654f7`，2028 个源码文件。
- 349 个迁移文件未改；迁移指纹仍为 `5ac57554c2b7e68f0cbc126f4a54b3c7481db18b208d8be114a0edf71729024f`。
- 本文在验收完成后生成。业务源码指纹由既有 `release_candidate` 规则计算，不包含验收文档；浏览器报告另保留运行时的完整工作区摘要。
- 汇总证据：`tmp_test/review-assistant/accepted/acceptance.json`。此前 `final/` 等试跑目录不代表最终门禁通过。

## 产品变化

1. 企业财务核验详情先展示申请的日期、金额、交易对方，再展示当前事项办理卡片。正常流程说明、已就绪账套说明和识别配置提示按需展开；实际差异、错误和阻断不折叠。
2. 卡片自动绑定当前企业、业务发生月份、事项 ID 和版本。提供“核对原件”“差异与依据”“核对后办理／办理交接”定位按钮，复用现有表单、原件预览、确认与返回流程。
3. 卡片解释下一位处理人、下一步和依据范围。没有当前原件或自动核对不可用时如实提示，不把“打开助手”当作核验完成。
4. 卡片读取失败、范围不匹配或撤权时清空依据；事项改版返回 409，要求重新读取事项及依据。重试是读取，不重复发送审批或交接。
5. 手机保留返回事项列表、触控按钮和正常文字换行；桌面继续使用现有列表与详情布局。未增加首页模块。

## 复用与安全边界

新增只读端点：`GET /api/agent-workspace/context/events/{event_id}`，必须提供 `company_id`、`period`、`expected_version`。

端点复用业务事项可见性、权限计算、原件绑定和现有确定性交叉核对。响应包含范围、原件 ID/hash、核对差异、依据摘要与受限定位目标。没有模型调用、任意工具或写入接口；打开卡片不会创建审批、费用或模型 attempt。

PostgreSQL 读取使用可重复读快照。红测确认：在同一读取事务内再次检查，无法看见另一会话刚提交的改版或撤权。实现因此在收集依据后结束原事务，再以新读取事务确认当前可见性、状态、事实版本和操作权限。真正办理仍由现有写接口重查权限和版本。

未修改 C01、核算规则、费用/attempt 实现、数据库权限、历史 SQL 或 checksum。企业隔离、独立复核及会计复核后导出的原控制保持。

## 先红后绿

- 新增事项投影测试先因接口缺失失败；再实现范围、原件与权限投影。初始红测 JUnit：`tmp_test/review-assistant/red-backend.xml`。
- 浏览器先复现当前详情缺少办理卡片；随后补真实本地 API 事项链、定位操作及“打开卡片零 POST”的断言。
- PG 双连接测试先复现读取过程中改版／撤权未被接纳边界拒绝，再增加新事务重检；PG16/17 均通过。
- 页面精简时补测警告不能随正常说明折叠。修复后保留原有可访问区域语义，既有引导、凭证关联异常和退回流程回归通过。
- 精确门禁曾拒绝六个新用例的错误登记名称；已按实际分组标题修正清单。未删除用例，未放宽精确匹配、跳过或重试规则。

## 最终验收结果

| 项目 | 结果 | 证据 |
|---|---|---|
| 前端单测 | 145 文件、1373 项通过 | `tmp_test/review-assistant/accepted-frontend.log` |
| 后端离线相关回归 | 82 项通过、无跳过 | `tmp_test/review-assistant/accepted-offline.xml` |
| PG16 / PG17 业务与工作台 | 各 45 项通过、无跳过 | `accepted/business16.xml`、`accepted/business17.xml` |
| PG16 / PG17 财务持续门禁 | 各 40 项通过、无跳过 | `accepted/finance16/summary.json`、`accepted/finance17/summary.json` |
| PG16 / PG17 C01 新库门禁 | 各 34 项通过、无跳过 | `accepted/c01-16/summary.json`、`accepted/c01-17/summary.json` |
| 桌面、手机任务门禁 | 开发版 36、构建预览版 36，通过且零门禁问题 | 下述浏览器报告目录 |
| 补充浏览器回归 | 28 项通过 | `tmp_test/review-assistant/broad-final.log` |
| 门禁与体积检查器测试 | 117 项通过 | `tmp_test/review-assistant/final-gate-tests.log` |
| TypeScript、ESLint、Ruff、格式检查 | 通过 | 本轮命令输出及 `accepted-lint.log` |
| 正式构建与体积预算 | 通过 | `accepted/production-budget.json` |

表中 `accepted/` 指 `tmp_test/review-assistant/accepted/`。业务、财务、C01 和浏览器最终报告均核对了上述同一源码指纹。补充 28 项在最后修正门禁名称前运行，业务代码相同，不替代最终门禁。

浏览器报告目录：`test-reports/e2e-tasks/af5830ed-37ea-4a47-898e-3517e43263cc/reports/`。新增用例已加入既有 `tasks` 精确清单，PG 用例已加入既有 `finance` 必跑清单；现有 CI 会执行它们。本轮没有推送，未声称远端 CI 已通过。

正式构建初始 JS 565450 字节，gzip 175134 字节，分别低于现行 570000／176000 预算。带本地测试身份入口的预览构建与正式构建分开统计，没有调高预算。

截图已人工检查：

- 桌面：`tmp_test/review-assistant/final/desktop-top.png`
- 手机：`tmp_test/review-assistant/final/mobile-top.png`

浏览器使用本地真实 UI/API、虚构身份与 SQLite；PG16/17 使用另外的隔离新库和受限角色动态测试。没有把 SQLite 浏览器通过当作 PostgreSQL 授权证据。

## 复跑入口

```text
python scripts/test_accounting_offline.py nexus_backend/tests/test_business_context_card.py nexus_backend/tests/test_stage14_agent_workspace.py nexus_backend/tests/test_stage14_agent_workspace_api.py nexus_backend/tests/test_business.py nexus_backend/tests/test_dcp_reliability_gate.py -q -p scripts.qa_no_skip

python scripts/dcp_reliability_gate.py --suite finance --major 16 --config <独立本地受限角色配置> --out <新证据目录>
python scripts/dcp_reliability_gate.py --suite finance --major 17 --config <独立本地受限角色配置> --out <新证据目录>
python scripts/c01_postgres_gate.py --config <对应版本本地配置> --out <新证据目录>

npm test
npm run lint
node scripts/run_e2e.mjs --mode both --suite tasks
```

配置含本地凭据，不纳入文档或提交。测试仅使用隔离库；本轮启动的四个本地 PG 集群已停止，未删除诊断数据。浏览器服务已退出。没有使用真实模型、真实通知、生产数据或生产服务。
