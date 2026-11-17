# FSC 执行状态

| 批次 | 工程状态 | 外部验收 | 说明 |
| --- | --- | --- | --- |
| FSC01 | 本地/PG/浏览器通过 | 缺客户资料 | 档案 API/矩阵/指标/雷达完成；PG16/17 档案迁移/RLS 通过；15 人目录已建；五行业档案待客户确认 |
| FSC02 | 本地/浏览器/PG16/17 通过；P01 未跑 | 缺真实客户规则/银行/T+ | 规费 canonical name、来源分页、应收对象、到账分配/撤销/退款、多银行/合并/跨期拒绝、审核政策版本绑定、代收独立依据、来源逐行回执、PG 双会话核销串行化、PG16/17 8 passed、SQLite 恢复已做；同候选 P01 未跑 |
| FSC03 | 本地/浏览器/PG16/17 通过 | 缺真实组织数据 | 只读组织投影、服务计划/账套角色/缺口、指派不授权；PG16/17 各 1 passed |
| FSC04 | 本地/PG16/17/浏览器部分通过 | 缺客户真实契约/行业系统 | 行业契约、共享导入信封、五企业能力矩阵、广告收入草稿完成；导入/契约并发、配置失效、完整恢复、前端导入操作未做 |
| FSC05 | 本地核心/PG16/17 部分通过 | 缺客户证据政策/补件往返 | 分动作证据政策/例外/评估/只读面板完成；修复历史 SQLite 162 外键列序缺陷（新 173）；缺件一键建普通补件任务/复用/政策绑定已完成；财务验收→重查→回原队列、五行业反例、前端策略操作、完整恢复未做 |
| FSC13 | 本地/PG16/17/浏览器/恢复通过 | 缺真实 T+ 外部回执/客户 | 结转权威 internal/tplus_voucher/hybrid、外部证据核对、内部结转门禁、前端面板；SQLite 5 passed、组合 18 passed、PG16/17 各 1 passed、浏览器 2 passed、恢复 1 passed；FSC14 部分完成；FSC15–FSC17/FSC12 未做 |
| FSC14 | 本地/PG16/17/浏览器部分通过 | 缺真实 T+ 回执/客户/归档 | 合同版本绑定、审核/记账/导出围栏、旧草稿拒绝、前端重复动作修复；SQLite 13 passed、PG16/17 stale+lease 子集通过、浏览器 2 passed；外部回执/归档包/两企业跨期链/恢复未做 |
| FSC15 | 本地/PG16/17/浏览器/恢复核心通过（单企业） | 缺两企业隔离/真实客户费率 | 费率卡草稿/独立确认/生效/续期/停用/历史/影响；重叠/停用后复核拒绝；跨年 PG；前端生命周期与竞态修复；SQLite 20 passed、PG16/17 各 3 passed、浏览器 2、恢复 1 |
| FSC16 | 本地/PG16/17/浏览器/恢复通过 | 缺真实客户合同/工单/T+ 归档 | 包干合同版本/独立确认/生效；额度占用/释放/历史；跨所/跨账套拒绝、并发单笔成功；SQLite 15 passed、PG16/17 各 4 passed、浏览器 2、恢复 1；两企业浏览器/跨年全链未做 |
| FSC17 | 本地/PG16/17/浏览器/恢复部分通过 | 缺真实保险单/合同/原件/T+ | 资产—车辆稳定关联、版本/指纹、折旧 aux/source_ref、撤销历史、审核/记账/导出、恢复；SQLite 3 passed、PG16/17 各 1 passed、浏览器 4 passed；保险单唯一计提/多车保单/退保处置/两期跨年未做 |
| FSC12 | 本地同候选联合部分通过 | 真实工时/双库恢复/容量/真实外部未验 | FSC13–17 同候选 SQLite 52 passed（含五人正式过账）、PG16/17 各 21 passed（含两家企业跨年租赁草稿隔离）、浏览器 12 passed、前端 1403 tests；完整恢复 blocked；SQLite 50/100 并发读 p95 16.9s/29.6s 未达容量目标；真实 T+ 未做 |

## FSC01 证据

- 归属：`docs/FSC_OWNERSHIP.md`
- 能力映射：`docs/FSC_CAPABILITY_MAP.md`
- 15 人目录：`docs/FSC_SERVICE_CATALOG.md`
- 验收：`docs/FSC01_ACCEPTANCE.md`
- 离线五行业档案：`docs/fsc/five_company_profiles_20261116.json`

## 当前候选

- HEAD `767f7cdf7c717c98d3516a0e31b53bd8cd9ae1a9`
- source `2358 / 97835ffdadb3588c9bffd0239f77a01761028069f728bb53f10da117818c4d0f`
- migration `469 / d266a53a73a5a3237cfe6e3676497acc43294ddb6d25466dbf88fb5b5fefb6d7`

## FSC03 PG 证据（2026-11-16）

- `test_fsc03_organization_postgres.py`：自建 PG16/17 隔离库各 **1 passed/0.68s、0.66s**；manager 可见本所账套与缺计划告警，operator 403，外部事务所查询为空不泄露目标企业。
- 修复测试夹具：firm 角色 `operator`、book 角色 `editor`，不手工重复插入 `book_access`。
- 证据目录：`tmp_test/ak05-pg-runs/1156335d29954989984e65ac97106ce6`、`1ec16f1c947b4021aee5f9c94b585f8a`。

## FSC02 当前（2026-11-16）

状态：本地工程能力完成；同候选 P01 与真实客户/银行/T+ 未验，不宣布发布完成。

已完成：
- 规费辅助值 canonical name；运输主/关系/班次变化使旧审批失效；PG 248 同样校验名称。
- 来源文件/行分页完整可达；原件按 SHA/大小校验；来源行逐行回执可下载并校验指纹。
- 规费复核落库应收对象；到账分配/撤销/退款、多银行/一银行多应收/跨期拒绝、代收独立依据、审核政策版本绑定均接通；PG 双会话核销串行化。
- SQLite 组合回归 42 passed/79.21s；PG16/17 `test_tax06_fees_postgres.py` 各 8 passed；浏览器 tax06 2 passed、tax04 4 passed。
- 恢复用例覆盖应收/分配/退款/来源原件；同候选 P01 尚未运行。

候选：source 2238/`22886fd7f96b5aa0f58df0798e145c67ab87b9661cd1df2eadca483b96e00f47`，migration 406/`53c3da09c53f89c6dbb465514287f67d407b822e3405c7773fb84f322dacc5aa`。



## FSC04（2026-11-16，部分通过）

- 行业契约版本/独立确认/生效/替代/停用 + `active_matrix` + 五企业能力矩阵；SQLite 2 passed、PG16/17 契约+导入+广告各 4 passed、浏览器 tasks0712 6 passed。
- 共享导入信封 sqlite 169/postgres 255：版本/幂等/替代/逐行状态/单位币种/目标/回执；SQLite 2 passed、PG16/17 各 1 passed。
- 第二领域广告项目收入草稿 sqlite 170/postgres 256：只消费 confirmed 信封与生效广告契约，按 project 归集 adopted 行；SQLite 2 passed、PG16/17 各 1 passed。
- 只读面板 `FscContractPanel` 显示生效契约与能力启用/未启用原因；未确认草稿不驱动业务。
- 组合 FSC01/03/04 后端 8 passed/16.79s；构建成功；当时候选 source 2254/`e376ce06…`、migration 412/`897af48b…`。
- 未完成：导入信封前端创建/确认/回执、profile 细分能力/证据政策、项目合同/预付/毛利、配置失效联动、PG 并发激活/去重、配置+原件恢复、客户真实契约。


## 2026-11-17 FSC17（部分通过）

- 资产—车辆稳定关联：`periodic_asset_vehicle_links` 每资产 active 唯一，保留 source version/fingerprint、撤销历史；PG RLS/FORCE。
- `AssetVehicleLinkService` 关联/撤销/摘要/按车辆查询；同车幂等、异车 409、员工/跨公司拒绝；写审计。
- 折旧草稿 aux 写车辆外部编号/版本/指纹，`source_ref` 追加 `;vehicle:<external>@v<version>`；输入指纹纳入车辆来源。
- 修复：`AssetVehicleLinkEditor` 双重 JSON stringify 422、旧响应覆盖新关联竞态；资产指纹未含车辆导致复核误报；aux 键顺序导致冻结行比较失败。
- SQLite `test_fsc17_links + fsc17 recovery` 3 passed/12.32s；PG16/17 各 1 passed；浏览器 `finance-operations` 4 passed/43.5s；typecheck/Prettier/ruff 通过。
- 候选 source 2358/`05e39195…`、migration 469/`d266a53a…`。
- 未完成：保险单/合同/原件绑定、单一保险计提守卫、多车保单分配、退保/处置/漏提/尾差、两期跨年、两企业浏览器、PG 并发、恢复 hold/解除；真实客户/外部数据未验。
