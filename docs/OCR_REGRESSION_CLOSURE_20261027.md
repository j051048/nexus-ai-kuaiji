# 六条后端与一条浏览器既有失败收口

## 范围与结论

基线 HEAD 为 `664ca877`，继承上一轮未提交的独立 OCR 修复。本轮先逐项复现，再修正夹具及过时断言；保留全部原有工作。未提交、推送、部署，未连接生产数据库、真实模型或 OCR 引擎。

六条后端旧失败已关闭。旧浏览器用例中默认开关、岗位入口及对象权限三处过时假设已纠正，原件、人工处理及权限负向断言均保留。核对中另修复了上一轮独立 Paddle 结果被当作旧版本的误报，并补全严格来源/版本负向测试。

未修改 C01、数据库迁移、RLS、企业权限、会计复核政策、金额校验、费用、attempt 或 Worker 调度。

## 原因与修改

### 五条缓存失败

原测试上传空白 PNG，只有模型自行转录的原文，正确行为是保留 `original_unverified`，不能进入已核验缓存。

- 新增 `tests/fixtures/accounting_ocr/cache_invoice_original.txt`：固定虚构票据原文。
- `test_stage7_intelligence.py` 由该文件生成确定性原生文字 PDF，通过实际 `storage.read`、PDFium 子进程和 `prepare_document` 获得独立文字；未 monkeypatch 原文或从模型响应反灌。
- 独立定义模拟模型响应。修改响应金额不会改变原件或原件 SHA。
- 修复缓存复用、同企业同内容复用、无模型密钥时复用、事实修订后旧幂等键拒绝等正向测试。
- 正确的刷新正向为补齐原件已有的票号；原件 0.30、模型改为 0.40 改为明确负向：保持 blocking，不能覆盖有效缓存。
- 强化跨企业不复用、企业暂停不可继续、等长字节篡改导致 SHA 校验拒绝、缺字段证据不入缓存。另保留空白 PNG 连续两次不能入缓存的负向。

五条原始红测：`tmp_test/offline-pytest/a24b5129c609473596b61ffde4c83150/results.xml`，**5 failed**。

### 一条警示保留失败

模型自由 warnings 已被安全规范化，旧断言仍匹配“业务用途”原话。实际警示没有消失。

现在按固定文案和 proposal ID 校验：首次未处理时不能采用；重新 OCR 后不复用旧已解决记录；人工保存后警示仍在；独立审核人因该警示被拒绝，凭证保持 draft。未依赖制单人自审被拒绝来制造假阳性。

红测：`tmp_test/intelligence-freshness-warning-red-20261027.xml`。整个文件 **9 passed**：`tmp_test/intelligence-freshness-warning-final-green-20261027.xml`。

### 一条旧浏览器用例

- 按实际默认开启契约，验证管理员显式暂停后恢复，清理恢复默认状态。
- 员工在事项内查看问题，不要求员工拥有技术导航。
- 使用真实 API 验证事务所会计看不到未移交员工事项，再由本企业财务通过既有受控深链接处理。
- 保留未配置模型不能识别、原件上传、人工依据留痕、员工不能处理异常等断言。
- 未改变产品默认开关、认证、授权或导航代码。

原始红测：`tmp_test/paddle-restoration-e2e/ui-r2-red.xml`；后续岗位/对象范围失败记录也保留，未跳过断言。

根代理冻结候选重跑时另复现浏览器关闭与在途 route.fetch 竞争；仅修测试清理：关闭页面前用 `unrouteAll({behavior: 'wait'})` 等待处理完毕，没有忽略错误、增加重试或放宽超时。修复前 XML：`tmp_test/paddle-restoration-e2e/regression-closure-final.xml`。

## 额外修复：Paddle 当前版本误报旧格式

`_assessment` 原来只承认普通视觉和 XML 版本，导致完整且有来源绑定的当前 Paddle 结果仍产生 `fact_validation_upgrade_*`。

先复现：`tmp_test/offline-pytest/f949b75f3b1744c1a1bab1856eedfb0b/results.xml`，**1 failed**。

新增 `ocr.saved_evidence_current`，仅用于识别保存格式：

- 必须为严格独立证据结构，并匹配当前事务所、企业和原件 SHA。
- 用保存的 OCR model/min_score 精确重算完整版本指纹；不按前缀放行，也不用新全局配置否定可信旧记录。
- 校验页数、页面唯一性、来源计数、坐标、block ID 及逐字段 binding。
- 已有低分、字段/金额冲突等 findings 继续保留；版本匹配不等于事实正确、凭证复核或发票验真。
- 详情读取不增加磁盘/对象存储读取或 PDF 重渲染；原件字节及页面证明仍在提取和缓存读取边界验证，不能把此函数当作新的字节真实性证明。

`test_paddle_saved_evidence_version.py` 包含正常结果/缓存、原生 PDF、阈值 1/1.0、换配置、伪造版本、其他企业/原件、坏页面/binding 和低分仍阻断等 **29 项**。

## 本地回归

后端单次组合 **139 passed，0 failed，0 skipped**（312.05 秒）：

```powershell
.venv/Scripts/python.exe scripts/test_accounting_offline.py `
  nexus_backend/tests/test_stage7_intelligence.py `
  nexus_backend/tests/test_intelligence_freshness.py `
  nexus_backend/tests/test_ak02_ticket_context.py `
  nexus_backend/tests/test_stage12_intake.py `
  nexus_backend/tests/test_paddle_saved_evidence_version.py `
  nexus_backend/tests/test_paddle_evidence_restoration.py `
  nexus_backend/tests/test_paddle_execution_boundaries.py -q
```

XML：`tmp_test/offline-pytest/2decc52dc564481d9dc842bfa84299d2/results.xml`。

前端全量单测 **95 files / 964 passed**；相关 Ruff、Prettier 和 `git diff --check` 通过。浏览器每轮沿用项目隔离环境和原 30 秒用例门槛，使用独立本地端口 18190/18191；未复用或终止其他工作区服务。

根代理对最终候选的完整浏览器复跑：**4 passed，0 failed，0 skipped（57.5 秒）**，包含桌面、手机、实际本地票据/权限流程及合成 OCR 展示；预览构建（含 TypeScript 检查）通过。XML：`tmp_test/paddle-restoration-e2e/regression-closure-drained.xml`。自建 API/前端进程已退出。

候选源码指纹：`tmp_test/paddle-restoration-e2e/regression-closure-candidate.json`。与上一轮源清单相比，原有文件仅变更 `ocr.py`、`intelligence/service.py`（当前版本资格）和 `business-mapping-visibility.spec.ts`（等待清理）；本轮旧失败测试、新夹具及新增测试单独保留，其余既有改动未覆盖。

## 验收界限

本轮为本地专项回归，不宣称全仓、PG16/17、真实 Paddle、真实供应商、线上或 C01 发布验收通过。企业隔离和会计复核后导出保持原约束。
