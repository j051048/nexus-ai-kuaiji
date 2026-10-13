# 结构化电子票据接入

首批支持财政部《电子凭证会计数据标准——全面数字化的电子发票（推广应用版）》指南展示的 **EInvoice、Version=0.3、国内蓝字增值税普通／专用发票 XML**。原件上传后优先本地读取，不调用模型、不预留模型费用；企业处理开关、本人／部门／企业授权、事实版本、原件哈希、后台任务租约、缓存隔离及会计复核继续生效。

本次明确支持范围：

| 内容 | XML 位置 |
| --- | --- |
| 20 位发票号码 | `/EInvoice/Header/EIid` |
| 版本及蓝字、票种 | `/EInvoice/Header/Version`，`InherentLabel/InIssuType`、`EInvoiceType`、`GeneralOrSpecialVAT` |
| 销售方名称／税号 | `/EInvoice/EInvoiceData/SellerInformation/SellerName`、`SellerIdNum` |
| 购买方名称／税号 | `/EInvoice/EInvoiceData/BuyerInformation/BuyerName`、`BuyerIdNum` |
| 未税金额／税额／价税合计 | `/EInvoice/EInvoiceData/BasicInformation/TotalAmWithoutTax`、`TotalTaxAm`、`TotalTax-includedAmount` |
| 开票日期 | `/EInvoice/EInvoiceData/BasicInformation/RequestTime`，保留原文时间并提取日期 |

所有金额经过现有整数分转换；不接受负数、科学计数法、超额、三位小数或特殊浮点文字。未税金额＋税额与价税合计不一致会保存阻断异常，不进入有效复用缓存。红字发票进入人工调整／冲销流程；未知版本、重复必要节点、损坏及不支持格式保留原件并返回明确人工处理状态。

解析输出的证据页 `1` 指 XML 的单份逻辑记录，非 PDF 物理页。字段原文由确定性解析器提供，并与保存的附件 SHA-256 绑定；可从原件下载追查上述路径。币种 `CNY` 是这个国内数电发票适配器的格式限定，显示为推导说明，不声称 XML 原文一定包含币种字段。

**解析不代表验签、验真、抵扣资格确认或已入账。** 输出带 `extraction_method=structured_xml`、`verification_status=not_verified` 和“尚未验签或验真”提示；后续业务金额、购买方归属、日期、发票重复、流水以及凭证复核仍在原有流程处理。模型服务未配置时，该 XML 模板仍可识别及批量排队；图片／普通 PDF 仍使用视觉模型。

安全限制：单份 XML 最多 1 MiB，节点最多 10,000，深度最多 32；拒绝 DTD、实体、外部实体及 XInclude；`schemaLocation`、签名 `Reference` 等 URI 保留原件且不下载。附件存储仍私有。MIME 规范化为 `application/xml`，接受浏览器声明 `text/xml`。员工未提交录入只本人可见。

OFD 内嵌 XBRL、其他 XML 根节点／版本、财政 PDF 内嵌 XML、银行回单以及税务数字账户采集本次尚未宣称支持。接收真实试点样本后须建立分别有版本和安全边界的适配器；不能把任意 XML/XBRL 会计状态当成已验真或本系统审核状态。

## 格式来源与验证

- [财政部电子凭证会计数据标准（推广应用版）公布页](https://kjs.mof.gov.cn/zt/kuaijixinxihuajianshe/dzpzkjsjbzshsd/sjbz/202505/t20250519_3964020.htm)
- [全面数字化电子发票资料包](https://kjs.mof.gov.cn/zt/kuaijixinxihuajianshe/dzpzkjsjbzshsd/sjbz/202505/P020250617561583232009.zip)，指南内含模拟环境 `EInvoice` XML 示例。实现只复用字段形状，仓库测试值全部虚构。
- [财政部全国推广通知](https://www.mof.gov.cn/jrttts/202505/t20250521_3964264.htm)，将验签或验真、解析、报销、入账与归档列为全流程环节。

测试覆盖：无模型无费用识别、严格金额、安全 XML、版本／红字处理、幂等、跨企业缓存及附件隔离、重复票据与金额异常、员工预填证据转移、后台队列处理。PostgreSQL `074`、SQLite `058` 仅扩展录入附件允许的 MIME；保留原件表身份、外键和不可修改约束。
