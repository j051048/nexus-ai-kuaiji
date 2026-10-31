# 助手第六批：交付阻断明细与办理入口

2026-10-31，**本地工程验收通过**。本批没有操作线上、调用真实模型或通知，没有改动 C01、账务内核、复核政策、费用/attempt 实现及数据库迁移。

## 交付内容

- 在现有“月结条件”步骤接入 PostingService 与 SubledgerService 的真实结果，不再只返回阻断码。有效预检复用其勾稽结果；没有预检或历史预检失效时读取当前事实，不把旧预检当作本次依据。
- 子账按科目与辅助维度显示账面余额、未结事项、差额，说明借贷符号和计算口径。金额只接受整数分，正确显示负数，不使用浮点计算。
- 银行按账户解释覆盖缺失、原件绑定、未匹配流水/正式分录、未结调节项和跨期衔接。未独立核验的对账单金额保留未知；不发送占位零余额和差额。相反方向差异分别展示，不靠合计抵销。
- 每类最多展示 20 行，优先阻断行；明确提示省略数量。完整读结果、正式余额及依赖版本仍进入指纹，省略不代表通过。校验金额、计数、资格标志及企业/账套/月份。
- 责任岗位与下一步由服务端生成，仍为未实际指派的接手清单；can_deliver=null。不新增复核、过账、导出或签收动作。
- 复用账套引用，添加受控的月结查看目标。“查看本月对账与月结”重新请求账套，核验企业、月份及编辑/复核权限后进入现有正式账簿；展开已有月结面板继续核对。历史诊断不冒充当前事实，不接受模型 URL 或命令。
- ContextPack 校验目标与交付投影一致。worker 继续在发送与结果接纳边界重取快照；事实变化、撤权、结果未知沿用原有围栏、费用链和防重发语义。

## 失败测试与修复证据

1. 新后端用例首次 **8 failed / 1 passed**：无预检时缺少科目/账户明细、失效预检没有当前调节项、期初缺失没有明确引导、缺少明细类型契约。见 tmp_test/assistant-details-red.log。
2. 接线首轮 **7 failed / 24 passed**：通用金额格式函数拒绝负数；在新展示适配器拆分符号与绝对值，保留金额校验。见 assistant-details-green1.log。
3. 前端目标契约首次因实现缺失失败，随后 8 个正常/畸形目标用例通过。没有删除负向断言或放宽权限。
4. 首次 PG 探索运行中源码仍在修改，门槛正确拒绝候选漂移，不计入最终验收；最终冻结候选后完整复跑。
5. 首次 Vite 构建遇到 Windows 临时文件删除权限错误；改用本任务独立临时目录重跑成功，没有修改构建或类型校验配置。

## 最终验证

| 范围 | 结果 |
| --- | --- |
| 六类助手任务、上下文/事实/取证、H01/AG05 及会计复核后导出链路 | **344 passed，0 skipped** |
| 新明细用例及 PG 门槛校验器定向回归 | 31 passed（与上项有重叠） |
| PostgreSQL 16.15，受限 API/worker | **47 passed，0 skipped** |
| PostgreSQL 17.11，同一候选与必验清单 | **47 passed，0 skipped** |
| 前端单测 | **106 文件 / 1,100 passed** |
| 构建预览浏览器：新增 6 项 + 执行记录/原进度 11 项 | **17 passed，零重试** |
| TypeScript、Vite、ESLint、相关 Ruff/format、Prettier、diff 检查 | 通过 |

六类安全基线仍为 6/6；正常、缺资料、冲突的回答完整性仍为 3/3。金标、输入和分母未改。未知结果不算业务成功或免费。

新增 PG 用例实际使用受限连接，覆盖科目差额/未知覆盖、跨企业排除、发送后银行事实变化、撤权及响应未知；一次发送/attempt 与费用分类保持。既有并发领取、租约、双进程恢复和审核后导出必验项继续通过。

浏览器使用真实应用组件、本机虚构身份与账套 API、合成历史回复，验证 1440px/390px 的展示、历史提示、授权导航、403/错企业拒绝、零业务写入及无横向溢出。实际生成与费用路径由 worker + MockTransport 验证，不冒充真实模型浏览器全流程。截图已查看，非手机真机验收。

## 候选与协作归属

- 起点 7cbf0b441488fa89fac5ccb479313067781fca92，工作区干净。
- 另一个任务将本轮 16 个源码/测试路径提交为 **2e222c9a5ee923bb308f0edc8810d1bd6ab30eda**；本任务没有执行提交、推送或部署，也未回退该提交。
- 最终 PG 报告与当前 HEAD、1606 个源文件摘要一致：baeb06fa6eee4d70a683f61d29bd260b9fba3d710b966413900dafc026da793f。
- 282 个迁移文件摘要未变：26e9be85529136507e36a2fe5b25e7a740274d8679cc635302601d596457275a。
- 离线候选 assistant-reconciliation-details-v6：2fd21974ce743650b018157e353e05cd69fa9dd4587bad6c98f2a60aae5accec。
- PG 必验清单 v5/47 项，继续拒绝跳过、遗漏、替换或重复。

[JSON 证据](ASSISTANT_DELIVERY_DETAILS_ACCEPTANCE_20261031.json) 保存候选、变更路径和产物摘要。日志位于 tmp_test/assistant-details-*，截图位于 artifacts/assistant-details-*.png，不包含数据库配置或凭据。验收后已停止本任务 25616/25617 隔离集群，保留数据；未修改其他集群或线上。

## 复跑与限制

离线入口：python -m tools.assistant_replay --out tmp_test/assistant-details-new-run --timeout 1200。

PG 入口：python scripts/assistant_delivery_postgres_gate.py --pg16 <本机隔离PG16配置> --pg17 <本机隔离PG17配置> --out tmp_test/assistant-details-new-pg。不得用线上 DSN。

前端执行 npm test -- --run、npm run lint、npm run build；设置 E2E_FRONTEND_MODE=preview 后运行 playwright test，选择 e2e/assistant-delivery-details.spec.ts、e2e/assistant-events.spec.ts、e2e/stage25-assistant-progress.spec.ts。

真实模型效果、生产查询开销/容量、远端 CI、iOS/微信真机未验证。20 行是显式展示上限，不是分页接口。本批仍为固定领域只读取证，没有新增模型自主工具循环、自动指派或正式账务执行。
