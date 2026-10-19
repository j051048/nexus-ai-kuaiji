# C01 账务与恢复门槛执行报告

日期：2026-10-19
性质：本地/隔离验证；未提交、未推送、未部署、未操作线上

---

## 1. 状态

| 项 | 状态 |
|---|---|
| F04 自定义控制科目 | **配置与部分业务路径已实现，完整约束和验收未完成** |
| F09 多银行基础模型 | **部分实现，逐账户勾稽/核验未完成** |
| F03 锁期依赖 | **已有 P0 修复；C01 未重做** |
| PG14 负向/集成 | **20 passed（旧套件，不含 F04/F09 专项）** |
| PG17 | **未验证（无环境）** |
| PG16/CI | **未在本机执行** |
| 完整 PG+对象存储+身份恢复 | **未执行** |
| F12–F14 浏览器正常路径 | 已跑；故障场景未跑 |
| C01 整批 | **未通过** |

### C01 未通过的原因分类

**A. 代码缺陷及契约未完成**

- F04：辅助维度只做名称/非空校验，未完成实体归属、类型、生效状态和逐维度勾稽；
- F09：银行覆盖仍以 `verified_by` 文本直接登记，未区分声明/待核验/已核验；
- F09：覆盖 `source_ref` 文本未绑定服务端原件/导入批次/hash；
- F09：银行调整项未归属账户；
- 跨期桥接、缺段/重叠、A/B 账户差异抵消的专项保护未完整。

**B. 新功能专项测试缺失**

- F04/F09 的 PG 受限角色成功/负向路径未覆盖；
- 原生 SQL 跨企业/错账套引用未测试；
- 配置/覆盖变更与 lock 并发未测试；
- 同账户跨月稳定 ID、A/B 账户差异抵消未测试；
- A 账户流水匹配 B 账户凭证未测试；
- 已锁期后覆盖写入未测试。

**C. 目标环境和完整恢复未验证**

- PG17；
- PG16/CI；
- 全新 PG + 私有对象存储 + 身份 + 角色/RLS 完整恢复；
- 浏览器 F12–F14 故障。

三类问题不得合并为“外部环境阻断”。

---

## 2. F04 自定义控制科目

### 实现

新增：

- PG `130_subledger_control_accounts.sql`
- SQLite `089_subledger_control_accounts.sql`
- PG `132_c01_control_bank_binding.sql` / SQLite `091_c01_control_bank_binding.sql`
- `ControlAccountCreate` / `ControlApplicability`
- `SubledgerService.configure_control_account`
- `SubledgerService.list_control_accounts`
- API：
  - `POST /api/subledger/control-accounts`
  - `GET /api/subledger/control-accounts`

行为：

- 由核算配置显式声明 `kind → control_account_id/version`、生效期间、必要辅助字段；
- 新增 ledger 级 `subledger_control_settings`：`pending/configured/not_applicable`；
- 无配置/待确认时，`control_reconciliation` 明确返回 `blocked`，不再空集合通过；
- 新建事项要求 `configured` 且有当前期间配置，缺失/过期/未声明控制科目均拒绝；
- `required_aux` 已写入 `subledger_items.aux_json`，建事项时校验必填名称与非空值；
- 勾稽从配置反查控制科目，不再使用 1122/1123/2202/2203 硬编码；
- PG 配置表 RLS + `close_dependency_bump('ledger')`，并显式授予 app 最小权限；
- SQLite 服务层 bump ledger revision。

### 限制

- `required_aux` 已落库并做必填/非空校验，但**尚未验证辅助实体归属、类型和逐维度勾稽**；
- 旧历史事项未绑定配置时仍可读取；`not_applicable` 只阻止新建，不自动覆盖旧事项；
- 自定义控制科目尚无前端配置页；
- PG 触发器/RLS 只有普通 PG 集成测试，未做配置写入/锁期专门并发；
- 旧历史可读不等于允许无约束新增或月结通过。

### 证据

```powershell
pytest test_r01_r03_regressions.py -q
# 13 passed
```

覆盖自定义 9999 科目配置、建事项、列表与勾稽路径。

---

## 3. F09 多银行基础模型

### 实现

新增：

- PG `131_bank_account_coverage.sql`
- SQLite `090_bank_account_coverage.sql`
- PG `132_c01_control_bank_binding.sql` / SQLite `091_c01_control_bank_binding.sql`
- `BankAccountCreate` / `BankCoverageCreate`，账户显式绑定总账科目及版本
- `create_bank_account` / `list_bank_accounts` / `declare_bank_coverage`
- `bank_statement_lines.bank_account_id`
- API：
  - `POST/GET /api/subledger/bank-accounts`
  - `POST /api/subledger/bank-coverages`

行为：

- 稳定银行账户 ID，显式绑定 ledger account/version；
- 按账户/期间保存期初、期末、来源证据、核验人；
- 多账户逐账户检查覆盖、流水净额、未匹配数、账户账面余额与覆盖期末；
- 账户账面合计与覆盖期末合计比较；
- 缺覆盖、未归属流水、开放调节项、账户映射不一致均不通过；
- 银行流水匹配凭证时检查账户映射，防止 A 账户流水匹配 B 账户凭证；
- 银行账户/覆盖变化 bump book dependency；
- 无银行账户配置时保留旧单账户路径，兼容历史测试。

### 明确未完成

- `source_ref` 仍是文本，未绑定服务端原件/导入批次/hash；
- 覆盖没有 `declared/pending_review/verified/rejected` 状态，`verified_by` 目前是登记字段，不能证明独立核验；
- 覆盖期初/期末没有强制原件证据；
- 银行调整项未归属账户；
- 缺段/重叠/跨期桥接未全量测试；
- 旧单账户兼容是默认分支，尚未改为显式迁移状态；下一期缺配置可能静默回退；
- 同账户跨月稳定 ID 尚无专项测试。

### 证据

```powershell
pytest test_r01_r03_regressions.py::test_f09_multi_bank_requires_each_account_coverage -q
# 1 passed
```

覆盖两账户缺覆盖、单账户覆盖后仍不完整。

---

## 4. F03 锁期依赖

已有 P0 修复保留：

- `close_dependency_revisions` book/ledger/template；
- preflight 保存依赖指纹；
- review/lock 重新比对；
- 新增 F03 测试：预检后新增未审草稿，lock 拒绝。

C01 未重做，避免与 P0 代码冲突。

---

## 5. PG 验证

环境：WSL PostgreSQL 14.24，空库迁移 1–132，受限 `b08_runtime`/`b08_worker`。

```powershell
pytest test_postgres_adapter.py test_postgres_integration.py -q
# 20 passed
```

覆盖：受限角色、RLS、跨企业/账套、系统角色边界、审计/导出回滚、并发导入/审核/导出、关期竞争、目录授权、撤权。

**未覆盖：**

- PG17（无环境）；
- PG16 CI（未执行）；
- F04/F09 的专门 PG 动态/并发测试；
- 完整恢复。

---

## 6. 组合回归

```powershell
pytest test_r01_r03_regressions.py test_b04_pilot.py `
  test_stage10_backup_recovery.py test_restore_hold.py `
  test_b05_policy.py test_b08_acceptance.py -q
# 61 passed
```

前端未改；上一轮 345 tests/typecheck/lint/build 仍有效，但 C01 新后端迁移未重跑浏览器。

---

## 7. C01 阻断

1. **PG17 不可用**：无法完成本轮目标版本验证。
2. **完整恢复不可用**：无全新 PG + 私有对象存储 + 测试身份环境。
3. **F09 覆盖核验未完成**：`source_ref` 仍是文本，未绑定原件/导入批次/hash；没有 declared/pending/verified/rejected 状态；调整项未归属账户。
4. **F04 辅助维度仅做名称/非空校验**：未完成实体归属、类型、逐维度勾稽。
5. **F12–F14 故障浏览器未跑**。
6. **F04/F09 专门 PG 并发/原生 SQL 负向未跑**。

C01 **不能宣布通过**；只能报告“F04 配置与部分业务路径实现、F09 多账户基础实现、PG14 回归通过”。

---

## 7A. C01-R3A（2026-10-19）

### 实现

新增前向迁移：

- PG `133_c01_r3a_applicability_guard.sql`
- PG `134_c01_r3a_identity_guard_fix.sql`
- SQLite `092_c01_r3a_applicability_guard.sql`

修复：

- 新增 `subledger_control_applicability` 历史表，按 `effective_from/effective_to` 记录配置/不适用声明，不覆盖历史；
- `_control_configs` 区分“指定期间有效配置”和“是否存在任何配置”，不再用空字符串混用；
- `not_applicable` 先读取试算余额，存在 1122/1123/2202/2203 历史余额、历史配置或事项时拒绝通过；
- 新建事项必须有当前期间 `configured` 声明和对应 kind 配置；
- 控制科目/适用性期间统一 `YYYY-MM`，拒绝 `YYYY-MM-DD`、非法月份、起止倒置；
- `confirmed_by` 由服务端当前 actor 写入；请求指定他人返回 403；
- PG 新表最小 GRANT；新增 company gate、identity guard、bank coverage locked-period trigger；
- 银行账户显式绑定 ledger account/version，逐账户账面余额与覆盖期末比较；
- 银行流水匹配凭证时校验账户映射。

### 专项结果

| 用例 | 结果 |
|---|---|
| A1 受限角色自定义控制科目/建事项/勾稽 | SQLite 通过；PG 新 API 未专项 |
| A2 无 effective_to 的真实 PG 路径 | **未专项** |
| A3 真正不适用子账的正常路径 | **未覆盖** |
| A4 银行账户/覆盖新增 API | SQLite 通过；PG 未专项 |
| A5 原复核/锁期/导出回归 | B04/P0 48 passed |
| N1 有应付余额声明不适用 | SQLite 拒绝 |
| N2 漏传期间不能误判不存在 | SQLite 默认期间勾稽通过 |
| N3 当前声明覆盖其他期间 | SQLite 拒绝 |
| N4 伪造确认/审核身份 | SQLite 拒绝；PG `verified_by` 触发器通过 |
| N5 YYYY-MM-DD/非法月份/倒置 | YYYY-MM-DD 已拒绝；非法月份/倒置部分 |
| N6 原生 SQL 跨企业/错账套/错版本 | **未执行** |
| N7 无复核资格改审核状态 | **未执行** |
| N8 锁期后 API/原生 SQL 写覆盖 | 触发器已加；**未执行** |
| N9 撤权后旧请求 | **未执行** |
| N10 事务失败部分提交 | **未执行** |
| R1 配置变化后旧预检锁期 | **未执行** |
| R2 锁期后写入拒绝 | **未执行** |
| R3 适用性确认与事实竞争 | **未执行** |
| R4 重复配置/响应丢失 | **未执行** |

### 测试计数

```text
SQLite R01/R03:              15 passed
SQLite B04 + P0:             48 passed
PG14 adapter + integration:  20 passed
PG14 C01-R3A dedicated:       1 passed
```

PG 角色：迁移 `b08_admin`；业务/专项 `b08_runtime`；worker `b08_worker`。  
PG 版本：14.24；PG17 未验证。

### 候选哈希（SHA-256）

```text
subledger/service.py
FCDB0E78BC686905E9562F973CBA6081DCAD1DA47EA32B89A3E3CC3C69400B9D

subledger/models.py
B3C792CF7643089759FEA90C0C224293DD142B51FF78B70E859F20C840B723D2

PG 130 15F9C34389D9AD2D0BB19B4CF69528AE3450C0F6809B3AEDFBD6C569AFBDBC43
PG 131 8F5EA30FA86ED101C9DC55D8E8CB893B0236883D66E77DD4CACD2CFC58EA6ED6
PG 132 D7031644DA833CF73C848B065375BA5BE8B37AA29DFD8208348EC71244B6D70A
PG 133 7475702B5160D407B166F0A7FCBEA264AD5885665BF4C78C8B781DF77D2927EF
PG 134 38961690081FB939C7D7C711D200743749DA154AD67620A24244AAC5D7FA65F5
SQLite 089 33C2009E32FF02F830AC57EF68D65EB7B0230320056AE69B4D7BB9CB626C7869
SQLite 090 DF1A58E2EE40B98E1803BA9A89FA7C6682064205F60300AA10E27233F524A1A3
SQLite 091 526D105EC23DF2EE7C5D187D33F9EB47B77B39BD02C38FF49850AEF3BC84BC6D
SQLite 092 0B80FE4AEDB444765936D1AD2158F1670F00CCFA3ED8A0939C2302F293971398
test_c01_r3a_postgres.py
7A76F8F4B6A3968B29178ED5F13DCFBA456666767ECBAD799B5B1CC1A8D141A9
```

### 结论

C01-R3A **未闭环**：N6/N7/N8/N9/N10 与 R1–R4 尚未执行，A2/A3 未覆盖；  
因此**不允许进入 C01-R3B**，也不能把 C01 标记完成。

---

## 8. C01-R2 环境调查

### PG17

执行：

```powershell
wsl -d Ubuntu-22.04 -- bash -lc `
  "which pg_ctl; ls /usr/lib/postgresql; apt-cache policy postgresql-17; docker --version"
```

实际：

- `/usr/lib/postgresql` 只有 `14`；
- `apt-cache policy postgresql-17` 无可用包输出；
- `docker` 未安装；
- 本机无法启动独立 PG17。

可选方案：

1. 在 WSL 添加 PostgreSQL 官方 apt 源并安装独立 `postgresql-17` 集群（端口/数据目录隔离）；
2. 使用独立容器运行 PG17（需允许安装/拉取镜像）；
3. 由外部提供隔离 PG17 DSN。

本轮未执行安装，避免未经授权改变系统包/网络环境。

### PG16

CI 仍是 PG16 配置，本机未执行；PG16 未单独验证。

### 完整恢复

未执行。缺少：

- 全新隔离 PG17；
- 项目支持的私有测试对象存储；
- 测试身份/角色；
- 原件/身份/RLS/列级授权联合恢复目标。

不能用 SQLite 或 mock 存储替代“完整恢复通过”。

### 浏览器故障

正常 inline review/intake 已通过；F12 stale、F13 running/worker 重启、F14 原件 5xx 的专门故障测试未执行。

---

## 9. 下一步

- C01-R1 继续：覆盖状态/原件引用、F04 辅助实体与逐维度、PG 并发/原生 SQL 负向；
- C01-R2 环境：PG17、完整恢复、浏览器故障；
- 不进入 C02 集成验收，直到 C01 控制科目/银行模型契约稳定。
