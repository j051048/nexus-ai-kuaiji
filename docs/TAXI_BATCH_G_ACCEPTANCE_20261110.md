# TAXI 批次 G 本地验收记录（2026-11-10）

状态：**部分通过（G1/G2 本地通过，G3 联合验收未完整执行）**。本记录只覆盖本地虚构数据、隔离 PG16/17、本地身份与真实浏览器；不代表客户 T+、真实模板、真机、远端 CI 或生产验证。

## 候选

- HEAD：`781fd93499dcba7bb76056f0febe19e372202af2`
- source：`2143` 文件 / `882bd4394225081fff1acc375a88e8566ab14edeee62abc188d192f313c885a8`
- migration：`375` 文件 / `882f13d43209dfe1e87e6933137da720628d6c01bcbaf6f6f4b9343cf24eb329`
- 工作区：163 项未提交（批次 A–G）
- 未提交、未推送、未部署、未调用真实 T+/通知/模型。

## G1 / TAX16 外部证据双档

已实现：

- sqlite 152 / postgres 235：`monthly_external_evidence`，报告版本/期间/来源/正文/sha/登记人/核验人/状态，append-only 与独立复核。
- 有 T+ bridge 的账套，月度包必须存在同报表版本的已核验证据，否则 `external_evidence_missing`；冻结 manifest 记录证据指纹。
- API 与 UI：登记、独立核验/拒绝、冻结回显；登记人自审 403。
- 单 PDF 仅作为登记文本，不会因上传自动变成“已独立核验报表”。

证据：

- SQLite：`test_tax16_external_evidence.py` 2 passed/10.56s；`test_tax16_recovery.py` 1 passed/3.68s；与 `test_monthly_delivery_package.py` 合并 **23 passed/74.41s**，`tmp_test/tax-g/tax-g1-combined.xml`。
- PG：`test_monthly_delivery_postgres.py` PG17 **5 passed/14.06s**（`9f4332a9...`）、PG16 **5 passed/13.72s**（`5d67b00b...`）。
- 浏览器：1280/390 **2 passed/25.5s**；截图 `tmp_test/tax-g1-evidence-1280.png`、`-390.png`。

## G2 / TAX17 六阶段办理

- 月度矩阵新增六阶段投影：资料收集、差异核对、凭证准备、会计复核、交付核验、确认归档。它是服务端现有计数的导航投影，不新建业务状态机、不授予权限。
- 每行显示企业/期间/账套/服务单版本；打开后保留月份、筛选与展开位置；当前任务只高亮一个。
- 银行核验不锁期继续由既有 C01/月结守卫负责，未改动。

证据：

- 单测 `src/monthly-stage-guide.test.ts` **3 passed**。
- `e2e/stage17-monthly.spec.ts` 跨企业矩阵 **1 passed/6.8s**，含六阶段当前任务、返回恢复；截图 `artifacts/stage17-monthly-six-stages-desktop.png`、`-mobile.png`。
- `e2e/workbench-focus.spec.ts` **5 passed/22.1s**（返回保留筛选/展开、手机视口）。

## G3 / TAX18 联合验收

已执行：

- 连续两期正式账簿：`e2e/formal-books-range.spec.ts` **2 passed/19.5s**（期初只累加一次、未关账如实标记）。
- G1 外部证据 + 月度包冻结；G2 六阶段导航与返回恢复。

未执行/未通过：

- 两连续期间的完整“收资料→核差异→备凭证→复核→T+核查→归档”同一候选单链，未在浏览器逐期完成。
- 240 车/数百驾驶员、575–1001 行、多银行、退款、跨年、模板升级、撤权、重复导入的联合场景未完整执行。
- PG 目标库 `monthly_external_evidence` 行级恢复为 0（仅模式恢复）；行级恢复由 SQLite `test_tax16_recovery.py` 覆盖，不等同 PG 目标行级恢复。
- 客户真实 T+ 模板/导入、真机、远端 CI、生产发布未验证。

## 最终 P01

- `tmp_test/tax-g/p01-g/reports/summary.json`：**passed**；candidate 2143/`882bd4...`，migration 375/`882f13...`。
- regression 188/165.914s、joint 2/94.524s、identity 4/59.519s，0 skipped。
- PG16/17：migrations 223、max schema 235、originals 4、users 6、capability 1009、column 55；九项 p01_checks 全 true。
- 源/目标 `monthly_external_evidence` 均存在，目标行数 0。

## 结论

G1、G2 本地工程与浏览器通过；G3 只完成部分联合证据，**不得宣称批次 G 整体通过**，也不得宣称 T+ 外部闭环或生产可发布。
