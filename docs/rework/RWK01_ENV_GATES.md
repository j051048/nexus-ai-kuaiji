# RWK01：依赖对齐、静态检查、体积预算、全量基线

先读 `RWK00_MASTER.md`。无前置。

## RWK01-A  测试环境与锁文件对齐

**问题。** 本机 `python -c "import starlette,fastapi;print(starlette.__version__,fastapi.__version__)"` 输出 `0.35.1 0.109.0`；`nexus_backend/requirements.lock` 要求 `starlette==1.8.0`、`fastapi==0.133.1`。结果是所有使用 `TestClient(..., client=...)` 的用例报 `TypeError`，验收时 60 个问题中约 44 个由此而来。此前状态文件把它记为"环境不兼容，不算失败也不算通过"并继续往下做，这不可接受。

**做法。**

1. 在仓库内建一个独立虚拟环境（放在已被 `.gitignore` 忽略的目录，先确认忽略规则），按 `nexus_backend/requirements.lock` 与 `requirements-dev.txt` 安装。不改动系统级 Python 的包。
2. 记录 Python 版本。锁文件在当前 Python 版本下装不上时，停下并写"阻塞"，列出装不上的包和原因，不自行升降锁文件版本。
3. 写一个 `scripts/check_test_env.py`：比对当前环境与锁文件中每个包的版本，不一致即非零退出并列出差异。
4. 在 `README.md` 或现有环境说明里写明后端测试必须在该环境中运行，以及如何创建。

**验收。**

```
<虚拟环境的 python> scripts/check_test_env.py                     # 退出码 0
<虚拟环境的 python> -m pytest nexus_backend/tests/test_intelligence_http.py nexus_backend/tests/test_accounting.py -q
```

第二条不再出现 `TestClient.__init__() got an unexpected keyword argument`。此后本轮所有后端命令都用这个环境执行，状态文件里写出解释器路径。

## RWK01-B  全量后端基线

**做法。** 用 RWK01-A 的环境完整跑一次，不使用 `-x` 或 `--maxfail`：

```
<python> -m pytest nexus_backend/tests -q -p no:cacheprovider --ignore-glob="*_postgres.py" > tmp_test/rework/RWK01-B/<运行标识>/full.txt 2>&1
```

把结果整理成 `docs/rework/BACKEND_BASELINE.md`：总数、通过、失败、报错、跳过、耗时；每个失败和报错一行，含用例名、错误的第一行、你的初步归类（环境 / 旧测试与新控制冲突 / 产品缺陷 / 测试夹具问题 / 不确定）。

本模块只建立基线，不修复。

**验收。** 基线文档存在，行数与输出文件中 `FAILED`、`ERROR` 行数一致。

## RWK01-C  静态检查

**做法。**

1. `ruff check nexus_backend/app`：修掉 `nexus_backend/app/accounting/review.py` 中未使用的 `REVIEW_RISK_RULE_VERSION` 导入。先确认它是否本应被使用（例如应写入返回结果）；应使用就用上，不该用就删。
2. `ruff format --check nexus_backend/app` 验收时报 31 个文件。逐个用 `git show HEAD:<路径> | ruff format --check --stdin-filename <路径> -` 区分：HEAD 内容本身不合格的才处理，只格式化这些文件；仅因本机换行符造成的不处理，并在状态文件列出两类清单。
3. 核对本地 ruff 版本与 CI 固定版本，不一致时写明用的是哪个版本。
4. 前端 `npm run lint` 有 6 条"未使用的 eslint-disable"警告：删除这些多余的指令，前后各跑一次类型检查。

**验收。**

```
ruff check nexus_backend/app          # 0 error
npm run lint                          # 0 error 0 warning
npm run typecheck && npm test
```

## RWK01-D  体积预算

**问题。** `npm run build && npm run test:frontend-budget` 失败：`budget:initialGzip`（176,547 > 176,000）、`budget:homeCss`（147,272 > 145,000）。

**做法。**

1. **不得修改 `scripts/frontend_budget.mjs` 中的任何上限。**
2. 找出首屏包里本不该在首屏的内容：对比 `767f7cdf` 与当前的构建产物清单，看是哪些新模块进了入口包（例如新增面板被入口静态导入）。把它们改为按路由懒加载，照 `src/WorkspaceRouteContent.tsx` 现有的 `lazy()` 写法。
3. 首页样式：找出近期加进首页样式入口的新样式文件，把只属于某个面板的样式移到该面板自己的样式文件中随面板加载。删除样式只限于搜索确认无引用的类。
4. 不通过删除功能、压缩可读性或把资源挪到外链来过关。

**验收。**

```
npm run build
npm run test:frontend-budget          # status passed，violations 为空
npm test
```

状态文件贴出改动前后六个体积数字，并说明移出首屏的模块清单。相关页面的 Playwright 用例本机能跑则跑，确认懒加载后页面仍能打开。
