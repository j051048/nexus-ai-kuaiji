# PostgreSQL 16/17 持续验收门槛

日期：2026-10-23。性质：本地代码与隔离验证；不操作线上、不接真实模型。

## 范围与已有能力

保留既有 `verify`、`e2e` CI jobs 和 `scripts/test_accounting_postgres_isolated.py`。新增独立 `ak05-postgres-gate` job，分别使用 PostgreSQL 16、17；复用已有迁移、运行账号配置函数和 AK05 的真实 PostgreSQL／真实 OS 多进程测试。

新增内容：

- `scripts/ak05_postgres_gate.py`：目标检查、隔离准备、固定分组运行和结果门禁。
- `scripts/ak05_postgres_required_cases.json`：经审查冻结的精确 JUnit 用例身份，包含参数组合。
- `nexus_backend/tests/test_ak05_postgres_gate.py`：门禁自身负向回归。
- `.github/workflows/accounting.yml`：只追加独立 job。

## 门槛

每版本按 `unified / process / attempt / regression` 分四个独立数据库运行，避免全局领取夹具相互污染。原 AK05 门槛包含 135 项：3 + 7 + 27 + 98，完整保留。追加本轮 C01 实体 15 项、银行证据 17 项后，当前每版本固定 **167 项：3 + 7 + 27 + 130**。涵盖慢 OCR 与其他企业助手并行、心跳、停机排空、多进程领取、四类崩溃恢复点、费用／attempt、角色边界、公平调度和原清理权限回归。

新增的 `test_c01_auxiliary_postgres.py` 与 `test_c01_bank_evidence_postgres.py` 在不访问数据库的 collect 阶段确认各 15 项，精确参数身份加入 regression 清单；辅助实体原生 SQL 防绕过、银行证据绑定、独立复核及双审核人竞争不能被静默跳过。添加前先以缺少 30 项的旧门槛复现失败，证据 `tmp_test/ak05-pg-gate-c01-extension-red.xml`；追加后门禁自身 44 项全部通过。

安全复审又追加 2 个真实 PG 银行场景：停用账户不得跳过待复核覆盖、负余额证据仍可提交及独立复核。原 165 项保持，冻结银行专项扩展为 17 项。门禁先以 165≠167 复现失败，证据 `tmp_test/ak05-pg-gate-bank-review-red.xml`，追加后门禁自身仍 44 项通过。

门禁同时要求：

1. 实际服务器 `server_version_num` 与 PG16／17 matrix 匹配；`fsync / synchronous_commit / full_page_writes` 开启。
2. 每组提供 admin／app／worker／projection 四种身份，连接相同本地隔离库。必须 `127.0.0.1`、独立非 5432 端口、`ak01_local_ak05_` 前缀，无 URL 查询参数覆盖；不同分组必须不同数据库。
3. `ak05_runtime / ak05_worker / ak05_projection` 为独立 NOINHERIT 登录，无超级用户、BYPASSRLS、建库、建角色权限，直接成员关系精确匹配对应能力角色；能力角色 NOLOGIN 且无特权。
4. 迁移状态及校验和全部匹配；准备阶段迁移重入没有新增。
5. pytest 退出为 0；JUnit 必须存在、有效，实际用例与冻结身份完全一致，没有缺项、替代或重复；任何 skipped／failure／error／disabled／deselected 或计数不符均失败。
6. 不接受额外 pytest 选择参数，清除 `PYTEST_ADDOPTS` 和自动插件注入，不能通过 `-k` 等方式偷偷跳过门槛。
7. 开始／结束的候选 HEAD、源码／测试／工作流／迁移聚合指纹一致；验收期间并行智能体改动相关候选文件将使整轮失败。

不能仅凭 pytest 退出 0，或沿用上轮通过的 XML，宣布本轮通过。每次运行使用新的输出目录，XML 与脱敏日志、summary 一起留证。

## 本地运行

先独立启动已经批准的本地临时 PG16／17 实例。已有完整配置可以复用登录，准备器不会修改密码或放宽角色权限，只创建本轮随机命名的新数据库。CI 全新集群则只提供 admin 配置，使用现有 provision 函数创建三种受限登录。

```powershell
.\.venv\Scripts\python.exe scripts/ak05_postgres_gate.py prepare --admin-config tmp_test/ak05_pg16_unified_config.json --major 16 --out tmp_test/ak05-pg16-new-config --allow-create-local-databases
.\.venv\Scripts\python.exe scripts/ak05_postgres_gate.py run --manifest tmp_test/ak05-pg16-new-config/manifest.json --out tmp_test/ak05-pg16-new-reports
```

PG17 使用对应版本的本地配置、`--major 17` 和另外两个全新目录。所有配置路径／报告目录仅作示例，不能覆盖旧证据。

准备器需要显式 `--allow-create-local-databases`；不读取生产数据库环境变量，不删除数据库，不覆盖已有账号、目录或配置。原 runner 继续约束 libpq 目标和 Python 网络，只运行受控假供应商。

配置文件含临时数据库凭据，应保持在忽略目录；POSIX 文件权限为 0600。报告对完整 URL、编码／解码密码脱敏，CI 只上传报告目录，禁止上传配置目录。CI 仅把容器端口发布到宿主 `127.0.0.1:55432`；容器内部监听配置不等同于宿主公网暴露。

## 红绿证据与运行边界

新增门禁失败测试先执行：模块尚不存在时产生 collection error，证据 `tmp_test/ak05-pg-gate-red.xml`。实现后门禁负向与纯执行器测试 **44 passed**，证据 `tmp_test/ak05-pg-gate-green.xml`，包括退出 0 但漏项／跳过、损坏 XML、错误主版本、角色特权、成员关系、来源变动和敏感日志脱敏。

真实 PG16／17 当前候选的完整运行证据由本轮总验收报告统一登记，不能把上轮 PG16／17 XML 计为本轮重跑。本轮没有触发 GitHub Actions、修改分支保护、提交、推送或发布。新增状态检查可供后续配置仓库必需检查；工作流落库本身不代表已配置 GitHub 分支保护。

## 如何增加门槛

新增业务场景先编写可失败的测试并单独验证，再人工审查后更新 `GROUPS` 与冻结用例身份清单。禁止在 CI 中从本次实际结果自动生成预期清单，禁止遇到失败就删用例、降级为 skip 或扩大角色权限。已有能力不得因新增场景被排除。

本轮 C01 新增辅助实体、银行覆盖证据专项已加入同一版本回归分组与冻结身份；真实 PG16／17 执行结果由总验收报告登记，不用 SQLite 或 collect-only 的结果替代 PG 专项。
