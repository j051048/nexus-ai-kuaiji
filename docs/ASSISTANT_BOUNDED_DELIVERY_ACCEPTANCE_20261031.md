# 助手第五批验收：有界交付取证与人工接手

2026-10-31，**本地工程验收通过**。未提交、未推送、未上线、未连接真实模型或通知。沿用企业隔离、会计复核后导出的控制。

## 本批完成

- 既有交付诊断接入三个固定只读步骤：已选资料、月结条件、交付签收；客户身份只执行第一步，事务所身份继续受账套权限约束。
- 每次投影最多三步、默认十秒协作式预算；步骤前后核查范围和权限，拒绝乱序、重复、超额、迟到结果和前序事实改写。不能中断正在执行的 SQL，不将其描述为硬超时。
- `assistant-delivery-v2` 记录稳定步骤次序、输出摘要与范围；人工接手清单按负责岗位分组，保留每条阻断原因、下一步与引用。`assigned=false` 明确尚未实际指派；`read_complete` 不表示会计复核或可交付。
- 复用现有 ContextPack、事实校验、快照重检、单次模型请求和租约围栏。没有新增模型调用、重试、权限、写操作、数据库表或迁移。
- 既有助手回答显示核对范围和接手清单；不增加导航或前端页面。保留旧 v1 历史渲染，当前快照生成 v2，旧排队快照发生变化仍失效。

## 先红后绿与发现

1. 初始 `assistant-investigation-red.xml`：9 failed。复现缺少分步取证/接手契约、回答缺少相应范围与接手说明。
2. 首轮接线后，新旧诊断组合 31 passed。
3. 预算边界红测：1 failed、28 passed。授权查询自身耗尽时限时，结果仍被接受；修正为授权前后共用同一时限。最终新文件 29 项纳入统一基线。
4. 首轮 PG16：42 passed、1 failed。新用例错误地假设只读 `REPEATABLE READ` 事务能看到其他会话新提交的撤权。这是测试假设错误，不冒充产品缺陷；修正为证明旧私有快照隔离、新事务拒绝访问。原有 worker 发送前、响应中、提交时撤权测试保持不变，最终两版本全部通过。

运行日志均保留，不覆盖失败证据，不删除安全断言使测试变绿。

## 最终同候选回归

| 范围 | 结果 |
| --- | --- |
| 冻结六类任务 + ContextPack/事实/取证/进度/旧 OCR 评测回归 | 329 passed，0 skipped，143.00s |
| 费用、调用限额、Provider、对话与 PG 门槛兼容 | 160 passed，0 skipped，54.29s |
| PostgreSQL 16.15，受限 API/worker | 43 passed，0 skipped，56.06s |
| PostgreSQL 17.11，相同必验清单 | 43 passed，0 skipped，56.97s |
| 构建预览浏览器：执行记录/旧轻量进度 | 7 + 4 passed，零重试，34.5s + 25.3s |
| TypeScript/Vite 构建、相关 Python Ruff/format、diff 检查 | 通过 |

六类安全基线仍为 6/6；正常、缺资料、冲突的回答完整性仍为 3/3。没有调整输入、金标或分母。不同组含重复覆盖，不把次数相加当成独立业务场景。

新增 PG 验证：会计三步/客户一步、每步及接手依据的范围绑定、同一 request 仅一次 attempt/费用、入队前与发送前预算不足均零发送/费用、只读快照的撤权边界。原有跨企业 RLS、版本变化、最终接纳撤权、两进程竞争、发送后崩溃/未知不重发及会计复核后导出继续通过。

浏览器验收是既有助手的桌面 1440px、手机 390px Chromium 视口回归，包括断线续读、切企业迟到响应、403/404 清理、未知结果不重发；本批未改前端。截图已查看并另存；它们证明原进度界面未回归，不冒充新增取证清单的完整浏览器端到端测试或手机真机验收。新增回答及引用由实际 worker + MockTransport 的服务链路验证。

## 候选、归属和证据

- HEAD：`9b4a05c3104ec42979a1d0792348db2643a797a9`，包含本线程前几批未提交改动。
- 完整源候选：`ffeb365ad5a1053bd1a84378fe9d8bbe43364fee0d03afb63b696dadd65e0e42`，1601 个文件。
- 迁移摘要：`26e9be85529136507e36a2fe5b25e7a740274d8679cc635302601d596457275a`，282 个文件，未变化。
- 离线候选 `assistant-bounded-delivery-reads-v5`：`86b9bf8e3a0fd2523793dc84c418d133c295c38df07a70004b31348f76aaa482`。
- PG 必验清单 v4：43 项；拒绝缺失、替换、重复、跳过。

对本轮起点的文件摘要比较，仅 9 个代码/测试/门槛路径改变：助手 `delivery_context.py`、新增 `delivery_investigation.py`；3 个相关测试文件；2 个 PG 门槛文件；2 个离线入口文件。详细路径在 JSON 中。C01、账务内核、费用、worker、权限实现、迁移、前端源码及其他智能体文件未修改。

最终产物：

- `tmp_test/assistant-investigation-baseline-final/`
- `tmp_test/assistant-investigation-pg-final/`
- `tmp_test/assistant-investigation-compatibility-final.xml` 和 `.log`
- `tmp_test/assistant-investigation-browser.log`、`assistant-investigation-browser-progress.log`
- `tmp_test/assistant-investigation-final-candidate.json`
- `artifacts/assistant-investigation-regression-1440.png`、`assistant-investigation-regression-390.png`
- `tmp_test/assistant-investigation-environment-stop.json`

[JSON 验收汇总](ASSISTANT_BOUNDED_DELIVERY_ACCEPTANCE_20261031.json) 记录结果、候选、变更归属和各产物 SHA-256。日志、截图、虚构数据库是本机产物；不包含私有连接或密码。最后一轮冻结候选后复跑离线、PG、兼容与浏览器，产物生成后再次比对一致。

只操作本线程 25616/25617 两个隔离 PG 集群；168 项 PG 迁移 checksum、9 张相关表 FORCE RLS、受限角色和持久化配置检查均通过。验收后专用 PG 和浏览器服务已停止，保留数据与证据，未碰其他集群或线上。

## 仍未验证的边界

真实模型质量、生产性能/容量、远端 CI、iOS/微信真机均未验证。本批是固定领域只读取证，没有引入模型自主工具回灌、摘要模型、SSE、自动任务分派或正式账务动作。新增步骤的生产查询开销仍需后续测量，不能从本机回归耗时推导生产吞吐。

实现契约和复跑命令见 [有界交付取证说明](ASSISTANT_BOUNDED_DELIVERY_READS.md)。
