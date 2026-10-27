# PaddleOCR 来源与复用范围

当前目录和独立服务从本仓库 `2b9170fb^` 恢复，下面的上游版本、wheel 和模型哈希属于既有固定来源记录。本轮仅复跑假引擎离线契约，不代表重新下载或复验了官方 wheel、权重及真实推理结果；真实验收边界见 `services/paddle_ocr/VALIDATION.md`。

- 官方仓库：<https://github.com/PaddlePaddle/PaddleOCR>
- 发布标签：`v3.7.0`，固定提交 `b03f46425e8ff4442b268ce449e3eef758146cd4`。
- 原始示例：[`docs/version3.x/pipeline_usage/OCR.en.md`](https://github.com/PaddlePaddle/PaddleOCR/blob/b03f46425e8ff4442b268ce449e3eef758146cd4/docs/version3.x/pipeline_usage/OCR.en.md)，Python Integration 中的 `PaddleOCR(...)` / `predict(...)` 初始化及调用模式。
- 适配位置：`services/paddle_ocr/engine.py`；保留来源版权，修改范围见 NOTICE。
- 完整许可证：本目录 `LICENSE`，从同一固定提交原样保留。
- 官方 PyPI wheel：`paddleocr-3.7.0-py3-none-any.whl`，SHA256 `c0f0a81ad4112727f30c6fcf986ac0ef6a120d31ee0991a01fae0357ee32d338`。

复用的是官方 SDK 的文字检测、识别与坐标恢复实现，加上其公开示例的本地适配。SDK 固定为 PaddleOCR 3.7.0、PaddleX 3.7.2、PaddlePaddle 3.3.1；没有复制整个 SDK 目录，也没有把训练代码、PP-ChatOCR 或大型视觉模型打包进会计 API。

模型由官方 PaddleX 下载器首次启动时下载到独立缓存卷，不能由请求参数选择模型或下载 URL。`services/paddle_ocr/model_manifest.json` 已记录实际下载模型的 `inference.json`、`inference.yml` 和 `inference.pdiparams` SHA256，初始化推理器前验证；模型权重不提交 Git。升级任一依赖、模型或预处理参数时，必须同步更新服务 `model` 指纹和调用方配置，并跑票据回归评测。
