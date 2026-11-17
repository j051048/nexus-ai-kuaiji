# OPT02：本地可复现的门禁修绿与静态检查

先读 `OPT00_MASTER.md`。无前置。

背景：GitHub 上 `Accounting foundation`、`QA fault security and cross-browser gates`、`P01 joint recovery` 三个工作流在 main 上持续失败，只有 `local-quick.yml` 通过。你不推送，所以本批目标是：**让每个工作流里的每一步都能在本地跑通**，并把推送后才能验证的部分明确列出来交给人处理。

## OPT02-A  定位每个失败步骤

**做法。**

1. 只读查看最近一次失败日志：

```
gh run list -L 8 --json databaseId,workflowName,conclusion
gh run view <id> --log-failed
```

`gh` 不可用时，直接读 `.github/workflows/*.yml`，在本地按顺序执行每一步。

2. 在状态文件里列一张表：工作流、作业、失败步骤、失败原因、本地能否复现。

本模块只定位，不修改。

## OPT02-B  前端格式检查

**问题。** 据取证，`verify` 作业卡在 `npm run format:check`，导致后面的单元测试、构建、后端测试都没执行。

**做法。**

1. `npm run format:check` 取得不合格文件清单。
2. **只格式化清单里的文件**：`npx prettier --write <文件1> <文件2> ...`。不跑 `npm run format`。
3. 清单里有其他智能体正在修改的文件（`git status` 显示已修改且不是你改的）时，仍然可以格式化，但格式化前后各跑一次 `npm run typecheck`，并在状态文件列出这些文件。
4. 格式化只产生空白和换行变化。逐个文件 `git diff --stat` 检查，出现逻辑变化立即停下排查。

**验收。**

```
npm run format:check
npm run typecheck
npm run lint
npm test
npm run build
```

五条全部通过。

## OPT02-C  后端格式与静态检查

**做法。**

1. `ruff format --check nexus_backend/app` 取得清单。仓库 `ruff.toml` 设了 `line-ending = "lf"`，Windows 下 CRLF 会造成大量假阳性：先区分真正的格式问题与换行问题。只处理提交 HEAD 中就不合格的文件（用 `git show HEAD:<路径> | ruff format --check --stdin-filename <路径> -` 判断），至少包括 `tax/basis.py`、`tax/costs.py`、`tax/leases.py`、`tax/pilot.py`。
2. 核对本地 ruff 版本与 CI 固定版本一致，不一致先装成一致的版本再判断。
3. `ruff.toml` 的 `select` 现为 `["E9","F63","F7","F82"]`。追加 `F401`（未使用导入）和 `F811`（重复定义），仅作用于 `nexus_backend/app`，修掉报出的问题。测试目录暂不纳入，用 `per-file-ignores` 排除并注释原因。不追加更多规则。

**验收。**

```
ruff check nexus_backend/app
ruff format --check <你处理过的文件>
python -m pytest nexus_backend/tests -q -x --ignore-glob="*_postgres.py"
```

第三条耗时长，完整跑一次并记录用例数和耗时。

## OPT02-D  新增写事务必须声明锁范围的静态检查

**背景。** `Database.transaction(write=True)` 默认 `write_scope="global"`，会跨所有企业串行。存量先不动，但要阻止新增。

**做法。**

1. 新增一条测试，用 `ast` 扫描 `nexus_backend/app`，找出所有 `transaction(` 调用中 `write=True` 且未显式传 `write_scope` 的位置。
2. 把当前清单写成基线文件（按"文件路径 + 所在函数名"记录，不记行号），测试断言"实际集合是基线的子集"。新增未声明的调用会失败；修掉存量后可从基线删除。
3. 基线文件头部写明用途和更新规则。

**验收。**

```
python -m pytest nexus_backend/tests/<新增测试> -q
```

再临时加一处未声明的写事务，确认测试失败，然后撤掉。把这次失败输出贴进状态文件。

## OPT02-E  端到端与 PG 作业

**做法。** 对 OPT02-A 表中剩余的失败步骤（`npx playwright install`、PG 库准备等）逐个本地复现并修复工作流或脚本本身的问题。属于运行环境的问题（网络、缓存、权限、密钥）不要改代码绕过，写进状态文件"需人工处理"。

**需人工处理的固定项（写入状态文件，不由你执行）：**

- 推送后确认四个工作流在同一提交上全绿。
- 在 GitHub 上为 main 启用分支保护和必需检查。
- 后续改为分支加合并请求。
