# B02 执行报告：核算契约、长期账套、版本化科目与期初准备

日期：2026-10-18
基线：`cc39a916` + B01 第二轮未提交改动
性质：隔离实现与测试；**未提交、未推送、未部署、未切换真实账本**

---

## 0. 结论

B02 的“契约 + 长期账套/科目配置 + 期初准备包”已经在 SQLite/HTTP 层实现并验证；**PostgreSQL 迁移与权限负向测试未运行**，不能宣称数据库边界已验收。

**已完成**：

- 核算政策版本化（准则、纳税人、年度、本位币、有效期）。
- 稳定科目身份、父级/层级/末级、版本化、旧 book 映射。
- 期初包导入、逐行核验、提交、独立核准、冻结、新版本历史。
- 旧流程与正式账本归属不变；无凭证/分录/凭证号/报表。

**未完成/未验证**：

- PostgreSQL 迁移、RLS、写门与越权负向测试。
- 真实受限账号下的并发核准、撤权、企业停用。
- 未结事项与应收/应付控制余额勾稽。
- 缺失/零/不适用的字段级状态。
- 最小前端界面。
- 企业资料交付包与真实恢复演练。

---

## 1. B01 前置验收状态

| 前置 | 状态 | 证据/缺口 |
|---|---|---|
| B01.1 简报金额与有效来源 | **通过（合成回归）** | 8 个 `test_monthly_briefing` 用例；净流出/缴税/退款/冲回/重复来源/跨企业 |
| B01.2 原件备份与恢复 | **部分通过** | 8 类原件注册表、缺件阻断、合成恢复、恢复守卫；真实对象存储/身份恢复未验证 |
| B01.3 审核与数据库边界 | **未验收** | 4 条 PostgreSQL 负向测试 skip；HTTP/撤权/拒绝保持已补；无真实受限 PG 结果 |
| B01.4 外部接入 | **通过（应用层隔离）** | 代码级关闭、旧 URL 404、无副作用、HTTP 202 仅 received；网关访问日志未验证 |

**对 B02 的影响**：B02 的数据库 RLS/写门/并发边界同样未在 PostgreSQL 验证；SQLite/HTTP 结果不能替代。B02 可做契约与隔离实现，不能签收正式启用。

---

## 2. 实际完成范围

### 2.1 数据模型（迁移 104 / 079）

- `accounting_policies`：准则、纳税人、行业、年度起始、CNY、版本、active/retired、有效期。
- `ledger_accounts` + `ledger_account_versions`：稳定身份、编码、父级、层级、末级、类别、方向、辅助要求、版本、有效期。
- `ledger_account_mappings`：旧 book 科目 → 稳定科目/版本。
- `opening_packages` / `opening_package_versions`：期初包头/版本、状态机、幂等键、提交/核准/冻结。
- `opening_lines`：年初、YTD 借贷、启用期期初（有符号分）、辅助 JSON、来源行。
- `opening_open_items`：应收/应付未结事项。
- `opening_checks`：逐项核验证据。
- append-only/冻结触发器：科目版本不可改删；已核准/冻结期初行不可改删。

### 2.2 服务与接口

- `nexus_backend/app/opening/service.py`
- `nexus_backend/app/opening/models.py`
- `nexus_backend/app/opening/router.py`
- `POST/GET /api/opening/policies`
- `POST/GET /api/opening/accounts`，`POST /api/opening/accounts/{id}/versions`
- `POST/GET /api/opening/mappings`
- `POST/GET /api/opening/packages`
- `POST /api/opening/packages/{id}/versions`
- `POST /api/opening/versions/{id}/validate|submit|approve|freeze`

### 2.3 工作流

```
draft → validated → submitted → approved → frozen
  ↑         │           │           │
  └── 新版本/重新导入 ←──┘           └── superseded（新版本核准）
```

- 导入幂等：同 package + 同 key 同内容返回原版本；不同内容 409。
- 核验：期初合计 0、逐行恒等式、末级、重复、科目/政策版本、配置指纹、往来方状态。
- 提交/核准：重新核验；核准人 ≠ 提交人（暂定，待会计负责人确认）；旧 approved/frozen 转 superseded。
- 冻结：内容不可原地修改；新版本通过新版本 + 历史链。

### 2.4 明确未做

- 正式凭证、凭证号、过账、余额引擎、总账/明细账。
- 正式报表、损益结转、月结、税务申报。
- 自动审核、资金支付、真实账本切换。
- 前端页面（只有 API）。

---

## 3. 权限矩阵（已实现）

| 能力 | owner/admin | ledger_manager | 会计/复核 | 员工 |
|---|---|---|---|---|
| 政策/科目/映射/期初读写 | 是 | 是 | 否（B02） | 否 |
| 提交 | 是 | 是 | 否 | 否 |
| 核准/冻结 | 是（≠提交人） | 是（≠提交人） | 否 | 否 |
| 历史 book 读取 | 原 book 权限 | 原 book 权限 | 原 book 权限 | 原客户范围 |

服务层每次请求重新校验 firm/company/ledger 管理层/ledger_managers；企业停用返回 404；非管理层返回 403。

---

## 4. 测试与结果

执行环境：Windows 本机 SQLite + FastAPI TestClient；**无 PostgreSQL 测试 DSN**。

```powershell
$env:PYTEST_PLUGINS='tmp_test.pytest_plugin'
python -m pytest nexus_backend/tests/test_opening_service.py -q ...
python -m pytest nexus_backend/tests/test_opening_api.py -q ...
python -m pytest nexus_backend/tests/test_stage10_ledgers.py -q ...
```

结果：

- `test_opening_service.py`：**5 passed**
- `test_opening_api.py`：**1 passed**
- `test_stage10_ledgers.py`：**10 passed**
- 合计：**16 passed**（本轮 B02 定向）

覆盖：

- 期初包 validate/submit/approve/freeze；冻结后直接 SQL 改行被触发器拒绝。
- 核准人=提交人被拒绝。
- 同 key 同内容幂等、同 key 不同内容 409。
- 科目版本变化后旧核验失效，提交被拒绝。
- 父级/子级/父级自动转汇总；跨账套父级、成环、重复编码被拒绝。
- 映射、跨账套映射、非管理层读取拒绝。
- 未结事项往来方校验。
- HTTP 政策/科目/包/导入/核验与员工拒绝。
- 旧 ledger 管理测试继续通过。

未运行：

- PostgreSQL 迁移 104、RLS、写门、append-only 触发器。
- 真实受限账号并发核准、撤权、企业停用。
- 前端类型检查/构建（未改前端）。
- 全量 `nexus_backend/tests`。

---

## 5. 会计样本与确认状态

| 样本 | 状态 |
|---|---|
| 期初启用（银行/应付） | 合成测试通过；**待会计负责人确认** |
| 年中启用（年初+YTD=期初） | 合成测试通过；**待确认** |
| 正常贷方余额 | 合成测试通过；**待确认** |
| 退款/费用冲回 | B01.1 已验证；**待确认** |
| 跨期收付/预收预付 | **未实现** |
| 折旧/摊销 | **未实现** |
| 损益结转影响 | **未实现** |
| 跨年 | **未实现** |

开发 AI 只生成合成样本与待确认预期，没有宣称会计负责人已确认。

---

## 6. 历史兼容与回退

- 未修改已应用迁移；新增 PG 104 / SQLite 079。
- 旧 `books/drafts/sources/accounts` 未改；稳定科目与映射是新增层。
- `ledger_account_versions` append-only；旧 book 科目继续服务现有模板和草稿。
- 应用回退：旧版本可忽略新表；新版本写入的新表在回退后不参与旧流程。
- 数据回退：新增表可保留；不得删除历史。迁移 104 为增量，未执行生产。
- **未验证**：PostgreSQL 迁移升级、旧数据读取、回退。

---

## 7. 剩余风险与待决策

1. **B01.3 PostgreSQL 未验收**：B02 的 RLS/并发/写门不能签收。
2. **核准人分离政策**：当前暂定“提交人≠核准人”，需会计负责人确认；未确认前标“暂定”。
3. **未结事项勾稽**：只有往来方合法性校验，没有控制科目余额勾稽。
4. **缺失/零/不适用**：当前 API 必填，未提供字段级三态；不把缺失当零，但也没有“不适用”标记。
5. **模板兼容**：旧模板仍用 `account_code`；未自动迁移到稳定科目，避免改变历史。
6. **前端缺失**：只有 API，没有账套/科目/期初操作界面。
7. **真实恢复**：B02 新表未接入一次真实备份恢复演练；B01.2 注册表未自动包含新表（当前新表没有 `storage_key`，不需要）。
8. **未做年份截止/跨年**：期初包只按目标期间；跨年样本未实现。

---

## 8. B03 可依赖接口

- `accounting_policies`（当前 active 政策）
- `ledger_accounts` / `ledger_account_versions`（稳定科目、版本、辅助要求）
- `ledger_account_mappings`（旧 book 科目 → 稳定科目）
- `opening_packages` / `opening_package_versions`（approved/frozen）
- `opening_lines` / `opening_open_items` / `opening_checks`

B03 必须自行实现凭证、编号、过账、余额、账簿、结账和报表；B02 不提供“正式启用”或记账接口。

---

## 9. 完成状态分类

| 项 | 分类 |
|---|---|
| 核算政策/科目身份/父级/版本模型 | **已实现，SQLite 已验证** |
| 旧 book 科目映射 | **已实现，SQLite 已验证** |
| 期初导入/核验/提交/核准/冻结 | **已实现，SQLite/HTTP 已验证** |
| 冻结不可变 | **已实现，SQLite 触发器已验证** |
| PostgreSQL 迁移/RLS/并发 | **未验证** |
| 未结事项控制勾稽 | **未实现** |
| 跨年/折旧/损益结转样本 | **未实现** |
| 最小前端 | **未实现** |
| 会计政策/样本确认 | **待会计负责人确认** |
| B01.3 前置 | **未验收** |

**结论**：B02 可以作为“期初准备技术原型”继续评审，但不能称为正式账本能力完成，也不能进入真实企业切换。
