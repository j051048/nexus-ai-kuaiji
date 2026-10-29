# N03 PostgreSQL 16/17 本地验收

日期：2026-10-29。候选：`582cd87671b256dd747da7f5a49831bcfc3948f8` 加本轮未提交 N03 文件。

## 结论

两种真实 PostgreSQL 主版本的 N03 专项与 N02 邻近回归通过。全部在本机独立数据库、虚构企业和本地 sink 中运行，无真实通知、模型或生产连接。此结论不代表真实通知服务已经接通。

| 环境 | N03 专项 | N02 邻近回归 | 来源稳定性 |
|---|---:|---:|---|
| PostgreSQL 16.15 | 55 passed，75.42 秒 | 45 passed，13.77 秒 | 两轮 source_unchanged=true |
| PostgreSQL 17.11 | 55 passed，72.30 秒 | 45 passed，13.33 秒 | 两轮 source_unchanged=true |

两个环境均启用 `fsync`、`synchronous_commit`、`full_page_writes`。迁移账号只执行安装与虚构身份准备；所有权限攻击以受限运行账号执行，worker 使用受限 `accounting_worker` 角色。运行与 worker 登录均无 superuser、BYPASSRLS、CREATEDB、CREATEROLE。

## 红—绿证据

- 原生 SQL 不能以只读身份修改通知或授权，不能伪造创建/撤销身份、跨企业/账套/版本绑定、覆盖不可变 payload。
- 先前阶段真实复现了结算参数边界、空白回执、人工取消时篡改 worker/lease 证据的问题，以前向迁移 175–177 修正；174–177 本阶段没有改动已应用文件。
- 最后复查发现：冻结联系人仍有公司成员身份，但失去账套读取权或已被最新服务单换人，旧通知仍能原生插入、领取、进入发送。
- 在 PG17 的真实 1–177 基线重现 **6 failed**：原生 INSERT 两项，worker claim/begin 各两项。使用正常的独立客户财务身份和合法服务单变更，未关闭触发器或放宽角色；基线仅选择 1–177 文件集合，保留每个文件的校验和检查。
- 新增前向迁移 **178_n03_current_recipient.sql** 后上述六项通过，并纳入两版本最终 55 项。
- 初次 book-read 用例误尝试降低不可降级的 owner 身份，产生 fixture 错误；该错误没有计入真实缺陷证据。已更换为独立 operator + company finance + book viewer 客户。仍然具有事务所 owner/admin 权限的人不依赖单独 book_access，这条既有正常路径保留。

最终资格与客户实际签收契约一致：当前有效企业/服务关系/用户/企业成员，最新服务单联系人、冻结包联系人及通知收件人相同；拥有当前账套读取权；收件人与包创建人不同。worker 的私有资格函数不依赖请求 actor，且未向应用或 worker 暴露直接调用权限。

## 实际覆盖

- 两个独立进程同时领取，只有一个成功；领取不增加发送 attempt。
- `begin` 提交后子进程退出，租约失效后进入 unknown；重启不重新领取或重复发送。
- 错误/过期 lease token、恢复前 token、重复结算均被拒绝。
- 领取后撤权、撤销授权、换联系人、失去账套读取权阻止开始发送；已有 sending 的结果仍留痕，不宣称能够撤回已经外发的数据。
- 未知结果不能用换 kind、幂等键、授权版本或预先排队的兄弟任务绕过而再次发送。
- 慢本地 sink 调用期间，同企业配置写入可完成，证明外发没有持有数据库写事务/企业锁。
- 恢复 hold、恢复代次、待处理清单指纹与旧 lease 的边界；原有 N02 独立复核、来源版本、旧稿导出和并发用例未退步。
- worker 不具有通知表直接读写权限；应用角色不能调用 worker 领取/结算函数。

## 迁移验证

最终两库均重新从空库正常迁移至最大版本 178。仓库该时刻共有 **166 个迁移文件**（历史编号并不连续），所有文件均登记成功。再次正常执行迁移，两版本 `new_applied=[]`，数据库登记摘要与所有源文件摘要完全一致。没有修改数据库历史 checksum 或绕过校验。

## 命令与本地证据

隔离 runner 安全注入本机 DSN 后运行：

```text
pytest -q -p no:cacheprovider nexus_backend/tests/test_n03_notification_postgres.py nexus_backend/tests/test_n03_worker_postgres.py
pytest -q -p no:cacheprovider nexus_backend/tests/test_tax_workpaper_postgres.py nexus_backend/tests/test_n02_tax_concurrency_postgres.py
```

本机证据目录 `tmp_test/n03-pg-hardening/`：

- `recipient-baseline177-pg17.xml`、`.log`：六项真实红测试。
- `final-pg16.xml` / `final-pg17.xml`：各 55 项。
- `n02-pg16.xml` / `n02-pg17.xml`：各 45 项。
- 对应 `*-metadata.json`：完整源 SHA-256、版本、角色、数据库设置和迁移摘要。
- `repeat-migrations.json`：二次迁移零新增、所有摘要一致。
- `cleanup.json`：已删除的本任务测试数据库清单。

该目录是忽略的本地测试产物，不应提交凭据或连接串。测试后核验没有活动连接，按记录精确删除了本任务六个独立数据库（每版本三个），仅停止本任务拥有的两个集群，删除本地测试密码文件；没有强制终止其他任务连接或操作其他集群。

## 最终文件 SHA-256

```text
followups.py
42f308c4b642e62af5abf5ccef0acae55f222b1b5b861bb4b14182662def8521
notification_dispatch.py
e4241e046a62b15b45efc948780afb8c0cce00da1af63c158e1b1cef3219c0f4
test_n03_notification_postgres.py
661b4fb6e78df20fc7219089f48d5ee166ae057767855972314dfa455a9968e0
test_n03_worker_postgres.py
450d5e9df121cdc18c04cca766f38600f6bfa4d1e763857cdffe29da5957ae75
178_n03_current_recipient.sql
18dc256568b35c781d2d587b2783934e37a171b5f7156a364e3cd45fa8b020ca
```

未提交、未推送、未部署。本报告只证明 N03 的本地数据库与离线通知契约，不扩大为全系统、真实供应商或生产恢复的验收。
