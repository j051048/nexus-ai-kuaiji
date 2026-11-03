# NEXT P0 本地收口验收（2026-11-03）

## 结论与范围

本轮确认的 P0 已完成修复及本地动态验收；**尚未提交、推送或发布，不能据此宣布远端 CI 或生产验收通过**。未调用真实模型、真实通知，未访问或修改线上数据库。

完整恢复、N02/N03 与浏览器验收绑定同一候选：

- HEAD：`92e7ca1c351697258f4fad97d44714e1cfd3edb2`
- 源码 SHA-256：`7bf86f28c98ef7d0d1b5d41eeca3a743e30b9ab766b876f171c296282b8029cd`
- 迁移 SHA-256：`75155544c340f693cb2da327ef90112183ba66b3bf22ddee62ec54e07ab08d32`
- 应用候选包含 1,830 个源文件、331 个迁移文件；PG 最高编号 212、SQLite 最高编号 131。编号不等于迁移文件数量，PG 台账的 200 条不是“只到 200”。

## 修复内容

| 问题 | 修复与约束 |
| --- | --- |
| 连接参数被当成 SQLite 文件名，可能把凭据写入路径 | 数据库初始化及测试模板复制之前统一验证地址；拒绝 libpq 关键字连接串、错误协议、空白及控制字符，错误不回显输入。增加提交／公开产物的私有路径检查。该检查不是完整的内容级密钥扫描。 |
| 回调异常输入可能触发 500 或无界读取 | 校验签名格式；拒绝深层、重复键、非有限数 JSON；流式读取时执行 64 KiB 上限；数据库处理移出异步事件循环。 |
| 回调事件与通知的企业、账套归属缺少数据库约束 | 新增 PG 211／SQLite 131；原生 SQL 写错 firm/company/book 必须失败。现存非法记录阻止迁移，不擅自修补历史。 |
| 同一通知事件并发到达触发唯一键异常 | 实际 PG 两会话测试先失败；改为原子插入与冲突后内容核对。同内容返回重复，不同内容 409；不触发重发、状态推进或客户签收。 |
| 新业务表未进入恢复证据闭环 | 恢复夹具真正写入税务办理记录、回执原件、通知通道事件、人工接手事项；恢复清单及解除 hold 的内容指纹包含这些表；原件引用增加 filing_id。 |
| 税务回执上传在 SQLite 可行、PG 失败 | 完整恢复先复现用途约束失败；新增 PG 212，补充用途及 consumed binding 的数据库契约，保留既有六种用途和权限收口。 |
| 历史迁移标签／默认浏览器收集阻断 CI | 不修改 205–207／SQLite 129 的历史字节和 checksum；为其登记精确摘要例外，运行时仍按 breaking 处理。默认 SQLite 浏览器套件不再误收私有 PG 身份场景，改由独立真实 PG 门禁执行精确 8 项，失败、跳过、遗漏、替换都拒绝。 |
| 格式门禁与生成物扫描误报 | 只格式化既有报错文件；Python 文件 AST 与基线相同；排除 `.vercel` 生成缓存，不排除生产源代码。 |

## 红—绿证据

以下路径均为本机私有测试目录，未上传：

- `tmp_test/offline-pytest/8c518e10c6684c52a82d35cc055c54bb`：边界缺陷，修复前 14 failed / 3 passed。
- `tmp_test/offline-pytest/e1729a10b2cf44cdb4852687408c6b6e`：原生回调归属写入，修复前 3 failed。
- `tmp_test/offline-pytest/dfdc7d8b963a4d4c97bd01aa0d443824`：私有路径与发布契约，修复前 5 failed / 4 passed。
- 首轮实际 PG 两会话重复事件测试出现 2 项 UniqueViolation；修复后双版本均通过。
- `tmp_test/next-p0-recovery-r1`：181 项基础回归通过，但两项联合恢复因 PG 税务上传用途缺失失败。保留失败现场，没有覆盖为绿灯。
- `tmp_test/next-p0-recovery-r2`：前向修复后在全新源／目标库重新执行，全部通过。

## 本轮实际通过的验收

| 验收 | 结果 | 证据 |
| --- | --- | --- |
| 原 CI 离线组＋通知／税务／迁移回归 | 239 passed | `tmp_test/offline-pytest/49a01fbc707b4a7e9d6031a2872f1c77` |
| P0 边界、浏览器收集及恢复契约 | 59 passed | `tmp_test/offline-pytest/32bf6878dc884d948d85d8525dd5e103`；此为 PG 212 前的补充证据，不替代最终恢复门禁 |
| PG16、PG17 新增回调／税务上传专项 | 各 6 passed，0 skipped | `tmp_test/next-p0-pg16-r2.xml`、`tmp_test/next-p0-pg17-r2.xml` |
| C01 精确用例门禁 | PG16、PG17 各 34 passed | `tmp_test/next-p0-gates-pg16/c01/summary.json`、PG17 对应目录 |
| N02/N03 精确用例门禁 | PG16、PG17 各 101 passed（45＋56），0 skipped | `tmp_test/next-p0-gates-pg16/n02-n03/summary.json`、PG17 对应目录 |
| 同候选完整恢复 | 181 基础回归＋2 联合恢复＋4 真实 GoTrue 身份测试，0 skipped | `tmp_test/next-p0-recovery-r2/reports/summary.json` |
| 桌面／手机业务浏览器 | 8 passed，0 skipped | `tmp_test/next-p0-browser-local/summary.json` |
| 前端单测 | 119 files / 1,186 passed | 本轮完整 Vitest 执行输出 |
| 静态检查与构建 | TypeScript、ESLint、Prettier、Ruff check/format、迁移结构／幂等、公开路径检查通过；过滤后 Vercel 构建 2 passed | 本轮实际执行输出 |

C01 沿用其冻结的旧版后端指纹算法，摘要与完整 release-candidate-v1 不同，不应直接比较摘要字符串；其执行期间源文件未变。N02/N03、完整恢复及浏览器使用相同的完整候选指纹。

完整恢复覆盖：新空 PG16／17、原件逐个 hash、身份恢复及 MFA、RLS／列级授权、重新登录、跨企业与低权限负向、业务写入、独立复核后导出、包版本签收、未知通知不重发。新增税务原件按恢复后的归属和版本可取，通知事件保留为通道证据，不冒充客户签收。

浏览器 8 项中，6 项使用真实本地 GoTrue／API／PG 进行月结、报表发布、交付签收和隔离校验；2 项使用受控响应专测初始 403 与撤权后的迟到写入。原始日志和截图保留在私有目录，CI 只上传脱敏摘要。

## 协作与环境

- 开始 HEAD 为 `154537f7`。执行期间其他任务产生文档提交 `92e7ca1c`，已保留；本轮没有替他人提交、推送或撤销修改。
- 保留 `docs/CI_OCR_BOUNDARY_20261102.md`、`docs/CI_REPAIR_20261103.md`、`docs/NEXT_PHASE_DECISIONS.md` 原有工作区变动。
- 纯格式文件：auth/supabase.py、handoff/service.py、tax/filings.py、tax/router.py、scripts/next03_security_probe.py、scripts/next_harness.py；前端 HandoffDialog 与 batch-operations 相关文件只做格式化。
- 按用户明确授权停止了旧 NEXT02 测试桥；未删除旧数据。本轮使用独立环境、每次失败重试生成新的不可变运行目录。
- 已停止本轮 API、前端、身份测试桥及四个 PG 集群（25496–25499），保留数据、红测和恢复证据。
- Windows 下 Python 启动的 Vite 子进程遇到 SWC 原生缓存 ACL 错误，自动包装器的失败报告保留在 `tmp_test/next-p0-browser-r1`。没有放宽 ACL 或关闭安全校验；改用独立启动的本轮自有前端／API，执行同一专用配置、同样 8 项浏览器测试并校验候选指纹后通过。CI Linux 包装器接线已完成，远端执行仍待授权推送后验证。

## 发布仍须满足

1. 审核并仅提交本轮相关文件；重新冻结最终提交候选。
2. 获准推送后，验证该提交的远端 PG16／17、完整恢复与浏览器门禁。不要沿用旧提交 CI 状态。
3. 生产备份、迁移、服务更新仍须单独授权和受控计划；本轮没有执行。

P1 的批量失败投影、接手期限更新、精确版本深链接等后续优化未混入本轮 P0 修复。
