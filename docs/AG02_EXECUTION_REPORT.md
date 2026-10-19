# AG02 执行报告：ContextPack 与联合版本

状态：**离线纯编译器通过**；生产 assistant service 接线未完成；真实模型未验证。

实现：`tools/ai_inference_lab/context.py`

- 版本 `context-pack-v1`，任务白名单；
- 强制 firm/company/book/period 作用域与输入/输出预算；
- 事实、证据、规则、历史、未决问题分区；
- attachment/extraction/document/page/field 独立证据身份；
- 历史事实版本不一致标记 stale 并清空旧答案；
- 必需事实不足、预算不足 fail-closed；
- Prompt/schema/policy/content 联合 SHA-256 指纹；
- 未知版本拒绝加载。

测试：`nexus_backend/tests/test_ag02_context_pack.py`，**6 passed**。

覆盖 B01/B02/B03/B05/B06/B07/B08。B04 撤权缓存未接生产；生产 SQL 指纹未改。

命令：

```powershell
.\.venv\Scripts\python.exe -m pytest nexus_backend/tests/test_ag02_context_pack.py -q
```
