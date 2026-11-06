# IMP07–IMP09 整合检查点（imp0709-r2）

更新时间：2026-11-06

## 当前进度

- IMP07-A/B：本地工程与成本/契约测试通过；全量 `assistant_replay` 后台运行中。
- IMP08-A/B：代码与测试完成；08-C 仅 PG smoke + mock UI，完整动态证据待补。
- IMP09-A：既有 unified worker 复验通过（18 passed，含慢后台不阻塞助手）。
- IMP09-B：PG17/PG16 30 分钟 soak passed（1933）；HTTP 50 SQLite 100/100 但 p50 13.07s，延迟不达标。

## 最近命令

- `pytest test_imp07_costing.py test_imp07_contract.py test_imp07_eval.py` → 15 passed
- `pytest test_imp08_analysis.py test_imp08_observation.py test_assistant_task_workflow.py test_assistant_task_baseline.py` → 45 passed
- PG17 smoke → 1 passed；PG16 smoke → 1 passed
- `playwright --config=playwright.imp08.config.ts` → 1 passed
- `pytest test_unified_worker.py test_ak05_reliability.py` → 17 passed；新增慢后台测试后 18 passed

## 下一步

1. assistant replay 已收：353 passed。
2. PG17/PG16 soak 均已通过；HTTP 50 功能通过但 SQLite 延迟不达标。
3. 前端 typecheck/build/budget 已通过。
4. 最终候选 1933/9e87 已冻结；未提交/推送；剩余仅多节点/真实环境。
