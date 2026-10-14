# 私有 PaddleOCR CPU 服务

本服务提供独立文字证据，供会计后台现有任务 worker 调用。它不持有数据库、Supabase、会计用户或大模型密钥，不生成分录、不代替人工复核，也不接收远程文件 URL。

首版固定 PaddleOCR 3.7.0、PaddleX 3.7.2、PaddlePaddle 3.3.1，使用 `PP-OCRv6_small_det` 和 `PP-OCRv6_small_rec`。生产镜像以带 SHA256 的 Linux Python 3.11 依赖锁安装。两个模型的配置与权重也有独立 `model_manifest.json` 校验；同名模型被上游替换或缓存损坏时启动失败，不悄悄改变识别结果。

实际复用的官方代码是 OCR 示例初始化与预测模式，并通过官方 SDK 运行检测、识别和文字框恢复；来源和许可证在仓库 `third_party/PaddleOCR/`。本目录 `licenses/` 保留相同 LICENSE / NOTICE 副本，随镜像分发。不复制训练、聊天、向量存储或其他无关平台模块。

## 构建和运行

仓库根目录执行：

```sh
docker build -t hengzhang-paddle-ocr:3.7.0 services/paddle_ocr
docker run --rm --env-file /secure/paddle-ocr.env \
  --mount type=volume,source=paddle-models,target=/models \
  --cpus=2 --memory=2g \
  -p 127.0.0.1:8080:8080 hengzhang-paddle-ocr:3.7.0
```

环境文件仅需要 `PADDLEOCR_API_TOKEN=<至少32位随机ASCII密钥>`；密钥在后台 OCR 调用配置中设为相同值，不提交 Git。`PORT` 默认 8080，可由宿主注入。首次启动从官方 BOS 下载两个模型并预热，挂载 `/models` 避免每次重启重复下载。该卷需要 UID 10001 可写。健康检查 `/healthz` 在模型预热完成后返回 `{"status":"ready"}`，不泄露模型参数或密钥。

在 Zeabur 新建独立服务，Root Directory 选 `services/paddle_ocr`，使用本目录 Dockerfile，挂载模型缓存卷；与会计 worker 放在同一私有网络。优先只绑定内部域名和端口。若确实经公网连接，必须使用 HTTPS 和 Bearer 密钥；网络策略应只允许后台 worker 调用。不要给浏览器暴露这个密钥。

2 CPU / 2 GiB 是初始资源配置，不能视为容量承诺；应按真实扫描页尺寸、票据量和宿主测量调整。只运行 **1 个 Uvicorn worker**：它启动 1 个模型实例，每次最多处理 1 个请求，繁忙时返回 503 和 `Retry-After: 2`。持久化重试和企业公平调度由原会计队列负责。本服务没有内部无界排队。HTTP 请求取消后，原生推理仍持有独立锁，直到计算结束。

## 协议

```http
POST /v1/recognize
Authorization: Bearer <PADDLEOCR_API_TOKEN>
Content-Type: application/json

{"pages":[{"page":1,"image_data_url":"data:image/png;base64,..."}]}
```

响应：

```json
{
  "schema_version": "paddle-ocr-v1",
  "engine": "paddleocr",
  "engine_version": "3.7.0",
  "model": "PP-OCRv6_small_det+PP-OCRv6_small_rec@px3.7.2-pd3.3.1-det2048-line0-doc0-unwarp0-r1",
  "pages": [{
    "page": 1, "width": 1400, "height": 700,
    "blocks": [{"id":"p1-b1","text":"税额 69.88","score":0.98,"polygon":[[10,10],[160,10],[160,50],[10,50]]}]
  }],
  "elapsed_ms": 2900
}
```

页面编号必须为 1–6 且不重复。仅接受实际格式匹配的单帧 PNG/JPEG；每图最多 8 MiB、20M 像素，每次最多 6 页、24 MiB 解码图像字节、24M 像素，JSON 请求上限 36 MiB，接收完整请求体的时限 15 秒。每页最多 1,200 个文字框，每行最多 2,000 字符、每页最多 30,000 字符，JSON 响应上限 2 MiB。超限会明确拒绝，绝不静默截断证据。后台调用方按像素总量分批顺序提交，保留完整文档全部页面。

所有文字框坐标属于**输入图像的像素坐标**。首版禁用整页旋转、文本行方向模型和非线性纠偏，避免识别结果与原件错位。调用方先处理 EXIF 和 PDF 渲染；服务不重复做 EXIF 旋转。后续若增加几何变换，必须实现坐标逆映射并更新指纹，不能仅开启开关。识别分数是文字置信度，不代表会计处理正确率。

无票据磁盘落地，无 OCR 原文访问日志，无异常详情或请求内容回显。进程仍需临时内存解码图像；崩溃/OOM由平台重启，原队列重试。升级 SDK、权重、阈值或预处理参数必须同时变更服务指纹、调用方模型配置和依赖锁，再做回归。

## 本地验证

```sh
python -m pip install -r services/paddle_ocr/requirements-dev.txt
python -m pytest services/paddle_ocr/tests -q
python -m services.paddle_ocr.smoke --font /path/to/chinese-font.ttf --output .codex/paddle-smoke
```

单元测试注入假模型，覆盖鉴权、大小限制、几何证据、忙碌拒绝、取消请求后并发保护等。最后一个命令会真实下载/加载两个 CPU 模型，仅生成并识别虚构中文票据，验证金额、税额、日期和交易双方，再输出本地 JSON。它不是实际客户准确率评测。

Windows 开发机须将 `PADDLE_PDX_CACHE_HOME` 设为不含中文的可写目录；Paddle 原生加载器对中文模型路径可能报 JSON 读取错误。生产 Linux 镜像使用 `/models`。模型下载源可使用部署固定的 `PADDLE_PDX_MODEL_SOURCE=BOS`，请求体不能更改下载源。
