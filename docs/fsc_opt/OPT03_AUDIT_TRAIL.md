# OPT03：审计日志防改删、哈希链、读取下载留痕

先读 `OPT00_MASTER.md`。无前置。本批是国企巡视与外部审计场景下的最高优先级。

现状线索：`audit_events` 在 `nexus_backend/migrations/postgres/001_accounting.sql` 中只授予业务角色 `SELECT,INSERT`，没有禁止改删的触发器，没有哈希链。凭证表有硬触发器，见 `105_b03_posting_core.sql` 的 `b03_immutable_voucher_guard`，本批照它的写法做。

## OPT03-A  审计日志禁止改删

**做法。**

1. 先搜索确认没有任何应用代码或迁移对 `audit_events` 做 `UPDATE` 或 `DELETE`（含恢复、清理、测试夹具路径）。有则逐个列出并判断：恢复脚本整库还原不受行级触发器影响；应用内的改删必须改掉。
2. 新增 Postgres 迁移：`BEFORE UPDATE OR DELETE` 行级触发器加 `BEFORE TRUNCATE` 语句级触发器，抛出异常。函数写法、`SECURITY DEFINER`、`search_path`、`REVOKE` 与 `b03_immutable_voucher_guard` 保持一致。
3. SQLite 新增对应触发器（`BEFORE UPDATE`、`BEFORE DELETE` 各一，`RAISE(ABORT, ...)`）。

**先失败测试。**

- SQLite：直接对 `audit_events` 执行 `UPDATE` 和 `DELETE`，修改前成功，修改后报错。
- PG：以业务角色执行应被权限拒绝（现状已如此，保留该断言）；以表属主或迁移角色执行，修改前成功，修改后被触发器拒绝；`TRUNCATE` 同样被拒。

**验收。** 新增用例通过；全量后端测试无新增失败（有测试依赖改删审计行的，改测试的做法而不是放开触发器，逐条记录）。

## OPT03-B  哈希链

**目标。** 任何一行被改、被删、被插队，事后都能检测出来。

**设计约束。**

1. 新增两列：`prev_hash`、`row_hash`。`row_hash = sha256(prev_hash || 规范化后的本行内容)`。规范化必须确定：字段顺序固定、时间戳用存储的原始文本、空值有固定表示。复用仓库已有的规范化 JSON 摘要写法（搜索 `_fingerprint`），不另造。
2. 链按**事务所**分链，不做全局单链，避免所有企业的审计写入互相串行。链尾读取必须加锁，保证并发写入不分叉：PG 用按事务所键的事务级咨询锁，或用一张每事务所一行的链尾表 `SELECT ... FOR UPDATE`。选一种并在迁移注释里写明理由。
3. 哈希由数据库触发器计算（`BEFORE INSERT`），不依赖应用层传入，这样任何写入路径都无法绕过。SQLite 没有内置 sha256：先查仓库是否已注册自定义函数（搜索 `create_function`），有则复用；没有则在 SQLite 连接初始化处注册一个，并在应用层保证所有连接都注册。
4. 存量行：迁移时按事务所、按主键顺序回填。回填在同一迁移内完成。
5. 仓库有约 25 个 `_audit` 辅助函数和约 43 处直接 `INSERT INTO audit...`。本模块不要求合并它们，但必须验证每条路径写入后链都正确。

**校验工具。** 新增 `scripts/verify_audit_chain.py`：只读，按事务所遍历，重算并比对，输出第一处断点的主键和原因（内容被改、缺行、顺序异常）。退出码：0 完好，非 0 有断点。不输出审计内容本身，只输出主键和摘要前 8 位。

**先失败测试。**

- 正常写入若干条后校验通过。
- 用绕过触发器的方式（测试内临时禁用触发器，或直接改库文件）篡改一行内容、删除中间一行、交换两行，校验脚本分别报出断点。
- PG 双会话并发写同一事务所各 50 条，链无分叉、校验通过。
- 两个事务所并发写互不阻塞（一个会话持有未提交事务时，另一事务所的写入能完成）。

**验收。**

```
python -m pytest nexus_backend/tests/<新增哈希链测试> -q          # SQLite
python -m pytest nexus_backend/tests/<新增哈希链PG测试> -q        # PG16、PG17
python scripts/verify_audit_chain.py <本地测试库>
```

恢复演练：按仓库现有有数据恢复用例的方式备份再恢复，恢复后校验脚本仍返回 0。

## OPT03-C  读取与下载留痕

**问题。** 写操作审计覆盖较全，读取类没有。线索：`nexus_backend/app/intake/service.py` 中返回附件字节的方法没有审计行。

**做法。**

1. 盘点所有"把原件、导出文件、报表文件、备份内容交给调用方"的服务方法。搜索关键词：`FileResponse`、`StreamingResponse`、`Response(content=`、`download`、`export`、`csv`、`read_bytes`。在状态文件列出清单。
2. 为每一类写一条审计：动作名沿用现有命名风格（如 `attachment.download`、`export.download`、`report.download`），记录操作者、对象标识、所属企业和账套、对象内容摘要。不记录文件内容。
3. 审计写入与读取在同一请求内完成。审计写入失败时拒绝返回内容。
4. 列表查询和普通页面读取不记，避免噪音。

**先失败测试。** 对清单每一类：下载一次后查询 `audit_events` 应有对应行，修改前没有。无权限的下载返回 403 且不产生"成功下载"审计行。

**验收。** 新增用例通过；前端下载流程的现有 e2e 用例不需改动即可通过（本机能跑则跑，不能跑写"未验证"）。
