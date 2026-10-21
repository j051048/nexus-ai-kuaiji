# R5-0 基线与归属

## 实际基线

- HEAD：`baf3f414`
- 工作区：17 个已跟踪文件修改，另有 R4/PG 报告和 `138_projection_identity.sql` 未跟踪。
- 本轮不重置、不清理。

## AG01

- 生产 provider/service/meter/worker 相关修改仍冻结；
- 负责人接收状态：**pending**；
- 不以 Git author 推断归属；
- 不宣布 AG01 或联合生产接入通过。

## 本轮允许范围

- AG04：`tools/ai_inference_lab/investigation.py` 及其测试；
- AG05：`tools/ai_inference_lab/examples.py` 及其测试；
- UX：仅报告入口文件及测试；
- 不修改 C01、迁移、PG 投影及其测试。

## 已有未提交改动

PG/投影、AG04、AG05、UX 报告等属于前序轮次，保持原样，不回退。
