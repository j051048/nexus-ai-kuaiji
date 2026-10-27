# Q-S04 同候选最终验收 — 本地记录（2026-10-27）

- 候选：`tmp_test/qr01-integration`（主工作区不动）
- 基线：`32e3042d`；本轮改动未提交、未推送
- 记录时工作区：54 条改动（含本轮格式化 27 个文件 + Q-S01…Q-S03C 与 Q-R02 遗留）

## 1. 已完成

### 1.1 格式与静态（CI 范围）

```
ruff format nexus_backend/app services/api_gateway scripts        → 27 files reformatted, 280 unchanged
ruff format --check nexus_backend/app services/api_gateway scripts → 307 files already formatted
ruff check nexus_backend/app nexus_backend/tests scripts           → All checks passed!
```

27 个文件是 HEAD 上既有的格式债（`app/tax/estimate.py`、`app/reports/service.py` 等从未被我改过也不合格），属其他智能体语义，**只做格式化、未改语义**；`nexus_backend/tests` 仍是 219 个存量文件不合格——CI 不覆盖该目录，故不顺手全库重排。

### 1.2 后端回归（SQLite）

```
test_posting_service, test_q03_continuous_books, test_q04_client_signing,
test_q05_export_consistency, test_qr02_financial_correctness,
test_qs02_range_consistency, test_monthly_delivery_package
→ 66 passed, 1 warning in 366.78s
```

### 1.3 PostgreSQL 16/17

```
ACCOUNTING_ADMIN_DATABASE_URL=...:25531/qs01_gate  status --strict → exit 0（165–167 已应用）
ACCOUNTING_ADMIN_DATABASE_URL=...:25532/qs01_gate  status --strict → exit 0（同上）
```

两版本均运行 16.15 / 17.11，`fsync`/`synchronous_commit`/`full_page_writes` 均为 on（Q-S00 记录）。本轮为**纯格式化**改动，未重跑 PG 上的业务套件；完整复跑记录见 Q-S01 一节（两版本各 53 passed）与 Q-R02 一节。

### 1.4 浏览器核心集合（正式 runner，同候选）

```
node scripts/run_e2e.mjs --mode dev     → 106 passed / PASS / 0 gate issues（4.5m）
node scripts/run_e2e.mjs --mode preview → 106 passed / PASS / 0 gate issues（4.4m）
```

这两次运行在 Q-S03C 代码完成之后执行，覆盖四条既有链路：业务提交→审批→复核→导出、正式账簿与 CSV、月度交付包与客户签收、导航/返回栈。

## 2. 单条全链路用例（已补齐）

`e2e/qs04-chain.spec.ts`：同一会话内完成 财务导入流水 → 财务生成候选分录（制单责任落在财务）→ 会计在真实界面「跨企业批量复核」勾选复核包 → 生成预览 → 勾选人工确认 → 确认 → 导出 → 复核状态断言。

```
npx playwright test e2e/qs04-chain.spec.ts --reporter=line
→ 1 passed (16.2s)；prettier --write 后再跑一次 1 passed (16.6s)
```

关键点：先前判为「阻断」的现象（复核包全部 disabled）是**用例建模错误**，不是产品缺陷——原用例用会计自己生成草稿再让同一会计复核，触发器按设计把 maker 落成生成者，独立复核因此拒绝。改为财务制单、会计复核后链路自然打通，无需改产品。

同时修正 `e2e/stage11-review-packages.spec.ts`（同一错误建模 + 会计入口收在折叠组里导致不可达）：

```
npx playwright test e2e/stage11-review-packages.spec.ts --reporter=line
→ 1 passed (15.1s)
```

## 3. 声明边界

- 全链路用例覆盖「制单→复核→导出」；「正式账簿读模型」「月度交付包→客户签收」由核心集合内的 `formal-books-*`、`q04-client-signing.spec.ts` 分别覆盖，未塞进同一条用例（跨用例共享状态会让失败定位变差）。
- 链路由 dev 模式验证；preview 模式跑的是核心 106 项。
- 远端 CI 未执行；本轮全部结论仅代表本地隔离环境。
- PostgreSQL 侧本轮只做了 `status --strict`（纯格式化改动不改语义），业务套件复跑记录见 Q-S01/Q-R02。

## 4. 结论

同一候选上，七个包要求的证据齐备：迁移不可变性、连续账簿一致性、前端范围/撤权、月度包身份、复核资格接线、格式化与回归、核心浏览器集合 dev+preview 各 106、单条全链路用例通过。**Q-S04 完成**，改动仍未提交、未推送。
