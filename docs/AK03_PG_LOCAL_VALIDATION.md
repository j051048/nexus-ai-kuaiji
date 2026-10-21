# AK03 本机 PostgreSQL 事实校验验证

日期：2026-10-21。仅本机隔离 PG14、真实受限角色、合成 HTTP；没有真实模型、生产连接、提交、推送或部署。

## 结论

- 新增 `test_ak03_postgres.py`：7 passed；AK02 请求包/政策/助手/费用定向回归：20 passed。共 27 个不同 test ID 分两次通过，均 0 skip。
- 内部助手 claims/status 不进入旧 PG 三字段 public JSON；当前合法事实以服务端固定文字显示，错误/缺少事实依据不能发布财务断言或动作。
- 纯视觉票据的原文无独立证明时阻断采纳；独立会计明确处理原件异常后，仍必须会计复核才能导出。
- 虚构但借贷平衡的拆分建议被拒；已知费用保留且同键请求不重发。

## 隔离与版本

- HEAD：`baf3f414187a198dbde1c41f8a90e42dee2629ca`。
- 候选目录：`tmp_test/ak03-codex-20261021/pg/candidate`；从 HEAD 导出，只叠加 AK01/AK02/AK03 明确源码白名单及本次专项。
- PostgreSQL 14.24，仅 `127.0.0.1:15438`、临时库 `ak01_local_9dce865ffa`。原 5432 集群未修改或重启。
- runtime/worker 分别登录，均 NOSUPERUSER、NOBYPASSRLS、NOINHERIT。测试构造用本机管理员仅创建合成夹具/核验行；业务与 worker 执行走实际受限角色。
- 使用 HEAD 的 134 个迁移，最大版本 135；未纳入并行 138、未修改 C01、迁移或 PG 投影。
- 候选 database/main/maintenance/workspace projection/workspace service/migrate/旧 workflow/旧 PG integration 共 8 个保护文件与 HEAD 字节一致。

## 红—绿证据

先在上轮 AK02 冻结候选运行新增 7 项：`5 failed, 2 passed`。

- 无 claims 的“利润 999999、凭证已审核并导出”直接发布，属于真实发布边界缺陷。
- purevision 无独立原件证明时旧实现允许 apply，属于原文证据资格缺陷。
- 仅用 gross 引用的 0.15 + 0.15 均衡分摊被旧实现接受，属于金额来源缺陷。
- 另外两项为旧 schema 不支持内部 claims 的新契约缺口；不把它们误报为跨企业泄漏。
- 非法 citation 与跨企业零调用两项是原有能力，旧候选即通过。

覆盖 root / assistant / compiler 最终冻结文件后，相同 7 项全部通过。随后 20 项既有受限 PG 定向回归通过。

## 新专项细节

1. 合法当前 120 元事实：先业务移交，再由获得账套授权的会计执行；仅显示当前保存资格，不冒充会计审核完成。
2. 错误 claim：历史 100 不能替代当前 120；输出固定人工核对提示，不发布任一金额。
3. 无 claim：自由文本财务断言与任务理由被移除，输出固定安全提示。
4. 三种情况下都检查 HTTP 返回、assistant_turns.reply_json 和 assistant_messages.content_json 恰为 answer/citation_ids/proposals 三字段且一致；内部 claims/status 未泄漏。
5. 非法 citation：硬失败、不留 assistant 消息，保留真实模拟 usage 的 22 micro 估算费用，不重发。
6. 他企会话：授权拒绝、零发送、零费用。
7. 原件人工确认闭环与分摊拒绝：申请人不能 resolve 原件异常；独立会计 resolve 后仍先是草稿，approve 后才允许 export；无依据分摊失败保留 11 micro 费用且未采纳。

## 定向回归

- test_ak02_postgres.py：8 项。OCR/journal/intake/chat 的授权包、来源身份、费用、幂等；他企零调用、撤权与发送后政策撤销。
- test_stage15_assistant_meter_postgres.py：5 项。实际受限 worker 费用、共享预算、伪造 token、未知费用不重放、撤权不发布。
- test_stage15_conversations_postgres.py：7 项。真实队列/历史、权限/RLS、不可伪造领取与接纳、租约、原生 citation 及 OCR 版本绑定。
- 未重复运行旧的“纯视觉自动采纳无需人工确认”正常路径；该假设已被 AK03 有意更改，使用上述显式原件确认闭环验收，不静默放宽新守卫。

## JUnit 与哈希

| 文件 | cases | failures | skip | SHA-256 |
|---|---:|---:|---:|---|
| `pg-ak03-red.xml` | 7 | 5 | 0 | `96550c202a2b8848e2d8afdcfdb6cc61d9cf0b580e48cef489a59b1a285aa8d0` |
| `pg-ak03-green.xml` | 7 | 0 | 0 | `edd9e4b07a77e5ca2ff283f8a92ec389fa17811bdd429fa1683409069a94cac3` |
| `pg-regression-final.xml` | 20 | 0 | 0 | `c69e750255b43c59b8a297a3156bc5321c32df53caa1379061adb6119d487e33` |

| `pg-final-checks.xml` | 10 | 0 | 0 | `37edfbb5ee8007620ab3458de53d17e81d53c86856a3ed95af5ad39492993a7e` |

| `pg-final-label-boundary.xml` | 7 | 0 | 0 | `cb9968a9370cc1eba6d7c3c8e8949574c4266dfe10d68464905e6c9edaca270c` |

| `pg-final-native-conflict.xml` | 7 | 0 | 0 | `9767d29ea22b15e66d73e19403251e40c4b0d6bc7ff2d8302ef703525d447c96` |

证据均位于 `tmp_test/ak03-codex-20261021/pg/`；`pg-verification.json` 包含迁移校验和、角色、清理与保护文件核验；`final-overlay-hashes.json` 包含最终白名单。

## 最终白名单 SHA-256

| 文件 | SHA-256 |
|---|---|
| `nexus_backend/app/intelligence/context_pack.py` | `ad3408ac579d9167594c9120759aca86f4721f2542daa008c95c7451332d4bda` |
| `nexus_backend/app/intelligence/runtime_context.py` | `5b93c91de3627b1605c8ef05720df14fdd2bba789f29a4c7877524e166c42230` |
| `nexus_backend/app/intelligence/provider.py` | `1cb2c223a24d79e378eebaf07c5daf833bb200500052f433e74935404a29c469` |
| `nexus_backend/app/intelligence/ocr.py` | `829b9a6f0ab7f699283aed225ba31ab2b924a048e0f4a59f22e4dc699257bad4` |
| `nexus_backend/app/intelligence/service.py` | `adbe5caa489f87cf254126d5872faffd4211aebca0b65d50dcd9d7da9b004af0` |
| `nexus_backend/app/assistant/context_pack.py` | `3088609042ec52015a05426b73b2034e8711d3f63d8d56681bb863227af6574b` |
| `nexus_backend/app/assistant/provider.py` | `f4416b469e63160262383db8777c3a6ffadbfb16f149babdc211af3562d73c6c` |
| `nexus_backend/app/assistant/worker.py` | `e85031c9fbc125a005e95176972ab361b32ad6b77ffb9651ac06cd3c5bd3608a` |
| `nexus_backend/app/intake/service.py` | `a35f4b8c600567de9a2658e9cb9d68e4595cc07faa2259b0e4f6f980b8f5321d` |
| `nexus_backend/app/jobs/worker.py` | `0407a5c2cf6aeabb56d87955e3bd66aefbd6cd4f7d5ae05e887217faf84a8043` |
| `nexus_backend/app/operations/meter.py` | `ac093678fbd8fd2e07ceb859838b13363e82dc6b6054d5657d3710f66b7760c5` |
| `tools/ai_inference_lab/context.py` | `e608d90a87e86ff671a58953cb8049e1cf61ed898c0b7547f31d8bd47c7dc242` |
| `nexus_backend/tests/test_ak02_postgres.py` | `ab5e426c6706a418d9c5177200b96b5941bc9d1d26b972ba987ee5980abfd342` |
| `nexus_backend/app/intelligence/checks.py` | `7fa2d116a3ec3fd0e066cf0c98c31b49bff4b33c8944ac56c028725341e51000` |
| `nexus_backend/app/intelligence/models.py` | `92fdfe05549fbab641da14b568351a7e9c4d63f2a32d00b6e87dd0ea1d5c910e` |
| `nexus_backend/app/intelligence/journal_facts.py` | `5b0543c1e78c08e427af7c0d01800c31a6a5dfec0273f80d7f21be8b4ddc05d9` |
| `nexus_backend/app/intelligence/fact_validation.py` | `7c5a28523ffedceab81a3e4e176778586ee14d0ac8a5ac668762f3a80155afd0` |
| `nexus_backend/app/assistant/models.py` | `4808be978ee6eed17489a4181f5fa582490743320f18055d2de438327fa744ac` |
| `nexus_backend/app/assistant/fact_validation.py` | `cc0281170bbbe9f98f26713e88a6eac354784b82867f965d0c195d40e30b248f` |
| `tools/ai_inference_lab/answers.py` | `fe9601caf9499cd6ab77a99456ea0c79b2fe0178043dd8bcf186db55eec277a7` |
| `nexus_backend/tests/test_ak03_postgres.py` | `e97a3909d661d263ff4015f48503ecc87e129714ac2b477a3dd966c886b4f128` |

## 清理及未验证范围

- 前后白名单 21 个文件 hash 与工作区一致；测试退出后该库其他连接数 0。
- 临时 15438 已停止，pg_isready no response；本轮私有密码文件和启动脚本删除，报告/日志未保存密码。原 5432 仍 online。
- Python runner 清除继承的数据库与模型供应商环境，连接仅允许明确本机数据库；socket 限 loopback。此为测试防护，不宣称操作系统网络沙箱。
- PG17、真实供应商、真实票据识别准确率、生产接入、完整恢复与 C01 验收不属于本轮通过范围。
- 新增测试 ruff 通过；保留已有 Starlette 弃用警告。

## 最后检查器安全修复补验

root 终审补齐金额 quote 截断边界：税额 10.00 不得引用原文 10.001 的部分字符。本轮仅同步 checks.py，其余白名单未变。

- 在该最终源码上重跑 AK03 全 7 项 + AK02 OCR/journal/intake 三条实际请求路径，`10 passed, 0 skip`（13.50 秒）。
- `pg-final-checks.xml` 替代前次相关用例结果；不是额外 10 个不同用例，总计仍为 27 个不同 test ID。
- 该次数字边界版本 checks.py SHA-256：`05d613adb13bc03468ddc4f9fa7fc18faa98bd6413874d50564a4b6b92eb8475`；已由下述最终标签边界版本替代。
- 白名单 21 文件与工作区一致，迁移校验和不变；已再次停止 15438、清理临时凭据与脚本，5432 未触碰。

## 最终金额标签边界补验

root 终审再修中文完整标签不得被截取内部后缀：不含税金额不能裁成含税金额。仅 checks.py 变化。

- 最终候选重新运行 AK03 7 项真实受限 PG：`7 passed, 0 skip`（11.17 秒），正常英文 gross、独立原件确认、会计复核导出均保持通过。
- 证据：`pg-final-label-boundary.xml`；最终 checks.py SHA-256：`7fa2d116a3ec3fd0e066cf0c98c31b49bff4b33c8944ac56c028725341e51000`。该结果替代前次对应 7 用例，总体仍 27 个不同 test ID。
- 全部 21 个白名单 hash 与工作区一致，数据库迁移校验和不变；15438再次停止、临时凭据和启动脚本删除，5432保持不变。

## 已有原生 PDF 冲突回归修复补验

root 完整 Paddle 回归发现 native/Paddle 合并可能抹去非空 PDF 原生文字冲突；修复仅让 Paddle 补空 native，既有非空 native 冲突单独保留 blocking。仅 ocr.py 变化。

- 最终候选再次运行 AK03 7 项真实受限 PG：`7 passed, 0 skip`（10.59 秒），证据 `pg-final-native-conflict.xml`。
- 最终 ocr.py SHA-256：`829b9a6f0ab7f699283aed225ba31ab2b924a048e0f4a59f22e4dc699257bad4`。全部白名单 hash 与工作区一致，迁移校验和不变。
- 该定向结果替代前次对应 7 用例，总数仍 27 个不同 test ID，不把重复补验相加。
- 15438已停止、本轮临时凭据与启动脚本删除，原5432保持online且未修改。
