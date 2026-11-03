# NEXT P1：批量失败接手、期限变更与精确原记录

本轮仅本地修改、隔离验证，未提交、未推送、未发布、未连接真实模型或通知通道。范围是上一轮 P0 报告列出的三项 P1，不代表所有中长期产品规划均已完成。

## 实际修复

1. **失败项不再因结果为空而消失。** 已复核导出、交付包批次使用服务端冻结计划中的企业和账套；同时核对期间。开账尚未产生账套时保留失败项及原批次入口，明确不可指派，不猜选现有账套。
2. **同一负责人也可调整期限和依据。** 完全相同的重复提交幂等；变更保留旧行并新增记录。新增来源指纹及当前指派 ID 比对，冲突要求重新确认。旧来源的负责人不再冒充当前有效指派，旧指派 ID 保留用于安全替换。审计包含旧指派 ID、期限和来源指纹。
3. **接手入口绑定原对象。** 链接包含来源类型、ID、版本、指纹、企业、账套和月份。服务端重新鉴权；前端原子接纳范围并在领域页面再次核对来源。批量和通知二次读取还检查指纹，来源变化、缺失或撤权不自动替换成最新对象。
4. **复用现有办理页面。** 批量项、通知核查、税务底稿、交付包分别进入对应组件。单项视图不允许误触整批重试；通知 unknown 接手保持“不自动重发”，批量 unknown 提示人工核查。
5. **真实 PG 成功路径补出数据库缺陷。** 通知核查原本被 `handoff_tasks_source_kind_check` 拒绝；新增 214 补齐已有适配器类型，保留原有权限和只增历史约束。213 补充原生 SQL 的冻结账套、企业、期间和指纹绑定，以及旧税务版本禁止指派。

新增 PG 文件：`213_handoff_source_fingerprint.sql`、`214_handoff_notification_kind.sql`。既有 001–212 文件和 checksum 未改；SQLite 表结构未变。两条新迁移仅应用于本轮隔离库，线上尚未执行。

## 红—绿证据

- 最初离线五项失败：`tmp_test/offline-pytest/03e3064cf2924fee9dab2964e5447a26`。
- 来源更新后丢失指派比较依据：`tmp_test/offline-pytest/053f753a834941e78a41c67eab7ec934`，1 failed / 5 passed。
- unknown 批量仍提示重试：`tmp_test/offline-pytest/83859acca7da400fb68c6141cbbd1b13`，1 failed。
- 原生 PG 伪造账套与指纹未被拒绝：`tmp_test/ak05-pg-runs/ebe65005ebab4933946087c87b6bb265`，2 failed / 2 passed。
- PG 通知核查成功路径被类型约束拒绝：`tmp_test/ak05-pg-runs/4c514f9aaa224849b46e2ec3d48c04a3`，1 failed / 4 passed；前向修复后通过。
- 前端测试先复现原记录链接缺失、期限回执未检查、二次读取依据变化未检查，再实现修复。旧 DCP 浏览器夹具返回空期限，修改为真实保存期限并断言请求，不放宽校验。
- 新增 214 在初次本地验证中使用了仓库不接受的兼容标签，结构门禁失败。校正这份未发布的新文件后**重新创建隔离数据库**；旧库和失败证据保留，未修改数据库已登记摘要，也未登记豁免。

## 最终验收

| 验收 | 实测结果 | 证据 |
|---|---|---|
| P1、既有接手、迁移结构及门禁回归 | 40 passed | `tmp_test/next-p1/offline-final.xml` |
| PG16 精确 DCP 门禁 | 34 passed，0 skipped | `tmp_test/next-p1/final-v2-pg16/summary.json` |
| PG17 精确 DCP 门禁 | 34 passed，0 skipped | `tmp_test/next-p1/final-v2-pg17/summary.json` |
| 新增真实本地浏览器流程 | 桌面 1280 / 手机 390，各通过 | `playwright.next-p1.config.ts`、`e2e/next-p1-handoff.spec.ts` |
| 原 DCP 批量与接手浏览器回归 | 4 passed | `e2e/dcp-r4-operations.spec.ts` |
| 前端全量单元 | 120 files / 1,189 passed | 本轮 Vitest 输出 |
| 静态与构建 | TypeScript、ESLint、Prettier、Ruff、迁移结构通过；构建通过 | 本轮执行输出 |

PG 门禁还验证：两会话同人改期只有一条当前指派；原生 SQL 越权、错账套、伪造指纹拒绝；通知接手后仍 unknown 且 attempts=1；213/214 各重复应用两次，历史指派、RLS/FORCE RLS 和权限不变。企业隔离及既有复核、交付围栏回归保留。

两种宽度的浏览器测试使用新建 SQLite + 真实本地 API + 本地测试身份，**没有模拟 API 响应**。验证空结果批次、同人改期、原记录刷新/后退/前进、旧指纹拒绝及 unknown 不重发。它不冒充 PG 浏览器或 GoTrue 全链验证；PG 部分是独立的服务、原生 SQL 与并发测试。

截图在 `tmp_test/next-p1/browser-results/` 的两个视口目录中，分别包含 `assignment-*`、`batch-*`、`stale-*`，已检查桌面和手机实际显示。

Windows 默认 TEMP 的 esbuild 文件删除曾出现 Access denied；仅将本次构建的 TEMP/TMP 指向 `tmp_test/next-p1/build-temp` 后构建通过，没有改系统安全配置。现存主入口体积警告未在本轮处理。

## 复跑与候选

基线 HEAD：`aba952c60a13d55bb829fed9aa72157e6885234c`，开始工作区干净；本轮未撤销、覆盖其他智能体改动。

双版本 PG 最终门禁的同一后端候选：

- source SHA-256：`1532ec3d2a1f3f0fcb00853f7e82d842d40c4f41716e3d600f7bbad2bf14774c`
- migration SHA-256：`44fee7aff4a8679d39ce66e19399e6efad82c57a72248f6227a065ceeba17fa5`
- required-case manifest SHA-256：`ac1e7fcb82e4056de0f786178f7dc42a840718f24036ff4ae06db15d8380c4cf`

完整应用候选采用另一个包含前端的 `release-candidate-v1` 算法，不能与上面摘要直接比较：source `6f4828f74de3560b46fd721b2e50cd77d3fb2ad4bebc73a512d6875337e77066`，migration `f10679dae6abf4a4f1651342e041acc8b5039499138bb2b549fb20751e596d59`。

```powershell
python scripts/test_accounting_offline.py nexus_backend/tests/test_next_p1_handoff.py nexus_backend/tests/test_next04_handoff_adapters.py nexus_backend/tests/test_dcp05_handoff.py nexus_backend/tests/test_migration_structure.py nexus_backend/tests/test_ag00_migration_idempotency.py nexus_backend/tests/test_dcp_reliability_gate.py -q
python scripts/dcp_reliability_gate.py --suite dcp --config <受限本地PG配置> --out <新的证据目录>
npx playwright test --config playwright.next-p1.config.ts
npx playwright test e2e/dcp-r4-operations.spec.ts
npm test
npm run build
```

新增 PG 用例已进入既有 PG16/17 精确清单；专用浏览器配置在 CI 第一分片后运行。普通套件不误收专用环境用例。远端 CI 尚未执行，不能据本报告宣布发布通过。

## 范围边界

- 没有账套的开账失败可见、可核对，但既有接手表要求绑定账套，因此不伪造可指派能力；由事务所负责人在原批次处理。
- 本轮没有改费用结算、模型 attempt、C01 核算或正式凭证复核／导出规则。
- 没有开启真实通知、没有扩展 AI 工具循环，也没有实施独立的凭证纠错向导或环境清理任务。
- 本轮未重新执行完整恢复门禁；发布前仍需对最终提交验证远端 CI、恢复与完整业务浏览器门禁。213/214 尚待获得上线授权后按迁移顺序执行。
- 验收结束已正常停止本轮专用 PG16/17 实例（25716/25717）；数据库文件、失败现场和最终报告保留，未停止其他任务的服务。
