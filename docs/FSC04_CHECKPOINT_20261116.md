# FSC04 检查点：行业契约与导入内核

日期：2026-11-16
状态：**部分通过（本地/PG16/17/浏览器；契约+导入信封+广告收入草稿）**；不得写成 FSC04 整体完成。

## 已完成

- `fsc_industry_contracts`（sqlite 168 / postgres 254）：
  - 企业、行业、版本、状态 `draft|confirmed|active|superseded|inactive`；
  - `processor_code` 白名单：出租车规费、广告项目收入、物业分期、商贸库存、医院汇总；
  - `capabilities_json`、`source_contracts_json`、`evidence_policy_json`、fingerprint；
  - 非 draft 内容不可改、不可删；事件表 `fsc_industry_contract_events`。
- `FscContractService`：
  - 草稿仅 manager；旧 draft 自动 superseded；版本递增；
  - 独立确认：确认人不能是制单人；版本必须匹配；
  - 生效：必须有同企业同行业 confirmed profile；旧 active 变 superseded；
  - 停用、列表、`active_matrix`（跨企业已生效契约）。
  - 递归拒绝配置键 `sql/formula/script/command/eval/exec`；未知处理器 422。
- API：`GET/POST /api/fsc/contracts`、`GET /api/fsc/contracts/active`、`confirm|activate|deactivate`。
- 前端 `FscContractPanel`：只读展示已生效契约（企业/行业版本/处理器/能力/生效时间），并显示五企业能力矩阵（已启用/未启用及原因）；空态与“契约是配置证据，不是执行脚本”。
- 共享导入信封（sqlite 169 / postgres 255）：
  - `fsc_import_envelopes`：企业/账套/期间、源系统/对象/版本、原件 hash/大小、字段映射/规则版本、单位币种、逐行状态与目标、总采用金额、confirmed/superseded、事件。
  - `FscImportService`：同版本同指纹幂等、同版本异内容 409、低版本 409、新版本替代；跨期/重复行/单位币种/needs_review 原因/目标成对拒绝；独立确认后不可改；行回执含指纹。
  - API `GET/POST /api/fsc/imports`、`GET /api/fsc/imports/{id}`、`GET .../receipt`、`confirm`。
- 第二领域广告项目收入草稿（sqlite 170 / postgres 256）：
  - 只消费 confirmed 导入信封，要求已生效 `advertising_project_revenue` 契约；按 `project` 目标归集 adopted 行，生成不可直接记账的收入草稿；幂等；不创建应收/凭证。
  - API `POST/GET /api/fsc/books/{book_id}/advertising/drafts`。
- 测试：
  - SQLite `test_fsc04_contracts.py` **2 passed/6.12s**（独立确认、自确认 409、重复激活 409、v2 superseded v1、未知处理器/非法键 422、无 profile 409、跨所 403/404、active_matrix、能力矩阵）。
  - `test_fsc04_imports.py` **2 passed/6.18s**（版本/幂等/异 hash 409/supersede/needs_review 阻止确认/确认后不可改/回执稳定/跨期/重复行/跨所/非法键）。
  - `test_fsc04_advertising.py` **2 passed/6.50s**（未确认信封 409、无生效契约 409、按项目归集、幂等、跨所拒绝）。
  - PG16/17 契约+导入+广告草稿各 **4 passed**（PG17 7.30s `42e1d1fd…`；PG16 7.37s `b24c6af6…`）。
  - 组合 `test_fsc04_contracts + imports + advertising + test_fsc03_organization + test_fsc01_profiles` **8 passed/16.79s**。
  - 浏览器 tasks0712 **6 passed/47.7s**；`monitor-1365.png`/`monitor-390.png` 显示五企业能力矩阵。
- 构建：默认临时目录首次因 esbuild 删除 `Access is denied` 失败；改用 `TMP/TEMP=tmp_test/tmp-build` 后 `npm run build` 成功，index 566.19 kB（gzip 175.41 kB）、CSS 99.95 kB（gzip 20.32 kB），失败属宿主临时目录 ACL，不是产品错误。
- 候选：HEAD `767f7cdf…`；source **2254 / `85b6b08e716beecd22d1687569db024f89259bbc559009ff698488de96831577`**；migration **412 / `897af48bd0ee93ff829b8b83692e1cc749c368a90bb051776f565d07e7a40d8a`**。
- 类型检查 `npm run typecheck` 0；构建成功（同上）。

## 共享前端样本

- `GET /api/fsc/imports/recent` manager-only，返回本所最近导入信封及企业/账套名称。
- `FscImportPanel` 挂运行可观测性，只读显示企业/账套、来源/版本、期间、行数、金额、状态、SHA 前缀；空态说明未确认信封不成为正式依据；文案明确“不代表已生成凭证或外部系统已确认”。
- 浏览器 tasks0712 6 passed/51.4s，1280/390 截图更新。

## 未完成（FSC04 整体）

- 导入信封尚未提供前端创建/确认/回执下载；当前只有 API 与后端测试。
- 能力矩阵只区分“生效契约启用/未启用”，尚未展开五项 profile 的细分支持范围与证据政策。
- 广告草稿未接项目合同/预付核销/项目毛利，也未生成正式凭证；不得把它当成 FSC07 完成。
- 跨系统编码映射版本/有效期、客商别名复用未接。
- 配置变更使未完成输出失效尚未接到具体领域输出。
- PG 并发激活/去重、配置与原件恢复、前端创建/确认/停用操作未做；广告草稿与导入/契约的 PG 并发未测。
- 客户真实契约样本、真实 T+/行业系统未验。

## 结论

行业契约的版本、独立确认、生效/替代/停用与只读投影已具备本地和 PG 证据；共享导入内核与第二领域仍未完成，因此 FSC04 不能宣告通过。
