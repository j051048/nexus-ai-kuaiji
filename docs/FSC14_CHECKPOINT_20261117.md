# FSC14 检查点 2026-11-17

## 当前结论

FSC14 部分完成：合同版本围栏、内部复核、正式记账/导出边界和浏览器链已通过；外部权威回执、归档包、两企业跨期链未完成。

## 代码归属

| 文件 | 变更 |
| --- | --- |
| `nexus_backend/app/tax/leases.py` | `recognize/refund` 写 `lease_draft_bindings`；`amend` 指纹显式字段 |
| `nexus_backend/app/tax/lease_guards.py` | 新增审核/记账/导出统一合同版本守卫 |
| `nexus_backend/app/accounting/service.py` | 审核、导出调用 guard |
| `nexus_backend/app/posting/service.py` | 正式记账调用 guard |
| `src/TaxFPanel.tsx` | 删除重复动作、创建后乐观展示、版本/导出 UI |
| `nexus_backend/migrations/sqlite/195_fsc14_lease_draft_bindings.sql` | 新表 |
| `nexus_backend/migrations/postgres/280_fsc14_lease_draft_bindings.sql` | 新表 + RLS |
| `nexus_backend/migrations/postgres/281_fsc14_lease_plan_delete_grant.sql` | 前向 DELETE 授权 |
| `nexus_backend/migrations/sqlite/196_fsc14_lease_plan_delete_grant.sql` | SQLite 空操作 |
| `nexus_backend/tests/test_tax13_leases.py` | 旧草稿审核/记账拒绝 |
| `nexus_backend/tests/test_tax_f_postgres.py` | PG 版本围栏 |
| `e2e/tax-f.spec.ts` | 浏览器版本/导出链 |

## 命令与证据

```text
npm run typecheck
# exit 0

cd nexus_backend && PYTHONPATH=..:. python -m pytest -q tests/test_tax13_leases.py tests/test_posting_service.py
# 13 passed in 32.07s

cd nexus_backend && PYTHONPATH=..:. python -m pytest -q tests/test_tax13_leases.py tests/test_posting_service.py
# 13 passed in 32.24s（租约围栏首次验收）

npx playwright test e2e/tax-f.spec.ts --reporter=line
# 2 passed (1.5m)

PG17 stale 用例
# 1 passed/1.34s; tmp_test/ak05-pg-runs/88de63c4098c49e886300be18a29b71d
PG16 stale 用例
# 1 passed/1.24s; tmp_test/ak05-pg-runs/8895ca350fe5459183b374989a640878
PG17 lease 子集
# 3 passed/2.14s; tmp_test/ak05-pg-runs/c04dcdc0…
PG16 lease 子集
# 3 passed/2.16s; tmp_test/ak05-pg-runs/3e5d6eb0…
```

## 未验证

- 外部 T+ 办理准备/回执/归档包。
- 两企业、连续两期、跨年浏览器链。
- PG 双会话竞争/撤权/期间锁专项。
- FSC14 非空恢复。
- 真实合同、真实用户、真实设备。

## 下一步

1. 不把 FSC14 写为整体通过；FSC12 联合验收时保留“部分完成”。
2. 继续 FSC15–FSC17；若优先补 FSC14，先做外部回执/归档包和两企业跨期浏览器链。
