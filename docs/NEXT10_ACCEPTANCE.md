# NEXT10 验收记录：助手任务效果与可行动证据

- 工程状态：本地离线验收通过（MockTransport + 固定回放，SQLite）。
- 外部验收：未授权／未验证（无真实模型、真实客户数据或线上助手调用）。
- 候选：助手候选 `6ee338137d7f9b874276a6d9b6f57bceb9b17d04ea1c265c0d9a408afd51e91a`；数据集清单 `443d5d78b534e7ec93862badcefa8758665540550731b4e17a7fde680b917a4d`；该指纹覆盖 `nexus_backend/app`、迁移、助手回放/OCR 工具、固定夹具与指定测试文件，不等同于仓库发布候选。
- 运行目录：`tmp_test/next/next10-assistant-57bb/`（57bb 最终候选重跑）；`summary.json` status=`passed`，退出码 0，353 passed，answer 3/3，`real_model_evaluated=false`。
- 命令：`.venv/Scripts/python.exe -m tools.assistant_replay --out tmp_test/next/next10-assistant-57bb --timeout 900`

## 结果

- `353 passed`（14 个测试目标，JUnit `junit.xml`；无 failure/error/skipped，校验器还会拒绝零用例）。
- 安全通过 6/6；缺失 0；unknown 结果 1。
- 可回答样本 3 个，业务回答完整 3/3；撤权、超额、unknown 不计作成功回答。
- 六类固定样本：normal / missing / conflict 成功；quota 失败（0 发送）；revoked stale（0 发送）；unknown interrupted（1 attempt，不自动重发）。
- 三个可回答样本均给出：具体阻断（缺原始单据／金额差 0.10 元／资料冲突）、负责岗位（企业财务）、下一步（上传原件／核对原件与事项），并明确“只读、不代表复核或交付授权”。
- 预算：`cost_cny=null`，费用状态 `estimated`（mock 费用未知，不改为零）；未调用真实计费。
- 请求与证据绑定：每个样本有 `case_sha256`、`request_sha256`、`observation_sha256`；`summary.json` 记录 artifacts 的 SHA-256。
- 人工修改率、真实模型准确率、真实客户效果：**未验证**，本轮不能给出数字。

## 边界与缺口

- `mode=mock`，`real_model_evaluated=false`，`production_ready=false`；FakeProvider 回答质量不代表真实模型。
- 固定六类样本不含单独的“跨企业读取”和“过期证据版本”效果样本；这两类安全性质由既有 `test_ak02/ak03/ak04` 等 353 项中的契约测试覆盖，未作为独立效果样本计分，不能宣称已做独立验收集评测。
- 仅 SQLite；未跑 PG16/17、浏览器或真实业务查询端点。
- 未依据已审核修正自动生成规则候选或发布；本轮只到草稿/离线评测。
- 报告自身声明“安全弃答与准确字段回显不等于回答了阻断原因、负责人及下一步”；本次三个可回答样本已包含这些字段，但该能力未在真实模型上验证。

## 证据文件

- `tmp_test/next/next10-assistant-final3/summary.json`
- `tmp_test/next/next10-assistant-final3/report.md`、`report.json`、`observations.json`
- `tmp_test/next/next10-assistant-final3/junit.xml`、`pytest.log`、`candidate.json`
