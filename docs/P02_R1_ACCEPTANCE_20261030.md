# P02-R1 非零月结、客户签收与普通复核员权限验收

日期：2026-10-30。基线 HEAD：`8a21224d0eb69a613702bb8bdd4ea1b67f70c2db` 加工作区修改。
仅本地隔离 PostgreSQL、虚构身份与原件；未提交、推送、上线或调用真实模型/通知。

## 范围与结论

银行覆盖核验、子账检查、正式月结预检/独立复核/锁期、报表复核/发布、版本化资料包与客户签收已接通。普通事务所复核员可配置当前授权企业的合法收件人，不用提升管理员权限。
最终状态、测试与文件摘要见 `P02_R1_ACCEPTANCE_20261030.json`。本报告不计入 P03/P04/P05 完成度，不等同于远端 CI 或发布批准。C01/P01 完整恢复及同候选发布检查仍是发布前置门槛；本轮复跑相关 C01 业务/并发和恢复边界，不把上轮完整恢复冒充新迁移后的恢复验收。

## 实际修复

1. `b04_close/service.py` 不再把子账阻断降为 `rows=[]`，保留配置/适用性失败原因；各检查带处理岗位、下一步和准确账套/月份目标。岗位文字不授予权限、不虚构人员指派。
2. `delivery/followups.py` + PG179：普通复核员查询他人成员行受 RLS 隐藏，旧实现误报“不是当前成员”。改用当前账套复核权限约束的布尔核验；不开放成员目录，发送仍重检冻结联系人和当前资格。
3. PG180：普通会计看不到已绑定冻结期初，正式记账误报 409。增加准确企业/事务所/长期账套/物理账套/期间、有效绑定、冻结及指纹一致的只读投影；科目仅当前月份必要版本和正式引用版本。未绑定期初、他企、无关未来版本仍不可见，不增加写权限或 ledger_manager 身份。
4. `reports/service.py` 原先按 current_version 识别现金及对方科目，未来版本会影响当前报表。复用正式账簿的按月份解析；公式和历史冻结数据不改。
5. 正式账簿内新增可折叠月结面板，显示银行差额/未匹配/覆盖证据、原件 hash、子账差额、冻结三张报表内容及校验，接通独立核验、预检、复核、锁期和发布，再复用已有资料包/签收。未重做导航。
6. 挂载组件红测复现并修复：403 使加载状态无法结束；撤权后迟到成功响应覆盖错误状态。旧 legacy_unverified 覆盖只显示诊断，不能被当成可核验新原件。

预检 HTTP 201 保留：检查记录已创建，不代表检查通过。前端依据 preflight_failed/pass 等状态开放操作，失败不能锁期。内部记账、外部回执、客户签收为不同状态轴。

## 非零业务全链

8 类身份：负责人、制单会计、复核会计、员工、部门审批、企业财务、客户签收、他企员工。普通会计非管理员；专项进一步撤去复核人长期账套管理权，验证普通会计均能办完。

真实签名 HTTP/PG：员工上传独立 PDF → 部门审批 → 财务核验 → 离线固定模型建议及事实校验 → 独立复核 → 正式期初冻结/记账 → 应付事项与核销 → 银行 CSV 导入匹配 → 覆盖原件/批次/hash 绑定及独立核验 → 审核后导出与合成外部回执 → 月结锁期/报表发布 → 资料包下载 → 客户准确版本及 manifest 签收。

原 PDF 独立定义，经真实 PDFium 提取，不从模型输出生成原文；固定模型不联网。

| 项目 | 元 |
|---|---:|
| 银行期初 / 应付期初 | 各 10,000.00 |
| 本期费用及应付增加 | 268.10 |
| 本期付款及应付核销 | 268.10 |
| 银行期末 | 9,731.90 |
| 应付期末 | 10,000.00 |

期初没有伪造成当期银行流水。银行原件只有本期付款。

浏览器用独立桌面/手机企业，从已准备好的非零银行和子账数据开始，实际本地 GoTrue 密码登录，真实 HTTP/PG 点击原件核验到签收。只将合成身份域名映射至本机 GoTrue，不伪造业务响应。员工至草稿前半段由 HTTP 覆盖，不冒充全流程浏览器录入。

负向：自审403、未审导出409、跨企业404、错账套/期间/原件/hash/金额拒绝、旧版本/自行核验拒绝、锁期后覆盖拒绝、代签403、错误manifest409。合法路径在负向断言之后仍可完成。

## 先红后绿及回归

| 缺陷 | 修改前证据（tmp_test 下） | 修改后 |
|---|---|---|
| 子账原因丢失 | p02-diagnostics-red.xml | 49 passed |
| 普通复核员配置收件人 | p02-notifications-pg16-red.xml，2 failed | 专项14及最终双版本组合 |
| 普通会计已绑定期初不可见 | p02-plain-accountant-red.xml；p02-formal-read-pg16-red.xml | PG180后双版本正式账/非零链路 |
| 未来版本影响报表 | p02-formal-report-pg16-red.xml | 双版本期间解析与金额断言 |
| 403/迟到响应 | 两条挂载浏览器用例先2 failed | 最终业务+边界6项 |

最终后端矩阵：

| 组 | PG16.15 | PG17.11 |
|---|---:|---:|
| C01剩余/辅助维度/银行证据/原生SQL复核/真实两会话报表竞态 | 72 passed，0 skip | 72 passed，0 skip |
| P02正式事实/报表/非零HTTP、收件人、N03通知、月度包 | 46 passed，0 skip | 46 passed，0 skip |
| 合计 | **118 passed** | **118 passed** |

JUnit：`tmp_test/p02-final-c01-review-pg16-green.xml`、`p02-final-c01-review-pg17-green.xml`、`p02-final-flow-notifications-pg16.xml`、`p02-final-flow-notifications-pg17.xml`。

并发实测先观察数据库阻塞，再提交政策变更，旧报表复核/发布409且无部分提交。旧测试入口有历史固定集群路径、缺依赖 fixture、过时适用性初始化，初次出现skip/error，不计通过。新增适配复用原负向断言，在现行正式期初/适用性服务上构造真实零业务竞态，未修改 C01 或伪造预检结果。

其他：恢复边界15、通知/迁移结构20、迁移幂等31、报表/账簿/发布/迁移28通过；前端104文件/1076测试通过，lint/typecheck/build通过；本轮11个Python文件ruff/format及git diff --check通过。组间有交集，不叠加为唯一测试总数。存在既有TestClient弃用警告。

## 复跑

配置仅本机保存，不提交连接字符串、密码、令牌或浏览器账号文件。先准备显式本地隔离 PG16/17、四角色和迁移至180；不得指向线上。

```powershell
.\.venv\Scripts\python.exe scripts/test_accounting_postgres_isolated.py --config <本地配置> nexus_backend/tests/test_c01_remaining_postgres.py nexus_backend/tests/test_c01_bank_evidence_postgres.py nexus_backend/tests/test_c01_auxiliary_postgres.py nexus_backend/tests/test_p02_review_gate_postgres.py -q --tb=short
.\.venv\Scripts\python.exe scripts/test_accounting_postgres_isolated.py --config <本地配置> nexus_backend/tests/test_p02_formal_read_postgres.py nexus_backend/tests/test_p02_reviewer_notifications_postgres.py nexus_backend/tests/test_n03_notification_postgres.py nexus_backend/tests/test_monthly_delivery_postgres.py -q --tb=short
```

浏览器使用 `p02_browser_server.py` 的全新输出目录，保持旧证据不重复消费已签收数据。`playwright.p02.config.ts` 专跑真实PG/GoTrue与挂载边界；缺fixture失败，不静默skip，也不混入常规SQLite套件。最终运行与截图摘要见JSON。

## 明确边界

- 旧月度财税简报仍依赖 `periods.closed` 和旧草稿/月结基准，尚未改为正式报表投影。正式月结锁期后客户可能看到旧简报不可用提示，但独立资料包、正式三表、下载和签收正常。本轮不隐藏提示或混用两条状态/金额轴。
- 银行主数据、控制科目及核销准备复用现有API；本轮新增核验至发布界面，不声称新增完整主数据编辑UI。
- 旧冻结报表不自动重写。受原current_version错误影响的未发布报告需重新生成复核；已锁历史必须明确修订处置。
- 客户签收不是外部正式过账或税务申报。无真实模型质量、通知渠道、大规模容量、Safari/微信真机或远端CI结论。
- PG179/180只在本地隔离库执行；发布前重核迁移依赖及完整恢复，不覆盖历史checksum。
- P01仍欠CI联合入口、不可变私有配置归档、单测自动绑定候选、可移植路径和环境生命周期。旧停机证据证明的是当时状态；本轮为P02明确重启本机集群，当前监听不等于当时未停止。

## 修改归属

只改P02月结诊断、收件人权限、正式只读投影、期间报表解析、前端接线和相应验收。P01/C01既有未提交修改、DeepSeek的test_batch_review_postgres.py、旧test_p02_business_flow_postgres.py及P02–P05报告保留，未重置、暂存、覆盖或提交。整个候选摘要包含实际工作区；P02自有文件另列，不能把其他任务修改算成本轮成果。
