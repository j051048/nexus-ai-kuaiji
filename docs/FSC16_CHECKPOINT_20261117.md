# FSC16 检查点（2026-11-17）

- 包干维修合同版本/独立确认/生效、额度占用/释放、历史保留完成。
- 迁移 sqlite 198 / postgres 283；服务 `RepairPackageService`；UI 已接创建、确认、生效、占用、释放、额度 summary。
- PG 真缺陷：JSONB `vehicle_external_ids_json` 返回 list，`_contract_view`/`consume` 统一 `_json_list` 兼容 dict/list；此前 PG 报 `TypeError: the JSON object must be str... not list`。
- 并发：两线程同时占用 6000/10000，恰一成功一 409，最终只一条 consume。
- 验收：SQLite 15 passed/24.35s；PG17 4 passed/2.09s；PG16 4 passed/2.10s；浏览器 2 passed/38.0s；恢复 1 passed/7.44s；typecheck 0。
- 候选：source 2352/`7817238a…`；migration 467/`50fb3cc3…`。
- 未完成：真实客户合同/工单/T+ 归档、两企业浏览器/跨年全链、FSC16 与月度税费/凭证联动、FSC17/FSC12。
- 未提交/推送/部署。
