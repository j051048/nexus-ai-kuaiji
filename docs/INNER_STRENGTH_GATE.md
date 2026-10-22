# Inner Strength Release Gate

## 入口

```powershell
.\.venv\Scripts\python.exe scripts/check_inner_strength.py
```

可选分组：

```text
p0,ak05,ak04,attempt,offline
```

## 输出

每次运行写入：

```text
tmp_test/inner-gate/<UTC>/report.json
tmp_test/inner-gate/<UTC>/<group>.log
```

报告包含：

- HEAD；
- 关键文件 SHA-256；
- 分组列表；
- 每组 returncode；
- log 路径；
- 最后三行结果。

## 当前基线

```text
HEAD d4fc7b8e
p0          70 passed
ak05        28 passed
ak04        43 passed
invariants  75 passed / 13 skipped（PG 无 DSN 时跳过）
attempt     51 passed
offline     262 passed
```

## 约束

- 只运行现有离线 runner；
- 不接真实模型；
- 不访问线上；
- 不替代完整 PG 矩阵；
- 不把测试数量当作发布结论。
