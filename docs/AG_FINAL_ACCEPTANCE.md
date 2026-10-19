# AG 最终验收

## 结论

**AG02–AG05 离线纯模块门槛通过；AG01 部分通过。整体不能宣布五包全部完成。**

允许的结论：

> AG02–AG05 本地离线契约通过；AG01 usage 修复通过；真实模型效果、生产 PG 对等验证及上线接入未验证。

## 测试

```text
AG combined（AG02–AG05 + intelligence provider + model cost usage）
124 passed, 1 warning
```

分项：
- AG02：6
- AG03：9
- AG04：9
- AG05：10
- AG01 provider/cost regression：90

## 各包状态

| 包 | 离线工程 | 生产接线 | 真实模型 |
|---|---|---|---|
| AG01 | 部分（usage/上限） | 未完成 | 未验证 |
| AG02 | 通过 | 未完成 | 未验证 |
| AG03 | 通过 | 未完成 | 未验证 |
| AG04 | 通过（单进程） | 未完成 | 未验证 |
| AG05 | 通过 | 未完成 | 未验证 |

## 候选哈希

```text
intelligence/provider.py
F914A8F1BBE1362A25B57F9D9C44586FE1DEE27819C78D51D51C4A0FF0B96964

intelligence/models.py
1795976B2D7FB9DCE7C316004FB512CFD3D90718B3442F020E255CDB092AC53C

context.py
F4DCBB08941A4A2DA91C59BEAA353B3C9A3786BEAC430B60BCF2CA750A5FF471

answers.py
91E6AF30E6D5EE685D6CECA7407C76BE3B419F26518F0DBFF7CE72937140DC73

investigation.py
A1C78A54BD18C779F070CFA7D6D60C233738E48FBC9F4EE750A2235501D45CFE

examples.py
8B1C321B44DBDF2F143E16C9CA3163B0A3B17009439C5AD38D8D80272B3F0C62
```

## 未完成/阻断

- AG01：端点 context/output/timeout 贯通、attempt 全链路、撤权重检、限流/连接/读取分层未全部新增专项；PG 并发未验证。
- AG02：未接生产 assistant service/ContextPack；B04 撤权缓存未接。
- AG03：未接生产 OCR/建议适配。
- AG04：双进程恢复未实现；生产队列/worker 未接。
- AG05：未接生产 learning/样例库/发布系统；未做真实模型评测。

## 边界声明

未联网、未调用真实模型、未启动生产 API/worker、未执行迁移、未修改 C01/UX、未提交/推送/部署。
