# NEXT11 验收记录：最终同候选联合验收

- 结论：**本地候选工程验收部分通过；不满足发布条件。** 不宣称已全面上线验收通过。
- 候选：HEAD `0a0c6cab4ba18d2dfe5ce695b6844e01895c204b`；source 1821/`d3f7a1fbd9163d2b4f78d2fe26a39e85aafc3e7a0671993267f7406f61fc2f07`；migration 328/`8bbd6f3fdb46addee9bb8737775ed7966f4a2938c8ea413d7c4d4952dd76444f`。该候选含并发任务的 `TaxFilingRecords.tsx` 修改及另一任务的提交，归属见 `NEXT_OWNERSHIP.md`。
- 冻结：`tmp_test/next/next11-final/candidate.json`；下述最终运行均核对该指纹，运行期间未修改受候选覆盖文件。
- 工作区：35 项未提交／未跟踪（认证修复、N04 适配器/迁移、坏 PNG 修复、NEXT 文档等）；未提交、未推送、未部署、未执行线上迁移。

## A. 本地候选工程与联合验收

| 门槛 | 结果 | 证据／说明 |
| --- | --- | --- |
| 候选冻结与指纹一致 | 通过 | 1821/`d3f7a1fb…`、328/`8bbd6f3f…`；P01 d3f7-r2 前后一致；N04 在 d3f7 复跑 |
| 离线回归（N01/04/05/06/08） | 通过 | 14+4+3、15、70+tsc、66+42、69；见各包记录（N04 在 d3f7，其余为 57bb 同后端候选） |
| NEXT08 PG16/17 | 通过 | 各 10 passed／13.07–13.14s（57bb） |
| 前端 typecheck/build | 通过 | d3f7 typecheck exit 0、build exit 0（`TMP/TEMP=tmp_test/tmp`） |
| 浏览器 NEXT07 | 通过 | d3f7 25 passed／1.4m，3 引擎 + 手机 |
| 浏览器 NEXT08 批量 | 通过 | 57bb 1 passed／28.8s（`monthly-batch.spec.ts`） |
| NEXT09 容量 | 50 clients 通过；**100 clients 诊断失败** | 50 clients/100 req 0 error；100 clients 132 次 queue_full 拒绝；单进程、4 连接池；诊断脚本不在候选内，不计验收 |
| NEXT10 离线助手 | 通过 | 353 passed；`real_model_evaluated=false`（57bb） |
| **P01 完整恢复（DB＋原件＋身份＋权限）** | **通过** | d3f7-r2：regression 155/153.216s、joint 2/82.368s、identity 4/58.671s；PG16/17 的 database/originals/identity/permissions/hold/review_export/tax_delivery/no_replay 全 true；candidate `d3f7a1fb…` |
| 恢复后登录/读/写/复核/导出 | 通过（P01 内） | `p01_checks` 的 identity/permissions/hold/review_export/tax_delivery 为 true；未做浏览器端恢复后操作 |
| NEXT02 主链 | **57bb 通过（8 passed/28.8s）；跨年 HTTP/PG 6+6 passed** | 真实 GoTrue＋PG17＋preview；连续两期、跨年浏览器、多银行仍缺 |
| NEXT03 动态探测 | 57bb 通过 | 19/19 passed（57bb，`next03-security-probe-57bb.json`）；7 文件 **188 passed**（`next03-security-57bb.xml`） |
| 迁移前向升级 | 部分 | 328 个迁移在 SQLite/PG 隔离测试中应用；P01 恢复库 migrations 198；未对生产快照做升级演练；未改历史 checksum |
| 恢复后不盲目重放 | 契约测试通过（N06/N03/P01） | P01 `no_replay=true`；未做真实供应商回调重放 |

因此 A **仍不能整体标记通过**：NEXT02 单期主链在 57bb 通过、跨年 HTTP/PG 已补 6+6，但连续两期、跨年浏览器、多银行未验证；NEXT04 四类接手适配器已接，d3f7 SQLite **15**、PG16/17 各 **28**、税审与交付包来源真实浏览器通过，批量/通知来源浏览器未验；NEXT09 100 clients 边界失败；NEXT05、NEXT06、NEXT07、NEXT08 已在 57bb/d3f7 通过。

## B. 远端 CI

**未运行。** 本轮未提交、未推送；不触发远端 CI，也不以本地替代远端。推送是否触发自动部署需在真实仓库规则中核查，本轮未核查线上规则。

## C. 真机、真实供应商、真实试点

**未验证。** 未连接真实邮件/短信/推送供应商，未发真实客户消息，未用真机/微信/屏幕阅读器人工走查，未接真实模型或真实客户数据，未做真实税局/银行试点。

## D. 生产发布授权

**无。** 本轮未获提交/推送/部署/线上迁移授权；迁移、配置与回退顺序仅供评审。

## 迁移、部署、检查与回退顺序（评审用，未执行）

1. 冻结候选与文件摘要；确认 `git status` 归属，排除并发任务未评审修改（尤其 208/OCR）。
2. 备份数据库与对象存储；记录恢复点。
3. 在隔离副本按 001→328 顺序执行迁移，核对 checksum；不得改写已应用迁移。
4. 先部署 API/worker（关闭通知真实发送），再部署前端；配置 `DELIVERY_CALLBACK_SECRET` 等密钥由密钥管理注入，不写入仓库/日志。
5. 健康检查：登录、租户读取、复核、导出、通知领取；确认 worker 不在数据库事务内做外部网络等待。
6. 观察 attempt/unknown：结果未知的通知不得自动重发；人工核查不生成签收。
7. 回退：优先回退应用版本；数据库迁移按新增列/表向后兼容策略处理，不删除历史证据；对象存储保留原件。
8. 恢复演练：数据库＋原件＋身份＋权限恢复后，重新登录并验证读/写/复核/导出，再决定是否恢复 worker。

## 明确声明

- 未提交、未推送、未部署、未执行线上 SQL、未重启线上服务。
- 未调用真实模型、真实通知渠道，未向真实客户发送消息。
- 未创建收费云资源。
- 本记录不构成生产发布授权。
