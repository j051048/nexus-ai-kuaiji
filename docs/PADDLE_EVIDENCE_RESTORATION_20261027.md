# 独立 OCR、金额标签及员工提示本地验收

## 候选范围

基线 `664ca877c0c0cc3ce36f2d5ffada354e1fc0dbb3`，开始时主工作区干净。本轮只操作本地虚构数据和模拟传输，未提交、推送、部署或访问生产数据库/真实模型。没有修改 C01、数据库迁移、RLS、会计复核政策、费用公式或 Worker 调度。

恢复的独立服务及 OCR 适配基础来自本仓库 `2b9170fb^`，保留 SDK/模型文件固定版本、许可证、NOTICE 和来源说明。历史实测结果不能作为本候选真实引擎验收。

## 已修复

1. `ACCOUNTING_OCR_ENGINE=paddleocr` 不再被忽略。独立服务读取经过大小限制和重编码的图像；兼容模型只读取 OCR/原生 PDF 文字提取字段，不获得写账工具。
2. 服务端保存企业、事务所、原件 SHA256、渲染页 SHA256、文字框、分数和逐字段引用绑定。模型 schema 不接受 `ocr_evidence`；缓存再次计算引用匹配，不只相信缓存中的 matched 字样。旧版无绑定缓存按 miss 处理；旧录入证据不可信时保留旧记录及原件、审计跳过转交，仍允许用户确认手工事实并正常提交审批。
3. 外发政策分别检查私有 OCR 和字段模型两个目标。OCR 后再次检查授权、事项/录入版本、原件、政策和队列租约；处理共享既有 deadline，不新增重发或回退路径。
4. 低分、金额/字段冲突、PDF 原生文字冲突仍进入人工处理。OCR 故障不回退视觉模型。OCR 自托管耗时/页数标为 unpriced，模型实际 usage、未知费用及 attempt 保护保留。固定 OCR 失败码不再误显示为 Worker 内部故障。
5. 精确支持 `Gross Total`，保留裸 Total、错金额、多金额、跨字段、英文标签拼接/截取和无独立原文等拒绝路径。
6. 业务响应增加 `mapping_visibility`：visible/restricted/missing/not_applicable。保持原权限过滤；员工看不到关联时不返回账套、来源或草稿 ID。员工权限隐藏显示正常等待；旧 API 未声明可见性时中性处理；明确有权但关联缺失才提示会计核查。没有把“交接”显示成审核、记账或导出完成。

## 先红后绿

- 新独立接入测试首次：12 failed / 1 passed，包含配置被忽略、证据未保存、无私有 OCR 调用。
  - `tmp_test/offline-pytest/eeaf337c06204ec1be134c37abf11e3b/results.xml`
- Gross Total：依次复现别名缺失 4 项、标签拼接 2 项、原文前缀截取 8 项失败，随后相关 130 项通过。
  - `tmp_test/gross-total-final-regression-green-20261027.xml`
- 员工提示：前端新增 3 项、后端新增 3 项先失败，再分别通过；业务服务完整组合 36 项通过。
- 私有服务严格数值：布尔值/字符串置信度及坐标 4 项先失败，修复后服务 39 项通过。
- 固定 OCR 错误分类：5 failed / 1 passed，修复后新增边界及原 AK04 32 项通过。
  - `tmp_test/paddle-execution-categories-red-20261027.xml`
- 旧版录入证据兼容：在合法初次写入边界模拟旧终态数据，真实复现完整性校验导致手工提交失败，再修复；未禁用触发器或修改终态证据。
  - `tmp_test/offline-pytest/546767c851f44148baa68fb3095e5efe/results.xml`

## 回归结果

| 验证 | 结果 |
| --- | --- |
| 独立接入、供应商、AG01 限额、费用用量、业务可见性、私有服务组合 | 168 passed |
| 独立接入、金额证据、AK02、AK03、业务可见性组合 | 170 passed |
| 最终独立接入 24 项 + 执行边界 13 项 + AK04 19 项 | 56 passed |
| 另两条企业隔离/金额冲突及未复核禁止导出 | 2 passed |
| 前端全量单测 | 95 files / 964 passed |
| TypeScript、全 src ESLint、相关 Prettier、相关 Ruff、git diff --check | 通过 |
| 同候选预览构建 | 通过 |

以上组合有重叠，不能相加为独立用例总数。最终 56 项包含完整原生 PDF 无需 OCR、两个已批准目标正常发送、跨企业同原件不共享识别、金额冲突仍需人工、未复核拒绝导出。

最终边界 XML：`tmp_test/offline-pytest/b7bd331305494fd79cd78355689c46a3/results.xml`。

## 桌面与手机

使用项目原有隔离配置、新 SQLite 和私有附件目录，仅改本轮临时配置的本地端口至 18190/18191，未占用或终止其他工作区服务。

- 新增桌面、375×812 手机用例均通过：真实员工提交、部门/财务审批、交接后检查员工隐藏与财务可见；额外模拟旧响应/有权却缺失，验证提示。
- 截图实际检查过，内容可见、无横向溢出：
  - `tmp_test/mapping-visibility/restricted-desktop.png`
  - `tmp_test/mapping-visibility/restricted-mobile.png`
  - `tmp_test/mapping-visibility/missing-desktop.png`
  - `tmp_test/mapping-visibility/missing-mobile.png`
- 附跑既有合成 OCR 字段、未知税额及页码展示用例通过。
- 首次完整浏览器组合 **3 passed / 1 failed**：旧首条用例仍假设企业 AI 默认关闭，实际种子按现有产品默认开启；未改其断言。不得报全套浏览器通过。
- 开发冷预热超时、首轮 Windows 临时目录权限问题及 ENOBUFS 均保留为环境失败记录；没有放宽超时或复用其他数据库。最终使用原 preview 模式与本轮私有 TEMP 目录重验，自建服务已退出。
- XML：`tmp_test/paddle-restoration-e2e/preview-final.xml`。
- 完成全部源码修改后，根代理冻结候选并独立重跑本轮桌面、手机及合成 OCR 展示三项：**3 passed / 0 failed（34.0 秒）**，再次完整预览构建成功。显式选择这三项不等于修复上面旧默认关闭断言。XML：`tmp_test/paddle-restoration-e2e/frozen-final.xml`；源码指纹清单：`tmp_test/paddle-restoration-e2e/candidate-files.json`。

## 未收口的既有回归债

**后续更新：以下为上一轮历史失败记录；本轮已逐项收口，见 [旧回归收口报告](OCR_REGRESSION_CLOSURE_20261027.md)。保留原始失败证据，不将历史结果改写为通过。**

扩展 89 项回归结果为 **83 passed / 6 failed**。已从未修改的 HEAD 单独物化隔离快照，复跑相同六项，仍 **6 failed**，确认不是本轮新增。没有放宽原件保护或覆盖这些旧测试。

- `test_stage7_intelligence.py` 五项旧缓存正向场景假设空白位图的模型输出可以进入已核验缓存，需要补真实独立的合成原文夹具。
- `test_intelligence_freshness.py::test_applied_warning_survives_new_extraction_and_manual_draft_save` 的既有警示展示断言仍失败，需单独核查。
- 当前扩展 XML：`tmp_test/offline-pytest/dc3779e72f6e48d1b8780f794879b9a3/results.xml`。
- 基线复现 XML：`tmp_test/paddle-baseline-664ca877/tmp_test/offline-pytest/c6981f45f8794ac4a2a713a9ab95f83c/results.xml`。

## 发布前界限

本轮三个问题的本地修复与专项验收完成，不代表全仓、真实引擎或发布验收完成。没有下载 Paddle 权重、启动真实推理、构建独立 OCR 镜像、进行 PostgreSQL 本轮动态验收或运行线上模型。需要另行批准并验证实际供应商、OCR 私网/密钥、双目标企业外发政策、模型缓存、真实票据质量和线上完整流程。始终保留企业隔离及会计复核后导出。
