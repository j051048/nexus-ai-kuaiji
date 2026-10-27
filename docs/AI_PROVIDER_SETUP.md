# 第四阶段：票据视觉识别与记账建议供应商接入

后端提供可配置的 OpenAI 兼容 Chat Completions 接口，视觉模型读取员工附件，记账模型只生成结构化凭证建议。供应商、模型和密钥由部署管理员配置；浏览器不能提交接口地址或模型密钥。尚未配置时页面显示不可用，原有人工核票、审核和导出流程继续使用。

票据识别支持显式选择 `vision` 或 `paddleocr`。`vision` 由兼容接口读取图像；缺少独立文字证据的图片仍进入人工核票。`paddleocr` 先调用独立私有 CPU OCR 服务，再由兼容文本模型提取字段，保存文字框、识别分数、页码、原件摘要及企业范围。不会把模型自报原文充当独立证据，也不会在 OCR 故障时隐式回退视觉模型。两条路径都保留会计复核后导出的要求。

## 可选独立 PaddleOCR

服务构建、固定版本、模型缓存和许可证见 [独立服务说明](../services/paddle_ocr/README.md)。本轮只恢复本地接入并用模拟服务验证；没有部署、下载模型或验证真实识别准确率。启用时在 API 和相关 worker 同时配置：

```dotenv
ACCOUNTING_OCR_ENGINE=paddleocr
ACCOUNTING_PADDLEOCR_URL=https://your-private-ocr.example
ACCOUNTING_PADDLEOCR_TOKEN=
ACCOUNTING_PADDLEOCR_MODEL=PP-OCRv6_small_det+PP-OCRv6_small_rec@px3.7.2-pd3.3.1-det2048-line0-doc0-unwarp0-r1
ACCOUNTING_PADDLEOCR_MIN_SCORE=0.85
ACCOUNTING_PADDLEOCR_ALLOW_HTTP=0
ACCOUNTING_PADDLEOCR_ALLOWED_HOST=
```

`URL` 仅为源站，不带路径、用户名或查询参数；代码追加 `/v1/recognize`。内部 HTTP 必须显式启用并固定允许主机。服务密钥只在后台保存。模型字段提取仍使用 `ACCOUNTING_AI_OCR_*`（或默认兼容模型配置），Paddle 不依赖会计数据库或模型供应商密钥。

企业开启受管外发政策时，必须分别授权私有 OCR 目标和字段提取模型目标：两者均登记为 `ocr` 场景，分别填写固定的 Paddle 模型标识和文本模型标识。`local_only` 同样约束两个目标，不因为名称是“私有 OCR”就绕过外发审批。识别和字段提取共享任务时限，OCR 后再次检查权限、原件版本及政策。

低识别分数、字段/文字冲突、PDF 原生文本冲突和来源绑定错误进入人工处理。缓存按企业、原件摘要及配置隔离，旧版缺少来源绑定的独立 OCR 缓存重新识别；不会静默升级。Paddle 页数与耗时单独记录为 `unpriced`，不能当作免费；模型用量、未知费用与防重发继续沿用现有记录。

## 服务要求与配置

选择支持 `/chat/completions`、内联 `image_url` 图片及结构化 JSON 的接口。视觉模型必须支持图片输入；记账模型可使用同一模型或单独的文本模型。请填写供应商的实际模型 ID，不设置自动替换模型或失败回退。

在独立会计后端环境中配置：

```dotenv
ACCOUNTING_AI_BASE_URL=https://your-provider.example/v1
ACCOUNTING_AI_API_KEY=
ACCOUNTING_AI_VISION_MODEL=
ACCOUNTING_AI_JOURNAL_MODEL=
ACCOUNTING_AI_CHAT_MODEL=
ACCOUNTING_AI_RESPONSE_FORMAT=json_schema
ACCOUNTING_AI_TIMEOUT_SECONDS=45
ACCOUNTING_AI_MAX_CONCURRENCY=2
ACCOUNTING_AI_ALLOW_HTTP=0
```

`BASE_URL` 是基地址，程序在其后追加 `/chat/completions`；复制供应商基地址时不要包含该终端路径。地址必须为 HTTPS，不能携带用户名、密码、查询参数或片段。纯本地测试模型仅允许 `localhost`、`127.0.0.1` 或 `[::1]` 的 HTTP 地址，并须显式设置 `ACCOUNTING_AI_ALLOW_HTTP=1`。不要求密钥的本地兼容服务仍需给 `API_KEY` 填一个非空占位值。超时可设置 1–90 秒，每个后端进程的模型请求并发数为 1 或 2；多进程部署分别计数。

复制根目录 `.env.accounting.example` 为被 Git 忽略的 `.env.accounting` 后显式加载：

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --app-dir nexus_backend --env-file .env.accounting --host 127.0.0.1 --port 8000
```

修改配置后需在获准的发布窗口更新 API 与相关 worker。新企业处理开关默认开启，管理员明确暂停的企业保持暂停；实际发送仍要求供应商、企业外发政策和费用额度全部通过检查。启停操作按企业留存审计。`GET /api/intelligence/config` 只返回可见范围的服务状态和供应商主机名；页面不能修改 URL、模型 ID 或密钥。

默认请求 `json_schema` 且 `strict: true`，对象拒绝额外字段，所有字段必填，未知票据字段明确输出 `null`。若供应商仅支持 `json_object`，由管理员显式改为该值；后端仍用同一数据模型验证响应。服务不自动降级，也不因超时、拒绝或格式错误隐式再次发送附件。修复配置后由用户明确重试。

请求固定 `stream: false`、`store: false`，不携带工具或执行函数。服务使用独立 HTTP 客户端，禁用环境代理和 HTTP 重定向。供应商须接受这些参数及非压缩 JSON 响应；不兼容时会明确失败并交人工处理。供应商错误正文、密钥和附件原文不进入应用错误日志。

## 原件与结构化字段

只读取经过企业授权、存储摘要校验的私有附件字节。JPEG、PNG、WebP 仅支持单帧图片，源图像最多 2000 万像素，重编码为最长边 2000 像素的 JPEG 并移除上传元数据。PDF 通过本地 pypdfium2 渲染全文件，支持 1–6 页，并提取本地原生文字作为交叉核验依据；原生文字不是 OCR。PDF 在独立子进程中处理，30 秒超时；加密、损坏、超页数文件整体拒绝，不能截取部分后当作完整票据。渲染图片不写入公开目录，不提供外链给模型；单次图像总量限制 12 MiB，响应按块读取且限制 1 MiB。

视觉响应分单据包含：单据类型、发票号码、日期、销售方及购买方名称/税号、价税合计、不含税金额、税额、币种；每页转录原文及各字段的页码、原文引用。未知税额是 `null`，不是零。金额从接口到业务校验都使用十进制字符串并转换为整数分；浮点值、指数、负数、无效日期和额外审批/权限字段被拒绝。

模型提示把图片、附件文字、交易对方名称和业务描述视为不可信证据，忽略其中的指令。模型只获得当前授权事项及账套上下文，不读取聊天历史，不使用外企业的资料。记账建议仅使用明确提供的科目，返回摘要、借贷分录、解释、引用和警示；模型没有批准、记账或导出的工具。借贷平衡、金额来源、可用科目、凭证版本、权限与最终会计复核由业务代码完成。

## 本地验证与 2–3 家企业试点

```powershell
cd nexus_backend
python -m pip install -r requirements.txt
python -m pytest tests/test_intelligence_provider.py -q
```

供应商测试使用 `httpx.MockTransport`，图像和 PDF 准备使用真实本地渲染；测试不会向外部模型发送请求，不能以模拟结果宣称真实模型准确率。

真实试点先用少量已知答案的票据验证字段和原文引用，再执行“员工上传 → 视觉识别 → 事项/流水核对 → 人工处理疑点 → 接受草稿 → 会计复核 → 导出”。逐项记录识别失败率、金额/税额/交易对方错误率、异常处理时间、会计修改量和供应商 token 用量。跨企业数据不可见、重复点击不重复执行、错误响应不绕过复核及撤权后不可读是上线前必须完成的实际配置验收。HTTP 路径、幂等键、版本、异常状态及流水关联请求见 [第四阶段接口](STAGE4_API.md)；解除流水关联通过 JSON body 传处理原因，避免将解释写入 URL 访问日志。

OpenAI 协议依据：[图像与视觉](https://developers.openai.com/api/docs/guides/images-vision)、[结构化输出](https://developers.openai.com/api/docs/guides/structured-outputs)。其他兼容供应商仍须按其当前协议和数据处理条款验证。

全局会计助手的对话模型、CNY 单价、企业预留与独立后台部署见[第十五阶段](STAGE15_ACCOUNTING_ASSISTANT.md)。对话费用与现有识别、分录预算共用；未配置对话模型不会复用其他模型。
