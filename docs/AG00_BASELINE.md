# AG00 基线与隔离

- HEAD：`0fdcef89`；分支 `main`。
- 工作区：仅本轮 AG 修改 + 既有 `ux-audit-venv-hlrk7xep/` 临时目录（未提交、未使用）。
- 起始哈希：
  - intelligence/provider.py `FF7CAFBA...`
  - intelligence/models.py 起始版本
  - assistant/meter.py `FB5C2C06...`
- 测试使用 `.venv/Scripts/python.exe`、MockTransport、临时目录；未读取真实 .env、未联网。
- C01/UX 未修改。
- 起始已存在保护：provider 无工具/无重试、URL 校验、usage 类型校验、成本 workflow 测试。
- 已复现缺陷：choices 结构失败时 usage 丢失；超大 usage 被接受。

命令：

```powershell
.\.venv\Scripts\python.exe -m pytest nexus_backend/tests/test_intelligence_provider.py -q
```
