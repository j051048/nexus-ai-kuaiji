# V10：同一候选的开发、预览和 CI 验收

本轮只收敛本地构建与验收工具，不修改业务、C01、迁移、授权、审核或导出实现，也不连接真实模型及线上服务。基于 `1ccc480413e75cd2dff280ba725490fbfd73cbe7` 上已验收、尚未提交的 V07–V09 候选继续；这些既有业务修改不重新归为 V10 实现。

## 统一入口

安装锁定依赖及本地测试依赖后，确保 `python` 指向安装过 `nexus_backend/requirements.lock` 的环境，且本机已安装 Playwright Chromium：

```text
npm run test:e2e:gate
npm run test:e2e:core
```

- `test:e2e:gate`：门禁自身的负向测试，包括一次真实 Playwright 的纯断言测试，证明预期失败、跳过和重试成功即使退出 0 也不能被接收。此测试不打开浏览器、不启动业务服务。
- `test:e2e:core`：按 dev → preview 顺序运行同一候选的全部 95 个冻结浏览器用例。完整通过必须两个模式各自通过，不能将两轮残缺结果拼接。
- `test:e2e:core:dev` / `test:e2e:core:preview`：独立诊断入口，只能证明相应模式；不能替代双模式门禁。
- 原来的 `test:e2e`、全量 e2e CI 分片、后端 verify、AK05 PostgreSQL 16/17 门禁继续保留。

正式核心命令不透传 grep、shard、repeat、project 等缩减覆盖参数。固定 1 worker、0 retries，拒绝 `test.only` 和 flaky。改变清单必须经过人工审查，不能从当前失败结果重新生成门槛。

## 配置与隔离

- 普通 `npm run dev` 和 `npm run preview` 统一使用 `127.0.0.1:8080`、严格端口及相同 API 代理；preview 原默认端口 4173 改为 8080。两者不能占用同一端口同时启动，可显式 `--port` 指定其他本机端口。
- 普通开发仍可设置 `ACCOUNTING_API_PROXY`，默认指向 `127.0.0.1:8000`。Vite 7 原本会继承 preview 代理，这里是显式固化，不是修复已证实的“preview 无代理”。
- 验收固定前端 `127.0.0.1:18080`、API `127.0.0.1:18000`，不接受外部地址；已有服务占用端口即失败，不复用。
- 两模式使用同一个 `playwright.config.ts` 和 `resolveE2ERuntime`。每次启动分配 UUID SQLite 及附件路径；本地认证、文件存储和虚构种子数据。
- 清除继承的模型、数据库（包括独立投影连接）、云存储、Supabase、邮件和代理配置；数值配置覆盖为合法测试值。仅 E2E 子进程使用 `E2E_LOCAL_ISOLATION=1` 令 Vite `envDir=false`，不读本机 `.env` 文件。
- preview 启动先执行 build，成功后才启动服务；失败不能读取旧产物。浏览器开始和结束分别核对整个 `dist` 指纹。请勿在验收期间并发修改该候选或运行其他 build。
- runner 为子进程分配独立 TEMP/TMP/TMPDIR，避免依赖 Windows 系统 Temp 的偶发权限状态。不修改用户的系统环境或权限。

## 固定门槛和证据

`scripts/e2e_core_cases.json` 来自已验收 V09 的完整 95 个测试身份，覆盖 18 个文件；包含 V07 14 项、V08 10 项、V09 10 项及其他相关流程 61 项。通过条件是逐项身份和执行结果，不只检查总数。

覆盖员工提交原件、独立审批、企业财务核验、会计同屏复核、未审禁止导出、人工复核后导出、月度服务单、企业/账套/期间范围、撤权与错误恢复、岗位导航、待办语言、多层返回来源，以及桌面/手机视口。

每次输出到全新的：

```text
test-reports/e2e-core/<suite UUID>/reports/dev.json
test-reports/e2e-core/<suite UUID>/reports/preview.json
test-reports/e2e-core/<suite UUID>/reports/summary.json
```

报告包含文件+完整标题、每次 attempt、预期状态、实际 outcome、退出码、运行 ID、清单与源码指纹，以及 preview 产物前后指纹。通过要求：每个身份恰好一次、预期和实际均 passed、retry=0、无全局错误、无缺失/额外/重复身份，报告属于本次运行，运行前后源码未变。

源码指纹覆盖 Git 跟踪文件和未忽略的新文件，包括未提交修改；临时数据库、构建、报告和其他忽略输出不在源码指纹中。preview 构建产物单独核对。验收期间编辑文档同样会改变候选，必须重跑，不能合并旧结果。

`runtime/` 是本地诊断材料，不是可发布证据。CI 只上传 `reports/*.json` 白名单投影，不上传原件、数据库、trace、错误响应正文或整段控制台日志。历史用例的截图路径暂沿用原位置，本轮不以截图文件总数充当验收证明。

## 失败后处理

1. 先查看当前 suite 的 summary 与对应模式 JSON；不要采用上一次目录的通过结果。
2. 端口冲突：确认占用者，给当前工作安排独立时段；不自动终止别人的服务。
3. 缺 Python/依赖/Chromium：完成本机依赖准备后重新运行；不要配置线上地址来“绕过”启动问题。
4. 身份缺失、重复、expected failure、skip、retry、源码/产物变化：修复原因后完整重跑，不能降低验收清单。

## 先失败再修复与边界

本轮先保存配置差异、继承环境与运行路径碰撞的失败测试；再实现共享配置。新增门禁先建立失败测试，再实现缺失、伪绿、旧报告、源码变化与产物变化的拒绝逻辑。补充了 CLI 导出路径的实际失败复现，以及 Vite 环境文件隔离的先红后绿验证。工作日志在本候选忽略目录 `tmp_test/v10/`。

CI 新增 `core-regression-gate`，执行上述相同命令并保留净化证据。本地运行不等于远端 CI 已执行，也不等于已配置 GitHub 分支保护。

验收范围是本地 SQLite、虚构身份、部分受控网络故障和浏览器视口。不能据此宣称通过生产 Supabase/RLS、PG 并发、真实模型、线上部署或手机真机验收。业务控制仍以原有服务端授权与会计复核为准。
