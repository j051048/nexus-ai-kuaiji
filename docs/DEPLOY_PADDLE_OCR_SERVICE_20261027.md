# 部署记账 PaddleOCR 独立服务（2026-10-27）

本文记录把仓库内 `services/paddle_ocr` 部署为 Zeabur 私有服务 `accounting-paddle-ocr`、
并把它接回记账主站的过程，以及期间发生的一次生产事故与恢复方式。全部操作针对
Zeabur 项目 `nexus-ai-kuaiji`（project `6ace1d4ef8156d031963b759`，environment
`6ace1d4e5a13ed952dc2d2b4`）与 Supabase 项目 `ai-kuaiji`
（`qgzketopnkzquotddwha`）。

## 1. 事故与恢复（先发生）

推送 `7278150a`（P01–P03 + E01）后，Zeabur 从 `main` 自动部署 `bc354080`，四个服务在
`12:04Z` 前后全部 `CRASHED` 并被平台挂起。运行日志中的失败原因是：

```
RuntimeError: PostgreSQL 迁移缺失或校验不通过，请使用管理员账号执行 migrate_accounting.py migrate
```

即新镜像要求数据库已应用到迁移 `168_monthly_delivery_followups.sql` 与
`169_tax_workpapers.sql`，而生产库停在 `167`（155 条记录）。

恢复步骤：

1. 用 Supabase Management API（`POST /v1/projects/{ref}/database/query`）按文件原文执行
   两个迁移（两者头部均为 `-- compatibility: incremental`）。
2. 按 `migration_digest()` 同口径（`sha256(text.replace("\r\n","\n"))`）写入
   `accounting.schema_migrations(version, checksum, compatibility)`，库版本变为 `169`、
   157 条记录。
3. `restartService` 逐个恢复：`nexus-ai-kuaiji`、`accounting-worker`、
   `accounting-assistant` 恢复 `RUNNING`，`/api/health` 返回
   `{"status":"ok","storage":"postgres","auth":"supabase"}`。
4. `accounting-migration` 仍停在 10-25 的旧镜像（早于本次推送即已崩溃并挂起，因此新提交
   从未部署上去）。`redeployService` 重建到 `bc354080` 后转为 `RUNNING`。

经验：迁移随代码合入时，必须先在生产库执行迁移再让新镜像上线；平台挂起后不会自动
追赶后续提交，需要显式重新部署。

## 2. 创建 `accounting-paddle-ocr`

- 模板：`createService`，`template=GIT`，`gitProvider=GITHUB`，`repoID=1414990080`，
  `branchName=main`，`rootDirectory=services/paddle_ocr`。这样构建上下文就是该子目录，
  正好匹配 `services/paddle_ocr/Dockerfile` 的 `COPY requirements.lock` 与
  `COPY . /app/services/paddle_ocr/`。
- 服务 id `6ae094ebe88b9f72265cac2b`，DNS `accounting-paddle-ocr`，端口 `8080/HTTP`，
  **不绑定公网域名**，只走 `accounting-paddle-ocr.zeabur.internal:8080`。
- 镜像内置 `python:3.11-slim`、`paddlepaddle`/`paddleocr`、`HEALTHCHECK --start-period=600s`、
  `--workers 1 --limit-concurrency 8`；模型在首次启动时下载到 `/models`。

## 3. 配置对齐

项目共享变量里本来就有整套离线 OCR 配置，因此 worker 侧不需要新增键：

| 变量 | 值 |
| --- | --- |
| `ACCOUNTING_OCR_ENGINE` | `paddleocr` |
| `ACCOUNTING_PADDLEOCR_URL` | `http://accounting-paddle-ocr.zeabur.internal:8080` |
| `ACCOUNTING_PADDLEOCR_ALLOW_HTTP` | `1` |
| `ACCOUNTING_PADDLEOCR_ALLOWED_HOST` | `accounting-paddle-ocr.zeabur.internal` |
| `ACCOUNTING_PADDLEOCR_MODEL` | `PP-OCRv6_small_det+PP-OCRv6_small_rec@px3.7.2-pd3.3.1-det2048-line0-doc0-unwarp0-r1` |
| `ACCOUNTING_PADDLEOCR_MIN_SCORE` | `0.85` |
| `ACCOUNTING_PADDLEOCR_TOKEN` | 共享 64 字符令牌 |

创建服务时自动生成的随机 `PADDLEOCR_API_TOKEN`（43 字符）与共享令牌不一致，导致
`/v1/recognize` 返回 401；已用 `updateSingleEnvironmentVariable` 把服务侧
`PADDLEOCR_API_TOKEN` 改成共享值，两侧一致。

## 4. 验证证据

均在容器内用 `executeCommand` 执行，输入为合成发票图像（不含任何真实票据）：

- 服务自检：`/healthz` → `{"status":"ready"}`；`/v1/recognize` 返回 4 个文本块，
  命中 `123.45`、`3.60`，耗时 2.39 s，模型串与 `ACCOUNTING_PADDLEOCR_MODEL` 一致。
- 容器内私有网络（从 api 容器发起）：`http://accounting-paddle-ocr.zeabur.internal:8080/healthz`
  → `ready`；无令牌调用 `/v1/recognize` → `401`。
- worker → 服务：`health=ready`、`no_token=401`、`with_token=422`（空请求体被校验拒绝，
  说明鉴权通过）。
- worker 真实客户端路径：`AISettings.from_env()` + `OpenAICompatibleProvider` +
  `app.intelligence.ocr._recognize` → 4 块、命中金额与税额、2.51 s、模型串一致。

## 5. 已知问题

1. **容器以 root 运行**：镜像内 `/app`、`/models` 属主为 `10001:10001`，`/etc/passwd`
   也有 `ocr` 用户，但 Pod 的 PID 1（uvicorn）`Uid: 0`；对照 `nexus-ai-kuaiji` 的 PID 1
   是 `10001`。说明 `USER ocr` 在该服务上被平台覆盖，需要平台侧 `runAsUser` 配置或
   改用预构建镜像模板后复核。
2. **共享变量残留**：`ACCOUNTING_PADDLE_OCR_HOST` 仍指向已删除的
   `service-6acef3bfcdea7d055de8a47d`，并且与新服务 id 形成同名重复键。当前代码不读取
   该变量（用的是 `ACCOUNTING_PADDLEOCR_URL`），建议清理。
3. **OCR 停摆窗口**：共享变量一直是 `paddleocr`，但服务被删除后没有实例承接，期间上传
   票据的 OCR 会失败。核对结果：`accounting.ocr_jobs` 共 8 条且全部 `succeeded`，
   无排队/运行中任务，未留下积压。
4. `accounting-migration` 长期崩溃的根因（旧镜像 + 迁移专人账号）本次只是通过重建镜像
   恢复运行，账号与 DSN 的实际配置仍建议用 `migrate_accounting.py provision-worker-login`
   复核。

## 6. 回滚

- 停用 OCR：把 `ACCOUNTING_OCR_ENGINE` 改回 `vision` 并重启 worker，即可回到视觉模型路径。
- 撤销服务：删除 `accounting-paddle-ocr` 服务即可，代码与迁移不受影响。
