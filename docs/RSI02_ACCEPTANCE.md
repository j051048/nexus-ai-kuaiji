# RSI02 少量候选比较与独立验收

日期：2026-11-02。实际组合结果见 RSI_FINAL_ACCEPTANCE.json。

实现：`tools/ai_inference_lab/rsi/experiment.py`；执行复用 H01 `extract` 和证据校验，评分完全委托 AG05 `evaluate_samples`。没有另写会计金额或平均分评分器。

固定基线 + 最多三个手工声明候选。先在最多两个开发样本上 screening，再跑完整开发集，冻结 selection 后才打开 validation。小规模离线实验为保留审计证据仍报告所有候选的完整开发成绩；screening 未通过的候选不能被完整开发分数挽救。任何安全/关键字段退步均被 AG05 否决；无明确提升、同等候选并列时保留基线。

来源、OCR、金标、候选与请求身份全绑定；同源/祖先/改 ID 跨集合拒绝。固定分母保留 missing/unknown/failed。已有 H01 的 REVIEW_REQUIRED 是领域待核验，适配层保留在 domain_findings，避免误写成网络失败；字段错误、人工标记与证据校验仍由 AG05 评分，真实失败不改写。

执行器只收到 ExtractionInput，没有 gold。验收集使用前登记一次性暴露凭据；再次选优或并发实验重复使用会被拒绝。候选 hash 不是权限证明；这是可信运行器内的阶段隔离，不是恶意进程不可读取磁盘的沙箱。

支持 mock 和严格绑定的 archived_results，禁止混合模式。归档需匹配原请求、企业、输入、策略、模型标识和 manifest；不重写归档的来源来让它通过。费用 null 与显式零分开，mock 成本始终未知，不宣称免费或真实性能。没有实际供应商调用。

调用数和时间有上限，发送前先持久化 reservation，未知结果不自动新建 attempt；已完成或取消终态不重开。结果、配置、依赖漂移拒绝恢复。JSON 和中文 Markdown 复用同一评分对象；原文受控转义。

演示夹具在 `nexus_backend/tests/fixtures/rsi`，派生自已公开于仓库的 H01 合成数据。因此只证明开发/验收阶段隔离及比较逻辑，不是独立未见真实数据上的模型质量验收。
