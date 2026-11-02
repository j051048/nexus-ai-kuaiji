# RSI01 候选快照验收

日期：2026-11-02。仅本地；实际组合结果见 RSI_FINAL_ACCEPTANCE.json。

实现位于 `tools/ai_inference_lab/rsi/snapshots.py`。只接受 `prompt.txt` 和 `mock.json`；后者仅有三个预注册策略 ID，不接受正则、Python、schema、预算、权限或工具配置。提示词不会被当作代码执行。目录外链接、额外文件、缺文件、重复 JSON 键、非法类型及超长内容被拒绝。

资源内容摘要与版本谱系 ID 分开；谱系绑定父候选和根候选，加载重算摘要并检查父项/深度。reason 是非执行元数据，改说明不会制造新候选。没有自动搜索、训练或代码改写。

保存采用同目录临时文件、flush/fsync、hard link 的不可覆盖提交，测试实际 Windows 双进程竞争和 junction 越界。实验配置、数据副本、发送前 reservation、结果和最终报告都绑定候选及依赖。子进程在预留后退出时，OS 释放运行锁，缺结果的 attempt 保持 unknown，不重发。

边界：内容摘要只检测漂移，不证明作者身份；白名单不是操作系统沙箱；需信任运行器、配置目录所有者和本地源文件。没有声称断电/硬件损坏下的全文件系统持久性。

测试：`test_rsi_snapshots.py` 和 `test_rsi_experiment.py`。先复现的行为红测包括非法 reason 被加载、取消终态再次执行、耗时 bool 被当数值接纳；红测 JUnit 保存在 `tmp_test/rsi/red-*.xml`。

补充红绿：完成 JSON 后缺 Markdown 可恢复；崩溃导致耗时未知时不重获预算。运行和组合门禁用法见 `RSI_LOCAL_RUNBOOK.md`。
