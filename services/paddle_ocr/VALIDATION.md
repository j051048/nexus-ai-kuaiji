# 独立 OCR 服务验收记录

## 当前源码恢复：只完成本地离线契约

- 来源：本仓库 `2b9170fb^` 的 `services/paddle_ocr/` 与 `third_party/PaddleOCR/`，恢复前目录不存在，未覆盖并行工作。
- 恢复固定 SDK、Linux Python 3.11 带哈希依赖锁、六个模型文件 SHA256 manifest、原 Apache-2.0 LICENSE、来源与 NOTICE。没有复制或下载模型权重。
- 先新增 4 个负向用例，旧实现全部失败：布尔/字符串置信度及布尔/字符串坐标经 `float(...)` 被接纳。红测证据：`tmp_test/offline-pytest/16a2ba6ee0a14a8eb60e10ebe488ba57/results.xml`。
- 修正为严格实数检查，仍支持 SDK 使用的 NumPy 实数；保留非有限数、范围、几何、图像大小、取消与并发锁等原有检查。
- 运行 `python scripts/test_accounting_offline.py services/paddle_ocr/tests/test_service.py -q`：**39 passed，1 个 StarletteDeprecationWarning**。绿测证据：`tmp_test/offline-pytest/e9307eaa859641c5a732e1a615f8a9ff/results.xml`。
- `ruff check services/paddle_ocr` 与 `ruff format --check services/paddle_ocr` 通过。
- 本轮未安装依赖、未下载模型、未调用真实模型或线上服务，未启动数据库；未执行 Docker 构建、Paddle/NumPy 原生推理或真实性能/准确率测试。
- 恢复的启动流程仍通过 PaddleX 在缓存缺失时下载固定模型；这不等于已支持断网冷启动。后续真实部署必须单独验证依赖安装、缓存预配、运行时网络限制和健康检查。

## 以下仅为恢复时保留的历史证据

历史验证日期：2026-10-14。此节原有真实运行记录不能用于宣称当前源码候选已通过真实引擎或部署验收。

- 在隔离 Python 3.11.15 Windows 环境从 PyPI 安装并实际运行 `paddleocr==3.7.0`、`paddlex==3.7.2`、`paddlepaddle==3.3.1`。版本发布确实存在，没有依赖 Git 主分支的未发布接口。
- 从 Paddle 官方 BOS 下载 `PP-OCRv6_small_det` 与 `PP-OCRv6_small_rec`，六个模型文件的实际 SHA256 已进入 `model_manifest.json`，真实运行前验证通过。
- 合成 1400×700 中文票据，真实识别出六行文字，买卖方、金额 `1164.68`、税额 `69.88`、价税合计 `1234.56`、日期均存在；每个文字框在输入图像范围内。预热约 3.39 秒，识别约 2.92 秒。
- 使用真实模型经完整 ASGI HTTP 边界请求，`/healthz` 返回 ready，`POST /v1/recognize` 返回 200，六行文字和三个金额校验通过；请求内识别耗时约 2.78 秒。输入和回执仅为本地忽略目录下的虚构资料，无外部客户数据。
- 另将合成票据放大到 2800×1400，真实模型检测阶段缩放至 2048 后，HTTP 返回坐标仍对应完整输入像素；页码 2、尺寸和金额校验通过，耗时约 4.38 秒。
- 35 项隔离测试通过：认证、请求参数、MIME/文件/像素/响应限制、合并文字计入换行的字符上限、模型文件篡改拒绝、非法坐标和非有限数值、繁忙直接拒绝、取消请求期间原生推理锁保留、慢请求体超时和恢复、日志/响应不回显票据。
- Ruff 静态检查和格式检查通过。
- Linux x86_64 / Python 3.11 带哈希依赖锁的解析与安装 dry-run 通过，共 74 个运行依赖。本机没有 Docker；**尚未执行 Linux 镜像构建或云端部署验收**。

这些结果证明固定 SDK、响应协议和本地模型链路可以工作，不代表真实发票准确率、CPU 生产吞吐量或会计正确率。需要用获得授权、会计标注的真实票据另做字段准确率和人工修正耗时评测。
