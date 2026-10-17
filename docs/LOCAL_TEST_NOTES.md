# 本机测试环境与已知红名单

## 1. 先建环境再谈结果

本机历史上曾出现「17 个失败 + 106 个夹具错误」，经逐条对照确认**全部是全局 Python 环境过旧**
（fastapi 0.109 / starlette 0.35，而仓库声明 `fastapi>=0.133`）。
现在仓库提供可复现环境：

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r nexus_backend/requirements.lock
.venv\Scripts\python scripts/env_doctor.py   # 0=可用于全量测试
```

`requirements.lock` 是在干净 venv 上用 `pip freeze` 生成的精确版本集合
（fastapi 0.133.1 / starlette 1.8.0 / pytest 9.1.1 / ruff 0.17.0）；
修改 `requirements*.txt` 后请在干净环境重新生成：

```powershell
python -m venv .venv-lock && .venv-lock\Scripts\python -m pip install -r nexus_backend/requirements-dev.txt
.venv-lock\Scripts\python -m pip freeze > nexus_backend/requirements.lock
```

## 2. 真环境基线（2026-10-17 实测）

```
.venv\Scripts\python -m pytest nexus_backend/tests -q
→ 1939 passed / 660 skipped / 0 failed / 0 errors（约 19 分钟）
```

skipped 均为需要隔离 PostgreSQL/worker DSN 的集成用例，CI 里会真实执行。
**本机任何出现 failed/error 的运行，先跑 `env_doctor` 排除环境因素，再怀疑代码。**

## 3. 常用命令

| 目的 | 命令 |
|------|------|
| 后端全量 | `.venv\Scripts\python -m pytest nexus_backend/tests -q` |
| 前端单测 | `npm test`（303 项） |
| 类型/风格 | `npm run typecheck` / `npm run lint` / `npm run format:check` |
| 构建 | `npm run build` |
| e2e 快检（约 1–3 分钟） | `npm run test:e2e:smoke` |
| e2e 全量 | `npm run test:e2e` |

e2e 的 webServer 用 PATH 上的 `python` 启动后端；想固定用仓库 venv：

```powershell
$env:PATH = "$PWD\.venv\Scripts;" + $env:PATH
npx playwright test e2e/monthly-briefing.spec.ts --timeout=180000
```

本地 e2e 默认 30 秒超时会在慢机器上假失败，务必显式 `--timeout=180000`。

## 4. 本机已知红名单（与代码无关，以 CI 为准）

以下用例在本机（Windows）无论新旧依赖都会失败或偶发失败；已用 `git stash` 对照证实：

- `e2e/batch1-navigation.spec.ts:46`（导航直达/阻断离开；失败时页面停在首页/历史回退行为，
  与断言超时无关）
- `e2e/stage20-home-loading.spec.ts:60`、`:101`（首页慢读取与角色切换；失败在页面状态而非加载时长）
- `e2e/business.spec.ts:43`（会计确认审核按钮等待超时）
- `e2e/stage8-cases.spec.ts`：与其他 spec 连跑时偶发超时，单独运行通过
- `e2e/stage9-reconciliation.spec.ts`：冷启动曾偶发（导航后标题 5 秒内不可见）；
  已把首次导航断言放宽到 30 秒，清空 vite 缓存冷跑验证通过（保留观察）

排障时不要为这些用例反复做基线对照；以 GitHub Actions（Linux + 同一套 spec）为准。

## 5. CI 口径（`.github/workflows/accounting.yml`）

- `verify`：prettier format:check → eslint → 按 `requirements.lock` 安装 → `env_doctor`
  → ruff check/format → Postgres 迁移与账号开通 → vitest → build → 全量 pytest → PaddleOCR 边界测试；
- `e2e`：4 分片并行（`--shard=N/4`），fail-fast 关闭，典型总时长从 ~25 分钟降到并行后约 1/4。
