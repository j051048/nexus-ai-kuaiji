# P2 本地验收：按需加载、体积门槛及接手来源查询

## 范围与结论

本轮承接五包中的两个 P2 方向：**按测量优化前端**，以及**一次整理一个业务域的查询与服务职责**。完成首页加载边界、构建体积检查和人工接手的精确来源查询；不代表所有历史 P2 债务或生产容量已通过。

基线 HEAD `aba952c60a13d55bb829fed9aa72157e6885234c`。开始工作区已有上一轮 P1 未提交改动，全部保留。本轮继续修改 handoff/service.py、DCP 用例清单及 CI 接线时，只增量整理其实现，保留 P1 的来源绑定、历史改期和通知类型修复。

未提交、未推送、未部署；未调用真实模型或真实通知。未修改 C01、费用/attempt、复核与导出政策。**本轮没有新增或改写 SQL 迁移**；P1 的 213/214 仍保留在工作区，仅在隔离测试库应用。

## 真实问题与修复

### 1. 首页提前加载账套界面

代码原本已对大多数页面使用 lazy，本轮没有重建路由或权限模型。遗漏的 BookWorkspaceShell、WorkspaceHome、NewBookModal 和 ClientAccessTools 改为按需加载，复用既有 Suspense、分块加载失败恢复和角色导航。

- 首页不下载账套页面及其专属 CSS；打开账套才下载。
- 登录外壳不预先携带工作台大组件。
- 开账弹窗加载中可关闭，客户签收入口加载不遮掉整个首页。
- 不预取后台管理或旧账迁移，不增加首屏业务请求。

### 2. 单条接手记录却扫描全所队列

旧 source() 调用 queue()，先读税务、批量、通知和交付全部来源，再找目标。本轮先用失败测试复现：21 个批量积压项时，打开其中 1 条会触发 **21 次账套权限检查**。

现在查询按固定类型和参数化 ID 下推到 SQL，只读取指定来源；同场景为 **1 次目标账套检查**。单笔指派的非税务来源也使用同一目标过滤。队列和精确来源共用适配器及指派状态投影，避免两份业务规则漂移。

`handoff/queries.py` 只承载四类确定的读取语句；服务层仍负责当前企业/账套授权、版本与指纹检查、下一步和指派展示。数据库仍保留 RLS、锁、不可篡改历史及原生写入约束。没有跨请求权限缓存、任意 SQL、通用 ORM 或规则下放。

## 构建测量与门槛

生产构建真实文件大小，单位为字节；gzip 为本地压缩估算，不是线上传输测量。

| 指标 | 修改前 | 修改后 | 变化 |
| --- | ---: | ---: | ---: |
| 入口及静态依赖 JS | 622,861 | 562,856 | -9.6% |
| 入口及静态依赖 JS gzip | 191,519 | 173,854 | -9.2% |
| 入口 CSS | 135,941 | 99,067 | -27.1% |
| 首页（入口＋首页依赖）JS | 622,861 | 589,671 | -5.3% |
| 首页 JS gzip | 191,519 | 184,194 | -3.8% |

证据：`tmp_test/next-p2/red-bundle.json`、`final-budget.json`。不可把入口减少 9.6% 说成完整首页、整站或响应时间减少 9.6%。当前入口仍有 >500 KB 的 Vite 提示，没有提高警告阈值掩盖。

新增 `scripts/frontend_budget.mjs` 根据 Vite manifest 遍历静态依赖闭包，去重统计共享 JS/CSS；拆 vendor chunk 不能避开预算。要求六个低频/岗位模块实际独立输出且不在入口依赖图。缺文件、缺依赖、目录穿越及超额均失败。

预算：入口 JS 570,000、gzip 176,000、CSS 110,000；首页 JS 625,000、gzip 195,000、CSS 145,000。日后变更必须重新测量并评审预算，不能为了绿灯直接调高。

构建预览单次本机诊断：桌面 ready 485 ms、FCP 84 ms；手机视口 ready 233 ms、FCP 72 ms。首页 API 为 auth/config、session、workspace/bootstrap 各一次，没有会计账簿预取。记录在 `preview-final/**/home-metrics.json`。这是虚构数据、本地网络、Chromium 手机视口，不是生产 p50/p95、CPU 解析时间、真实手机或公网性能承诺。

## 先失败再修复

- `tmp_test/next-p2/red-handoff.xml`：3 failed / 1 passed。两种来源读取了无关域；单条查询检查全部 21 个账套条目。原始日志在 `offline-pytest/4ca845aa00f843188a9a63b77acbc437`。
- `tmp_test/next-p2/red-bundle.json`：四个模块未实际拆分，入口原始及 gzip 体积超预算。
- `tmp_test/next-p2/red-browser/`：桌面、手机均复现首页请求 BookWorkspaceShell。
- 预览首轮用例误把 `.css` 算成 `.js`，已更正为“初始不能下载任何账套专属资源、打开后 JS 恰好一次”，保留失败现场；没有放宽产品权限或体积限制。

## 最终回归

| 层级 | 结果 | 证据 |
| --- | --- | --- |
| 新查询＋P1＋既有接手离线 | 26 passed | `handoff-green.xml` |
| 精确 PG 门禁校验器 | 8 passed | `gate-contract.xml` |
| 受限 PG16 | 39 passed，0 skipped | `pg16/summary.json` |
| 受限 PG17 | 39 passed，0 skipped | `pg17/summary.json` |
| 前端全量单元 | 120 文件、1,189 passed | `frontend-tests.log` |
| 构建体积检查器 | 6 passed | 本轮 Node 测试输出 |
| 开发模式首页、撤权、范围与迟到响应 | 11 passed | `dev-browser.log` |
| 构建预览桌面/手机、开户/复核/导出/签收 | 11 passed | `preview-final.log` |
| P1 接手真实本地浏览器回归 | 2 passed | `handoff-browser.log`；同人改期、准确来源、unknown 不重发 |
| 过滤后的 Vercel 构建 | 2 passed | `vercel-build.log` |
| 类型、ESLint、Prettier、Ruff、构建 | 通过 | 本轮输出、`final-production-build.log` |

表内简写证据均位于 `tmp_test/next-p2/`。浏览器累计次数含重复场景，不应汇总成独立业务用例数。浏览器使用本地 API/SQLite；权限负向场景有受控响应，不冒充完整 PG/GoTrue 浏览器链。新增 PG 测试覆盖四类来源、错事务所、旧指纹、版本替代、请求之间撤权、接手人可见性和通知仍 unknown/attempts=1，原有原生 SQL 与并发守卫用例继续执行。

桌面/手机截图位于 `preview-final/`，已检查首页和账套页面。PG 两个专用集群（25716/25717）验收后已正常停止；保留证据及数据库，不删除缓存或其他任务环境。

## 候选、CI 与发布边界

PG16/17 为同一后端候选：source `cc672917183635c6d404b1fbd5a71c833ff73cf16d251922b253de14727bfecb`，migration `44fee7aff4a8679d39ce66e19399e6efad82c57a72248f6227a065ceeba17fa5`，精确用例清单 `e8febbd4f958e3ecb89ef00248ff6a957836538159aacbefc56e05b030d47066`。

完整 `release-candidate-v1`：source `de4b57884d37087e06685db5216fbc26231621157fe9653c52e37e8f172ed14b`（1,846 文件），migration `f10679dae6abf4a4f1651342e041acc8b5039499138bb2b549fb20751e596d59`（333 文件）。算法与 PG 后端门禁不同，不直接比较摘要。生产构建资产摘要 `905bf0f9706d22ce688c4ee8943f840136f341d8317f2fe84292d6127000a4f7`。

CI 配置新增生产构建预算检查、预览模式两视口测试；新增 PG 五项纳入既有双版本精确清单，不能以跳过或遗漏通过。**远端 CI 未运行**；本轮未重跑完整恢复，不能拼接旧候选报告宣布发布就绪。

```powershell
python scripts/test_accounting_offline.py nexus_backend/tests/test_next_p2_handoff_reads.py nexus_backend/tests/test_next_p1_handoff.py nexus_backend/tests/test_next04_handoff_adapters.py nexus_backend/tests/test_dcp05_handoff.py -q
python scripts/dcp_reliability_gate.py --suite dcp --config <本地受限PG配置> --out <新的证据目录>
npm run build
npm run test:frontend-budget
npx playwright test e2e/next-p2-loading.spec.ts e2e/stage20-home-loading.spec.ts
# 构建预览：设置 E2E_FRONTEND_MODE=preview 后运行
npx playwright test e2e/next-p2-loading.spec.ts e2e/accounting.spec.ts e2e/access-scope-policy.spec.ts
```

仍待后续独立安排：公网及慢设备加载对照、长期/多节点容量、其他业务域职责整理、真实模型效果与供应商通知沙箱。没有为本轮扩大连接池、删除数据库守卫、调整授权口径或增加 AI 自主写入。
