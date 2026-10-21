# R5 UX-R2B 本地验收

日期：2026-10-20。仅合成数据、本地隔离 SQLite、浏览器拦截及 fake identity adapter；未访问线上、未发送邮件、未接真实模型、未执行生产迁移。

本轮 UX-R2B 指定本地验收矩阵通过：**41 项浏览器通过、0 跳过、0 flaky；新增后端 6 项通过**。最终浏览器使用 Node 24、单 worker、`retries=0`，不是拼接数次通过结果。

## 范围和证据类型

- 基线为 `baf3f414` 的隔离候选；测试环境位于 `tmp_test/r5-local-a49c82f23b/`，API / Vite 仅绑定 `127.0.0.1:20080` / `20081`。
- 主工作区并行 AG、C01、PG / projection 修改不复制到候选，不纳入本验收。
- 浏览器阻断非 loopback 外联；身份及故障请求由本地夹具返回。Python 进程使用 loopback socket guard。
- 浏览器模拟 401 / 403 / MFA / 409 / 503 / 断网是前端恢复验证，不是生产身份供应商或真实网络供应商验证。
- 新后端开户测试调用真实 `AdminIdentityService`、目录服务和 SQLite，替换的仅是身份供应商。不是只 mock HTTP POST 成功。
- 新浏览器批量复核测试调用真实本地 HTTP 服务和 SQLite；预览、复核、补件、导出及权限检查没有 mock。

## 先红后绿及保留能力

已复现的失败：

| 场景 | 红测结果 | 修复责任 |
| --- | --- | --- |
| 手机批量开期 409 | 输入和提示保留，但 dialog 的 accessible description 为空 | 主任务修复产品弹窗错误关联 |
| 三个事务所工具页面 | 测试点击折叠的旧导航按钮，等待 60 秒失败 | 测试辅助改为真实“更多工具”导航 |
| 密码恢复回跳 | 测试硬编码端口 18080，与实际隔离 baseURL 20081 不一致 | 从 Playwright baseURL 构造回跳 URL |
| 离开会计工作区取消慢读取 | 点击已经不在主导航的“我的待办”，等待 90 秒；未释放夹具导致清理再超时 | 使用真实品牌返回链接，finally 释放阻塞响应 |
| 刷新后员工草稿恢复 | 切换身份只等旧壳可见；只等 session 也不足，profile 到达前仍是通用壳 | 监听本次 token 对应的既有 bootstrap/profile，按当前 `can_accounting` 等待真实角色壳；不新增读取 |
| 夹具重复运行 | 固定事项名产生多个选择结果；固定企业税号被归档历史的唯一约束拒绝 | 测试事项名和税号使用每次唯一 tag；创建后真实搜索再进入企业；不删除历史或放松约束 |

红测日志：`ux-red.log`、`ux-red-cancel.log`。未把导航夹具失败写成产品缺陷。

新后端 6 项和新浏览器 3 项初次执行即通过，属于补齐已有能力的验收证据，没有为制造红绿而破坏产品代码。首页请求数测试移除条件跳过，对固定会计身份直接断言“客户账套”入口可用。

## 新增后端验收

`nexus_backend/tests/test_r5_workflow_acceptance.py`：

1. 两个账套批量复核部分成功；补件后旧命令仍返回原回执；新预览只选失败项；重复确认不重复审核、不自动导出。
2. 预览后的版本变化阻断复核；撤权后新旧确认均不能写入，没有新增审核审计。
3. 未审拒绝导出；独立会计确认后导出金额准确；同键导出幂等；跨企业拒绝；已导出资料冻结；事实变化后的旧预览和导出被拒。
4. 员工、企业财务、企业管理员分别开通 A / B 企业账号：部门和角色准确持久化、无事务所权限泄漏、只能看到本人企业、不能写另一企业；撤权后重放开户不恢复权限、不重复调用身份供应商。

初次本地执行：`6 passed, 1 warning`。warning 为现有合成 JWT 夹具短 HMAC key；没有真实凭据。日志：`r5-backend-initial.txt`。

## 浏览器验收矩阵

复用并实际执行已有 MFA、后台账号、助手、同屏复核、员工录入、首页加载及权限测试，新增：

- 待办 503 / 断网后显示错误、不误登出、不伪造空结果；恢复后手动刷新成功。
- 手机 409 保留输入、错误可访问关联、Escape 关闭和返回焦点。
- 真实本地两账套批量复核部分成功，补件后仅重试失败项；成功项版本不变，无自动导出；会计复核后显式导出，同键重试与跨企业拒绝。

首轮组合矩阵 `39 passed / 1 failed`，唯一失败为上表身份切换辅助同步问题，已通过失败追踪定位；不是业务资料丢失。修复后复跑手机 409 为 `1 passed`，错误关联、关闭返回焦点与无横向溢出均有浏览器证据。

再次执行原员工录入测试遇到固定测试事项名重复：上轮失败遗留的草稿和本轮新草稿同名，Playwright 严格选择器拒绝任取一条。已将事项名唯一化，截图保存在各测试 `testInfo.outputPath`。后续组合的固定税号重复和 profile 同步问题也按上表修复，重跑使用 `retries=0`，不以重试掩盖失败。

最终完整运行：`41 passed (1.2m)`，JSON 记录 `expected=41, skipped=0, unexpected=0, flaky=0`、耗时 `69730.092 ms`。

| 测试文件 | 通过数 | 主要证据 |
| --- | ---: | --- |
| `firm-mfa-recovery.spec.ts` | 2 | 原地二次验证、表单保留、禁止自动重放 |
| `p0-batch-partial-ui.spec.ts` | 1 | 批量部分成功逐项回执 |
| `p0-request-count.spec.ts` | 1 | 固定会计角色、首页一次 bootstrap、账套按需读取 |
| `r5-acceptance.spec.ts` | 4 | 503、断网、手机 409 和焦点、真实本地批量复核与显式导出 |
| `stage12-inline-review.spec.ts` | 1 | 同屏修改使旧预览失效、手机原件与返回保留进度、员工拒绝访问 |
| `stage12-intake.spec.ts` | 2 | 上传、刷新恢复、部门审批入口、手机提交返回 |
| `stage18-admin.spec.ts` | 8 | 企业生命周期、企业管理员范围、并发修改、开户重试、恢复回跳、MFA |
| `stage19-assistant-polling.spec.ts` | 7 | mock 对话、A→B、迟到响应、取消、撤权、翻页和停止轮询 |
| `stage20-home-loading.spec.ts` | 9 | 首页读取量、取消慢请求、实时授权、bootstrap 失败与兼容边界 |
| `verify-firm-access.spec.ts` | 6 | 三个工具页面、403 清除旧数据保留页面、真正 401 退出、旧 worker 字段不泄漏 |

最终日志：`tmp_test/r5-local-a49c82f23b/ux-accepted.log`；结构化报告：`ux-accepted.json`；浏览器产物：`ux-accepted-results/`。

运行命令（工作目录为隔离 candidate，配置不启动主工作区服务）：

```powershell
& 'C:/Users/Fei/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node.exe' `
  '<repo>/node_modules/@playwright/test/cli.js' test `
  --config '<repo>/tmp_test/r5-local-a49c82f23b/playwright.ux-accepted.config.ts'
```

后端运行使用 `.venv/Scripts/python.exe`，`PYTHONPATH` 仅为隔离 guard、candidate 与 candidate/nexus_backend，`-p no:cacheprovider --override-ini=pythonpath=`，测试目录及 basetemp 均在隔离根目录。

## 最终测试文件 SHA-256

以下为主工作区提交候选源文件；隔离浏览器副本仅将 spec 的 Playwright import 替换为本地外联阻断 fixture，业务断言未更换。

| 文件 | SHA-256 |
| --- | --- |
| `e2e/r5-acceptance.spec.ts` | `B2CD3723CFA1EE922DD94B020F72A07429BF3BD76872A0EB6A4ABFB677F0BC1C` |
| `e2e/ui-navigation.ts` | `9B8C084C95A566665CE0B3B9B643C5BAEF00838D8F51B2A42D329CFDD65420F8` |
| `e2e/stage12-intake.spec.ts` | `8F596AB7D867723124DCE21577DC6E5362C7A31EA5704DB1ED206DCC52BB96A7` |
| `e2e/stage18-admin.spec.ts` | `292700CF6E0C8108DDF587F703B61E85B13298D68AA007457725880887054325` |
| `e2e/stage20-home-loading.spec.ts` | `8A08DB6A89D0DFE41D9F0B99EB9F53004CC5EEEF16C3FA44C6923747E99C9FE4` |
| `e2e/verify-firm-access.spec.ts` | `744FC8A5DA2E6D2EDC7A0AAA4DACBDBC7661FF3FD38CC1B33B8EBEDC1FF0FD5E` |
| `e2e/p0-request-count.spec.ts` | `86B257F6E2725C7F2EA20B8C10F5D615E11E02F458D875BD2CC94D2CCA81D165` |
| `nexus_backend/tests/test_r5_workflow_acceptance.py` | `7EFF09D7F7FEEF7204858445A0D5CB5F9ADF6C6B98831D0A4DF444B7D1A610D4` |

## 边界

本报告不能证明生产 RLS、PG 并发、真实身份邮件、真实模型、真实移动设备或 C01 门槛通过；本轮不改变会计复核后导出和企业隔离契约，不提交、推送或部署。
