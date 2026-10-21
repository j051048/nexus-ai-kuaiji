# R5 执行证据

HEAD：`baf3f414` + 工作区未提交修改。

## R5-0

- 已记录基线/归属；
- AG01 接收 pending，生产代码未动；
- 未修改 C01、迁移、PG 投影。

## R5-2

- 深层证据隔离：`RunView` 的 steps/evidence 使用 `deepcopy`；
- 接纳的 `result.value` 深拷贝，外部保留对象不能回写；
- 新增 A01 嵌套证据篡改测试；
- R4 的 known/unknown、非法 usage、冲突 handoff、语义快照保留。

```text
test_ag04_investigation.py: 32 passed
```

## R5-3

- `evaluate()` 不再把 missing/unknown 列表覆盖成整数；
- 增加 `missing_count` / `unknown_count`；
- `evaluation_report(evaluate(...))` 可生成 JSON/Markdown，不再 TypeError。

```text
test_ag05_examples.py: 24 passed
```

## 组合

```text
AG02+AG03+AG04+AG05+H01: 113 passed
ruff: pass
```

## 未完成

- R5-1 UX 报告 U01/U02 浏览器红测与修复：未执行；
- R5-2 A02 缺省 usage、A03 调用/证据计费、A04 组合快照、A06 恢复绑定、A07/A08 全矩阵：未完成；
- R5-3 E02–E08 必经身份链、自报评分、双侧完整性、检索泄漏、ContextPack、逐样本报告：未完成；
- R5-4 UX-R2B 全部：未执行；
- 因 R5-1 未通过，本轮不能宣布 R5 包完成或进入后续门槛。
