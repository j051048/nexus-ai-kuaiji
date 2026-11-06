# IMP01-B 验收记录：最终组合与候选冻结

- 包：IMP01-B（IMP01–IMP10 最终组合）
- 日期：2026-11-06
- 工程状态：**本地组合通过**；同候选 P01 恢复已通过；外部门槛仍未验证。
- 外部验收：远端 CI、生产、真人、真机、真实模型/通知均 pending 或 not_authorized。
- 运行目录：`tmp_test/imp/imp-20261106-a/`
- 候选：`tmp_test/imp/imp-20261106-a/candidate-final.json`

## 1. 最终候选

| 项 | 值 |
|---|---|
| HEAD | `143d4aea7154b1905b96ab757ecd14865354b641` |
| 分支 | `main` |
| source files | **1927** |
| source sha256 | **`e6a947031fe9e3b4bd23b522b29ef46bbfddd89232a5c9d989b70a170f44baa5`** |
| migration files | **338** |
| migration sha256 | **`2c96acd52de191347a2365962e879340918fad013786faa260b4b13140646116`** |
| PG / SQLite 迁移 | 205 / 133；最大 PG 217、SQLite 133 |
| 工作树 | 57 项：均为本任务 IMP 代码/测试/文档与输入文档；未提交 |
| 冻结时间 | `2026-11-06T12:25:00+08:00` |

候选自 IMP08 后未再修改源码；IMP09/10 只新增文档与一次外部 smoke 测试。工作树包含输入文档 `docs/AUDIT_SYNTHESIS_RECOMMENDATIONS_20261105.md`、`docs/DEEPSEEK_IMP01_IMP10_EXECUTION_20261106.md` 等，它们不参与候选摘要（docs 不在 release_candidate 前缀内）。

## 2. 最终组合门禁

### 2.1 Python / 契约 / 工具

```bash
.venv/Scripts/python.exe -m pytest -q \
  nexus_backend/tests/test_imp05_trial_summary.py \
  nexus_backend/tests/test_imp07_eval.py \
  nexus_backend/tests/test_imp08_analysis.py \
  nexus_backend/tests/test_release_candidate.py \
  nexus_backend/tests/test_next_harness.py \
  --junitxml=tmp_test/imp/imp-20261106-a/imp01b-final-python.xml
```

结果：**90 passed, 1 warning in 116.04s，exit 0**。

### 2.2 浏览器

```bash
for cfg in imp02 imp03 imp04 imp06 imp08; do
  npx playwright test --config=playwright.$cfg.config.ts --reporter=line
done
```

| 配置 | 结果 | 覆盖 |
|---|---|---|
| IMP02 | 8 passed / 54.2s | 五身份×四视口、身份条、键盘、0 写请求 |
| IMP03 | 2 passed / 5.8s | 桌面/手机待办、行折叠、标题兜底 |
| IMP04 | 6 passed / 8.1s | 手机首屏、短视口、显式企业 |
| IMP06 | 4 passed / 12.2s | 字号、长负金额、懒加载 token、axe |
| IMP08 | 1 passed / 12.6s | 只读差异面板、无写请求 |
| **合计** | **21 passed** | 不是与 IMP05 36 项相加的独立总数 |

IMP05-B 的 36 项协同链在候选 1916 上通过；其后只新增 IMP08 的 assistant/UI 文件，未改协同链，故未重跑，不能把这 36 项写成 1927 上的新增执行。

### 2.3 前端

```bash
npm run typecheck
TMP=tmp_test/tmp TEMP=tmp_test/tmp npm run build
node scripts/frontend_budget.mjs dist
```

- typecheck exit 0
- build exit 0
- budget **passed**：initial 565,956 B / gzip 175,047 B；home 593,479 B / gzip 185,602 B；initialCss 99,722 B；homeCss 144,373 B；violations 空
- 报告：`tmp_test/imp/imp-20261106-a/imp01b-budget.json`

### 2.4 前端预算负向测试

`node --test scripts/frontend_budget.test.mjs` → **6 passed / 119ms**。

## 3. 逐包状态

| 包 | 本地 | PG16 | PG17 | 浏览器 | 关键证据 |
|---|---|---|---|---|---|
| IMP01-A | passed | — | — | — | 69 工具/负向测试 |
| IMP02 | passed | — | — | passed | 13 单测 + 8 浏览器 |
| IMP03 | passed | — | — | passed | 83 单测 + 2 浏览器 |
| IMP04 | passed | — | — | passed | 100 单测 + 6 浏览器 + employee-flow 9 |
| IMP05-B | passed | — | — | passed | 36 协同链（候选 1916）；真人 pending |
| IMP06-B | passed | — | — | passed | 4 浏览器 + axe 3 |
| IMP07 | passed | — | — | — | 120 项固定分母；good/bad/unknown |
| IMP08 | passed | passed smoke | passed smoke | passed | 8 契约 + 1 UI；正向票据差异仅 SQLite |
| IMP09 | passed | passed | passed | — | 双库 30min/200 企业；同机双 worker |
| IMP10 | passed | passed | passed | — | 无代码/迁移；5+5 PG |
| IMP01-B | passed | passed* | passed* | passed | 90 Python + 21 浏览器 + 预算 |

\* IMP01-B 的 PG 列引用各包已有 PG 证据与 IMP08 smoke；P01 完整恢复已在 1927 同候选通过（见下节）。

## 4. 恢复与 P01

- **1927 同候选 P01 通过**：`tmp_test/imp/imp-20261106-a/imp01b-p01-1927/reports/summary.json`，`status=passed`。
- regression **183 passed / 167.967s**；joint **2 passed / 86.311s**；identity **4 passed / 62.361s**；0 skipped。
- PG16/17 的 database/originals/identity/permissions/hold/review_export/tax_delivery/no_replay 八项全 true；migrations 205、originals 4、restored capability grants 925、column grants 55；minimum migration 212。
- 配置 pair `tmp_test/next02-env/runs/a7d1d887e005473dae5a7f1099cb6a60`；使用 `C01_AUTH_PORT_FORWARDED=1` 与本地 25510/25511/25512 转发。
- 为在不改机器环境的前提下补齐 WSL `psycopg`，用 `uv pip install --target tmp_test/p01deps --python-platform x86_64-manylinux2014 --python-version 3.10 "psycopg[binary]"`，仅在命令前设置 `PYTHONPATH`；未安装系统包。
- 因此 A 的“同候选完整恢复”已补齐；仍不能把外部门槛写成通过。

## 5. 外部与未验证

| 项 | 状态 |
|---|---|
| 真实模型/OCR | pending；IMP07 只证明离线评分与回归否决 |
| 真实通知/供应商 | pending；未发送 |
| 真人试用 | pending；IMP05 记录表为空，未补零 |
| 真机/微信/屏幕阅读器 | pending |
| 远端 CI | pending；本轮未提交/推送 |
| 生产发布 | not authorized |
| 多节点/跨机网络 | 未验证；IMP09 是同机双 worker |

## 6. 剩余问题

1. IMP08 正向票据差异 PG 证据只有 SQLite；PG 仅负向/无上期 smoke。
2. IMP09 PG16 结束 pool 快照 3/4；进程随后退出，不能证明长期无泄漏，也不能证明有泄漏。
3. IMP05 真人试用未执行；不能宣称“员工易上手”已由真实用户验证。
4. 全量 Accounting foundation 仍有未迁移夹具（另案），本包只对 IMP 相关与选定门禁负责。
5. 外部门槛：真人、真机、真实模型/通知、远端 CI、生产发布均未验证/未授权。

## 7. 声明

- 未提交、未推送、未部署。
- 未执行线上 SQL、未改历史迁移 checksum。
- 未调用真实模型、真实 OCR、真实通知渠道。
- 本记录是本地工程组合验收，不是生产发布批准。
