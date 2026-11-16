# 批次 J 验收（2026-11-12）

## 结论

- 本地实现：**部分通过**。分页、稳定行定位、宽屏并排、手机堆叠、516 行真实浏览器复核通过。
- PG16/17：**未新增 J 专项证据**；复用既有复核/导出 PG 测试，不等于 J 表格专项通过。
- 桌面/手机浏览器：**通过已选样例**（1280/390），未覆盖全部 J 场景。
- 真机/人工无障碍：**未验证**。
- 真实客户/T+：**不适用/未授权**。

## 用例结果

| 项 | 证据 | 结果 |
|---|---|---|
| J01 宽屏原件+分录 | `e2e/tax05-large-voucher.spec.ts` 几何断言 | passed（1280 并排） |
| J01 手机完整办理 | 同上，390 | passed |
| J02 范围/合计 | 行范围 + 本页/全量借贷合计标签 | passed（本页 2,500.00、全量 25,800.00；末页 800.00） |
| J03 稳定行身份 | draft id 打开；绝对行号定位 | passed（单项） |
| J04 跨企业/撤权 | 既有 `useScopedQuery`/API；本批未新增断言 | 未单独验证 |
| J11 跨页待办选择 | `updateFirmSelection` 保留 kind/ref；翻页不清空；提交发送完整集合 | 单测 11 passed；`tax-j-selection` 1280/390 2 passed/11.8s |
| J12 行距偏好 | 舒适/紧凑切换，localStorage 持久化 | `tax05-large-voucher` 1280/390 2 passed/19.3s；断言 class 与存储值 |
| J13 列宽偏好/拖拽 | 标准/宽科目/宽金额 + 表头拖拽、键盘 10px 微调、工具栏 20px 微调、重置；localStorage 持久化 | `tax05-large-voucher` 1280/390/390×500 3 passed；拖拽后 340、调窄 320、重置；全套 6 passed/32.3s |
| J05 版本/资格 | 既有服务端拒绝；未重跑 | 未单独验证 |
| J06 自审/未复核导出 | 既有服务端测试 | 通过（既有） |
| J07 待办→来源→返回 | 既有复核/月度返回链 | 通过（既有） |
| J08 516 行分页定位与合计 | 2 passed/18.9s | passed |
| J09 批量部分成功 | 既有 batch operation 测试 | 通过（既有） |
| J10 表格短视口/定位输入 | 390×500：定位行输入聚焦后滚动，底部不越过复核 footer | `tax05-large-voucher` 390x500 1 passed/3.8s |

## 证据

- `tmp_test/tax05-large-voucher-1280.png`
- `tmp_test/tax05-large-voucher-390.png`
- `tmp_test/tax05-pager-1280.png`
- `tmp_test/tax05-pager-390.png`
- `docs/TAXI_BATCH_J_TABLE_MEASUREMENT_20261110.md`

## 未完成（明确）

- 真实 PDF 内容排版渲染（已有 6MB 合成 PDF 绑定/下载，无头浏览器不绘制 PDF 内容）；J 的 PG16/17 专项双身份复核导出已补，跨页选择尚未接服务端冻结集合（选择在客户端保存）。
- J02 的合计已补，但页面/筛选/全量的口径仅覆盖单张凭证，尚未做跨筛选汇总。
- 因此不得写成“J 全部通过”。

## J14 516 行跨浏览器（2026-11-12）

- Chromium 1280/390: 2 passed/18.9s（既有）。
- Firefox 1280/390: 2 passed/17.6s。
- WebKit 1280/390: 2 passed/19.1s。
- 同一 G 环境、同一 516 行夹具；分页、定位第 516 行、独立复核均通过。

## J15 大 PDF 同屏复核（2026-11-12）

- 6MB 合成 PDF 经真实 API 上传，进入复核抽屉；iframe 绑定 blob URL，下载回读字节 > 6,000,000。
- 2.1s，1 passed；1440 无横向溢出；截图 `tmp_test/tax-j-large-pdf-1440.png`。
- 无头 Chromium 内置 PDF 查看器不绘制页面内容，截图空白；本项只证明附件绑定、预览容器与下载完整，不证明 PDF 排版渲染。

## J16 表格短视口定位输入（2026-11-12）

- 发现：390×500 下定位行输入底部 393.2px，复核 footer 顶部 382.2px，重叠约 11px。
- 修复：`JournalEntryGrid` 定位输入聚焦时 `scrollIntoView({block:'center'})`。
- 复测：390×500 1 passed/3.8s；1280/390×844/390×500 三视口 3 passed/20.1s。

## J05 版本冲突（2026-11-12）

- 抽屉打开后另一会话推进草稿版本，复核确认返回版本错误，不出现“已审核”。
- `e2e/tax05-large-voucher.spec.ts` 1 passed/3.3s。

## 未验证边界

- J04 跨企业/撤权表格专项未建立；撤 book member 不撤销事务所级复核授权，不能作为撤权证据，相关夹具已移除。

## J04 不受信事务所读取/改写（2026-11-12）

- `test_untrusted_firm_actor_cannot_read_or_update_large_draft`：外部事务所 Actor 读取/改写 258 行对草稿均被拒（403/404），原草稿描述未变。
- `test_large_voucher.py` 5 passed/9.74s。
- 浏览器侧 `tax05` access denied：403 时 `.journal-grid` 数据行为 0，不渲染其他范围草稿行；1 passed/3.3s。
- 书级 reviewer 撤权：删除 `book_access` 并撤销 `staff_access_assignments` 后，读取草稿 403/404；`test_large_voucher.py` 6 passed/8.16s。
- 未验证：事务所级 reviewer 撤销后的浏览器全链；book member 单独撤销不等于撤事务所授权。

## J04 撤权复核

- 书级 reviewer：删除 `book_access` 并撤销 `staff_access_assignments` 后，读取大凭证 403/404；原制单人仍可读。`test_large_voucher.py` 6 passed/8.10s。
- 浏览器 403 显示无权且不渲染任何草稿行；1 passed/3.3s。
- 未验证：事务所级 reviewer 撤销后的完整浏览器链；单独撤 book member 不等于撤销事务所授权。
