# 批次 G 检查点

- 候选：2143/`882bd4...`；migration 375/`882f13...`；HEAD `781fd934...`。
- G1：外部证据 152/235、API/UI/门禁；SQLite 23/74.41s、PG16/17 各 5、浏览器 2/25.5s。
- G2：六阶段投影；单测 3、stage17 1/6.8s、workbench 5/22.1s；截图 artifacts/stage17-monthly-six-stages-*。
- G3：仅两期账簿 2/19.5s；完整联合链未做。
- P01：`tmp_test/tax-g/p01-g` passed；188/165.914s、2/94.524s、4/59.519s；migrations 223、max 235、九项 true。
- 环境：PG 25496–25499、bridge 25510 仍在运行；下一步收尾并进入 H。
- 未提交/推送/部署。
