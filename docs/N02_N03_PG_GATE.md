# N02/N03 PostgreSQL 16/17 持续验收门禁

本门禁复用现有受限角色和本地隔离执行器，不替换 AK05、C01 或独立复核门禁。执行对象是虚构资料所在的独立本机 PostgreSQL 数据库；不连接线上，不调用模型或真实通知。

## 固定范围

`scripts/n02_n03_postgres_required_cases.json` 为版本 1 的完整用例清单，来自真实 `pytest --collect-only`，包含参数化身份：

- N02：`test_tax_workpaper_postgres.py` 和 `test_n02_tax_concurrency_postgres.py`，45 项。
- N03：`test_n03_notification_postgres.py` 和 `test_n03_worker_postgres.py`，55 项。

数字仅作清单结构校验。通过判定比较每个 `classname::name[param]`，缺少、重复、替换用例均失败。新增或变更用例需要重新 collect、评审并显式更新清单；不能仅修改数量。

## 执行

先使用已有 AK05 `prepare` 流程准备专用空库和受限账号，完成全部迁移。不要与正在运行的其他验收共享数据库。配置文件留在被忽略的本机目录，不能放进报告目录或 CI artifacts。

```powershell
.\.venv\Scripts\python.exe scripts/n02_n03_postgres_gate.py `
  --config tmp_test/private-pg16/regression.json `
  --major 16 `
  --out tmp_test/n02-n03-pg16-new-run
```

PG17 使用独立数据库与新的输出目录，将 `--major` 改为 `17`。`--major` 不会改变或假定服务器版本：门禁读取实际服务器版本，错误版本即失败。命令自身不创建数据库、不执行迁移、不修改授权。

每次输出目录必须不存在，防止旧报告冒充本轮结果。输出包含：

- `n02.xml`、`n03.xml`：真实 pytest JUnit，精确逐项核对，任何 skip、error、failure、非零退出或超时均失败。
- `n02.log`、`n03.log`：已脱敏的本地执行日志。
- `summary.json`：`schema=n02-n03-pg-gate-v1`；完整候选指纹、实际 PG 主版本和服务器版本、持久化及受限角色检查结果、迁移状态、两组通过数与 JUnit SHA-256、冻结清单 SHA-256。

`migrations.status=verified` 仅在数据库全部迁移的登记摘要与当前文件一致时生成；`count` 是实际 PostgreSQL 迁移数，`digest` 绑定完整候选迁移树。每组开始和完成时重新检查源码、清单和数据库迁移记录；任何变化都使本轮报告失效。

完整候选使用 `scripts/release_candidate.py`，覆盖前后端、测试、脚本、依赖和部署配置。两个数据库版本和发布前检查必须使用同一候选，不能把不同提交或不同未提交内容的绿灯拼接起来。门禁通过仅证明本地技术验收，不等于授权发布。

## 门禁自身的红绿证据

先新增 `test_n02_n03_postgres_gate.py`，运行得到 1 个 collection error（新门禁模块尚不存在），随后实现独立门禁。没有将这个红灯描述为真实数据库缺陷。

```powershell
.\.venv\Scripts\python.exe scripts/test_accounting_offline.py `
  nexus_backend/tests/test_n02_n03_postgres_gate.py -q
# 59 passed
```

覆盖缺/多/重复/替换用例、隐藏 skip/错误、虚假计数、坏 XML、错误清单版本/文件、非零退出、超时、源码/清单漂移、迁移未应用/漂移、错误 PG 版本、空/重复/未来迁移、角色拒绝、输出防覆盖、环境隔离和密码脱敏。此项为门禁单元测试；真实 PG16/17 业务结果必须另行运行上述命令取得，不得以 mock 代替。
