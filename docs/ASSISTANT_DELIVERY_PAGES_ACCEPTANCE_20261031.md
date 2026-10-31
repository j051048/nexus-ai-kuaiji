# 助手第七批：同一诊断版本的明细分页

日期：2026-10-31。结论：**本地工程验收通过**；未提交、推送、上线，未连接真实模型。企业隔离、独立复核与会计复核后导出规则保留。

## 交付范围

第六批的科目／银行摘要每类最多展示 20 行。本批复用确定性勾稽与冻结诊断，在助手已有回复旁增加默认折叠的“继续查看核对明细”。按银行账户／子账科目切换，每页 10 行，显示本页范围和总数，支持下一页及返回第一页，不新增导航。

新增只读端点：

`GET /api/assistant/conversations/{conversation_id}/turns/{turn_id}/delivery-details?kind=bank|control&limit=10&after=...`

- 每次读取先校验私有会话所有权、当前企业／账套权限和已完成任务。事务所负责人也不能读取他人的私有会话。
- 在同一只读事务内重新核验完整诊断快照与勾稽输入指纹。企业、账套、期间、会话、任务、分页类型和页大小都参与绑定。事实变化返回 409，要求用户重新诊断，不把新资料拼进旧回复。
- 只读取 API 已获授权的任务状态、回复和快照列；未扩大数据库权限，没有迁移。
- 翻页不调用模型、不预扣费用、不重发 attempt、不更改账务。未完成／结果未知的任务不能以分页为由重新执行。
- 前端完整验证整页身份、范围、计数、行键、证据归属及状态类型后才展示；切页／切类型立即卸载旧行并取消旧读。403 复用全局失权清空，MFA 保持二次验证语义，409 不自动重问，500 仅允许重试读取。
- 未经核验的银行覆盖继续显示缺资料，不以 0.00 充当已核实余额。明确提示“查看完明细不代表已复核、已关期或可交付”。

## 失败证据及修复

先新增分页失败测试。首轮夹具误用了银行返回字段，另有 Windows ASGI 内部 loopback 被网络禁用夹具误拦，二者均不算产品红测。修正夹具后，在独立目录解压的原 HEAD 上复现 **9 failed**：服务没有分页方法、HTTP 路由不存在；没有回退或覆盖共享工作区。前端先新增契约测试，缺失解析模块时失败，补实现后 **22 passed**。

真实 PG16 新专项首次 **5 failed**，发现 `SELECT *` 触及 API 无权读取的内部列。改为仅取三个已授权字段后 **5 passed**，没有增加 GRANT。最终纳入 PG16/17 完整门槛。

浏览器探索时修正了测试本身的两类错误：403 按现有全局契约清空整个助手，不能期待已卸载组件里仍有提示；MFA 错误码必须位于实际 API 的顶层。新用例大量建账套还曾把旧效率用例挤出第一页；改为同一文件复用一个只读账套、每例独立会话，旧用例无需修改，最终组合全部通过。上述失败日志保留，不合并为通过证据。

## 验收结果

| 范围 | 最终结果 |
| --- | --- |
| 助手六类任务、ContextPack、事实校验、取证、H01/AG05、复核后导出 | **353 passed，0 skipped** |
| PG16.15 受限 API／worker，含新增分页及既有并发／租约／费用门槛 | **52 passed，0 skipped** |
| PG17.11，同一完整候选 | **52 passed，0 skipped** |
| PG 门槛校验器 | 16 passed |
| 前端单测 | **107 文件，1122 passed** |
| 构建预览浏览器：新增分页 14 项 + 明细／事件／进度 17 项 | **31 passed，零重试** |
| TypeScript、Vite、ESLint、相关 Ruff／format、Prettier、diff 检查 | 通过 |

SQLite 与真实 PG 都验证了 22 个账户按 10／10／2 读完、读取重放、事实改变、撤权、其他企业身份、私有会话、结果未知；任务行、费用与发送次数保持。六类安全基线仍为 6/6，正常／缺资料／冲突的回答完整性为 3/3。撤权、超额、未知不计作成功业务回答。

浏览器使用真实应用、虚构登录／账套／会话 API 和合成历史回复及分页响应，验证 1440px／390px 的真实组件、错范围整页拒绝、迟到响应、MFA、失权、500、无业务写入及无横向溢出。实际生成／费用链路由受限 PG 和 SQLite 的 worker + MockTransport 验证，不冒充真实模型端到端。桌面和手机宽度截图已查看；不是手机真机验收。

## 同候选与协作边界

- HEAD：`2e222c9a5ee923bb308f0edc8810d1bd6ab30eda`。
- 完整候选 1613 文件：`a4cea5a0e334c942acb006bf45ccf91cbc08b18a91aee72dc01b1435939df47a`。
- 282 个迁移文件摘要保持 `26e9be85529136507e36a2fe5b25e7a740274d8679cc635302601d596457275a`，未改 C01／核算核心。
- 离线候选 `assistant-reconciliation-pages-v7`：`e849510732a09d73d739ac96a70b87108be19f5a55b5a8700ffe81e2cf6d75e6`。其覆盖的后端、工具和夹具逐文件核对仍匹配最终候选；后续只调整了浏览器测试账套夹具。
- 最终 PG 报告已绑定含最终 E2E 夹具的完整候选。必验清单升级为 v6／52 项，继续拒绝跳过、遗漏、替换或重复。
- 本轮 17 个源码／测试路径与产物摘要见配套 JSON；起点已有第六批两份未跟踪报告保留，未覆盖其他任务修改。
- 已停止本轮专属端口 25616／25617 的隔离集群，保留数据；其他集群与线上未操作。

[JSON 证据](ASSISTANT_DELIVERY_PAGES_ACCEPTANCE_20261031.json)；最终日志分别位于 `tmp_test/assistant-pages-offline-final`、`tmp_test/assistant-pages-pg-accepted`、`tmp_test/assistant-pages-browser-accepted.log`。截图为 `artifacts/assistant-pages-1440.png`、`artifacts/assistant-pages-390.png`。不包含凭据。

## 复跑与限制

离线：`python -m tools.assistant_replay --out tmp_test/assistant-pages-new --timeout 600`。

双版本：`python scripts/assistant_delivery_postgres_gate.py --pg16 <本机隔离配置> --pg17 <本机隔离配置> --out tmp_test/assistant-pages-new-pg`。禁止使用线上 DSN。

前端：`npm test -- --run`、`npm run lint`、`npm run build`。设置 `E2E_FRONTEND_MODE=preview`，运行 `playwright test e2e/assistant-delivery-pages.spec.ts e2e/assistant-delivery-details.spec.ts e2e/assistant-events.spec.ts e2e/stage25-assistant-progress.spec.ts`。

本批是**传输与界面分页**，每页仍对完整授权范围重算勾稽和指纹，不是数据库分页性能优化，也没有增加模型自主取证循环。游标和摘要用于身份／版本绑定，不是授权凭证或数字签名。真实模型效果、生产开销／容量、远端 CI、iOS／微信真机未验证。既有 Vite 大分块和 Starlette 弃用提示仍存在，不影响本轮通过。
