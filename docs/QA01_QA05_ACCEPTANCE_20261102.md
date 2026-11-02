# QA01–QA05 本地隔离验收

本报告对应 2026-11-02 工作区候选，基线 HEAD 为 `2247af5184ee872f04c0acf15bf29d81c33a9246`。最终机器证据和结果统计在同名 JSON 中。范围为虚构企业、本地数据库、本地身份服务及模拟供应商；未提交、推送、部署或调用真实模型、通知通道。

## 修改与红—绿证据

| 批次 | 修复和验收范围 | 最初失败证据 |
|---|---|---|
| QA01 / P0 | 官方 GoTrue 资产统一到一个版本/架构/摘要清单；下载、桥接、CI 使用相同契约；失败阶段提供脱敏诊断 | `tmp_test/offline-pytest/822e04bf5b024ab193a46626936b895b`：4 failed；修复后相关 30 项通过 |
| QA02 / P1 | 鉴权、原件、OCR/journal/chat 的 504/断连/超时；HTTP 写操作结果未知不声称回滚，不自动重发；泛化错误带安全请求编号 | `4d2e23232f0f439b952fcbfe020e3466`：5 failed；`ba7b84aba86842e48339f3f42e0acc7f`：意外异常返回未受控；前端六项红测 |
| QA03 / P1 | 早期 401/403 也返回 no-store/nosniff；恶意 SVG、伪装 PNG/PDF、XML 外部实体拒绝；原件下载授权、文件名与 sandbox；保留费用、幂等和企业隔离 | HTTP 安全头红测同 QA02；附件原有防护复跑通过，未重复新增解析器 |
| QA04 / P1 | 三种真实浏览器引擎和手机视口；员工录入、同屏复核、批量办理、调查、客户签收；axe 和键盘检查 | 首屏两处对比度失败；`browser-cf-red.json`：59 passed / 3 failed，包括 Firefox 手机长金额裁切及旧 503 文案断言；`mobile-focus-red.json`：WebKit 关闭菜单未回到入口焦点 |
| QA05 / P2 | PG16/17、200 企业、5/20/50 客户并发、多企业/热点账套读写、双独立 worker、PG 锁等待/连接与应用资源测量 | 容量夹具连接池未关闭的失败测试；旧报告逻辑无法区分池准入拒绝和业务失败；空/缺项/失败报告拒绝 |

红测路径未写全时均位于 `tmp_test/offline-pytest/`；跨浏览器红测在 `tmp_test/qa01-05/`。既有测试更新只调整过时契约：企业财务不能充当事务所独立复核员；受控 503 不再显示后端原始错误。不删除安全断言、不降低金额可读性或授权标准。

## 持续门禁

- `.github/workflows/qa-reliability.yml`：故障和安全子集，Chromium、Firefox、WebKit、mobile-webkit 四项目。
- `.github/workflows/p01-recovery.yml`：固定 GoTrue 资产 → 新隔离环境 → 同候选完整恢复 → PG16/17 混合负载与动态并发 → 只停止本轮拥有的资源。
- `scripts/qa_no_skip.py`：拒绝测试阶段 skip、xfail、零用例及收集阶段整文件 skip；不允许“有一个通过、其余全跳过”的假绿。
- `tools/capacity/qa_mixed.py`：只接受受限账号、非默认本地端口、实际 PG16/17 与持久性配置；报告绑定运行前后候选指纹，不能把部分执行伪装为完成。

## 重跑方式

先在原生 Linux/WSL 用 `identity_test_assets.py fetch` 获取固定资产，再用 `p01_local_recovery_env.py prepare/bootstrap/new-pair/bridge-start` 创建独立环境。每次联合恢复必须使用全新源库和空目标库；不要使用已跑过容量夹具的源库。Windows 的 `new-pair` 和门禁使用仓库虚拟环境，原生集群/桥生命周期命令在 WSL 运行。配置与密钥不进入版本库。

```text
python scripts/p01_recovery_gate.py --all --config-dir <新运行目录> --out <新证据目录> --phase-timeout 3600
python tools/capacity/qa_mixed.py --config <独立源库配置> --major 16 --out <新容量目录> --companies 200 --rounds 2
python scripts/test_accounting_postgres_isolated.py --config <独立源库配置> nexus_backend/tests/test_batch1_database_pool_postgres.py nexus_backend/tests/test_ak05_unified_postgres.py -p scripts.qa_no_skip -q
npm run build
npx playwright test --config=playwright.qa.config.ts
```

容量和 PG 动态测试对 17 重复同样命令。离线必跑文件以 QA workflow 为准。Windows 本机使用独立可写 TEMP/TMP，避免系统临时目录的访问拒绝；浏览器安装与验收使用已有 Node 24，CI 固定 Node 22。首轮 Node 26 的 Playwright 解压挂起属于本机工具问题；重用同一官方压缩包及 Playwright 自带解压器完成，没有改浏览器或依赖源码。

## 证据边界

1. 本轮未推送，所以不能宣称远端 Actions 已绿。曾查到的旧失败是官方资产与桥内摘要不一致；本轮通过本地 CI 等价流程验证修复。
2. WebKit/iPhone 视口不等同于 iOS 真机、微信内置浏览器；axe 自动检查不等同于完整无障碍认证。
3. 安全验证覆盖选定的真实 HTTP 应用路径、PG 身份/连接/权限和重放边界，不等同于全站渗透测试、WAF 或托管登录服务防爆破认证。没有为此放开 SVG。
4. 负载报告区分成功延迟与连接池过载拒绝；测得的受控拒绝不等于所有请求完成。应用 CPU/RSS 不冒充数据库 CPU，短时容量基线不冒充长稳或生产 SLA。
5. 首轮恢复拒绝了被容量冒烟写入额外身份的 PG16 源库，检查未被放宽；原运行配置与失败证据保留，使用新的不可变运行目录重跑。
6. 现有临时目录既含故障夹具也含历史验收证据，没有批量删除其他任务的目录。复用容量种子产生的连接池泄漏已修；正常会话模板的清理由现有 conftest 管理，强杀进程留下的材料保留在本轮隔离目录。
7. 后端测试有既存 Starlette/httpx 弃用提示，Vite 主包约 622.5 kB 有分包提示；这些未被隐藏，也没有为本轮大规模替换框架或重写前端。静态检查、构建和选定回归的通过不代表仓库全部历史测试都已执行。

## 容量实测及边界

每个数据库版本 200 家虚构企业，每种并发级别 400 次操作（75% 账套读取、25% 业务导入），分别测试多企业和热点单账套，共 2,400 次请求；随后用两个独立 OS 进程执行 200 家开期。恢复和浏览器套件未与容量试验同时执行。没有把这些数当作 HTTP 端到端响应、50 人真实使用模型或生产 SLA。

| 项目 | PG16 | PG17 |
|---|---:|---:|
| 5 / 20 并发，多企业和热点请求失败 | 0 / 0 | 0 / 0 |
| 20 并发多企业读 / 写 p95 | 310 / 567 ms | 195 / 332 ms |
| 20 并发热点读 / 写 p95 | 571 / 879 ms | 1,354 / 1,994 ms |
| 50 线程突发，多企业准入拒绝 | 364 / 400 | 365 / 400 |
| 50 线程突发，热点准入拒绝 | 364 / 400 | 364 / 400 |
| 双进程 200 家开期 | 33.399 s | 32.012 s |
| 两 worker 处理数 | 100 + 100 | 100 + 100 |
| 新账套 / 重复账套 | 200 / 0 | 200 / 0 |
| PG 活动采样数 / 最大连接 / 最大锁等待者 | 826 / 12 / 1 | 913 / 12 / 1 |

**QA05 测量与守恒验收通过，50 线程突发全部请求完成的容量目标未通过。** 默认池容量和等待队列构成明显边界；拒绝的操作已逐笔核实没有写入，不把快速失败计入成功延迟。下一步应对入口背压、等待预算与多实例总连接预算做独立优化，并复测；不能简单放大连接池或自动重发结果未知的写请求。热点 p95 的波动还需要稳定环境中的长稳测试，不能据这次顺序测试断言某个 PG 版本性能更好。

## 最终结果

本地工程与指定回归收口完成。候选源 SHA-256：`91e5177b25f88c68a22a733ec8ce5a9f5c75d3a139140dfb1f382b300b4cb197`；1787 个源文件；316 个迁移文件的摘要保持基线值 `f07ba1cdba14400bfff08d8f01df8217c917304da93be1da858814e39e3f8dc8`。

| 验收 | 实际结果 |
|---|---|
| 后端故障、安全、费用/attempt、worker 回归 | 299 passed，0 skipped |
| 前端单测 | 119 files / 1174 passed |
| 浏览器 | 96 passed，0 skipped，0 flaky（31 Chromium + 31 Firefox + 31 WebKit + 3 mobile-webkit）|
| PG16 / PG17 动态连接池、权限、并发与停机 | 各 10 passed，0 skipped |
| 同候选完整恢复门禁 | 151 regression + 2 joint + 4 identity，通过 |
| TypeScript、ESLint、Vite build、本轮 Python lint/format | 通过；保留已说明的非失败警告 |
| QA05 | 两版容量测量与守恒通过；50 线程突发全部请求完成目标未通过 |

最终恢复证据：`tmp_test/qa01-05/accepted-recovery-final/reports/summary.json`，独立配置运行目录 `568440a13f794e53a41bdb76d8bb2c80`。最终浏览器汇总：`tmp_test/qa01-05/browser-accepted.json`。容量证据：`tmp_test/qa01-05/accepted-capacity16/report.json`、`accepted-capacity17/report.json`。机器汇总同名 JSON 校验这些输入的状态、数量、候选指纹及 SHA-256，不手工拼接成假通过。

期间候选变化、被使用过的运行目录、混入其他夹具身份的源库均被门禁实际拒绝；失败证据仍保留。最终结论来自新的完整调用，不修改历史失败记录。其他智能体原有文件字节保持基线；本轮修改归属见机器汇总。没有提交、推送、数据库历史迁移改写或生产操作。

本轮资源已收尾：四个自建隔离集群均为 stopped，身份桥接端口无监听，数据与不可变运行证据保留。第一次 bridge-stop 报超时，复查时桥已停止，重试返回 already_stopped；保留两次结果，没有强杀其他任务。状态证据为 `environment-final-status.json` 和 `bridge-stop-retry.json`，纳入同名 JSON 的校验清单。
