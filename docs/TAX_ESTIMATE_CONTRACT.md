# 税务估算契约（小规模/一般纳税人）

## 范围

纯离线计算模块：

- `nexus_backend/app/tax/estimate.py`
- `test_tax_estimate.py` 9 passed

只做估算，不做申报、不做缴税、不生成正式申报表、不写数据库。

## 规则

小规模：

```text
vat = sales × vat_rate
surtax = max(vat, 0) × surtax_rate
```

一般纳税人：

```text
vat = output_vat - input_vat
surtax = max(vat, 0) × surtax_rate
```

- 金额必须是非负、有限、最多两位小数的 Decimal；
- 负 VAT（留抵）保留为负数，附加税按 0；
- 输出明确 `filing: "not_filed"`；
- 不执行模型表达式，不使用 float。

## 测试覆盖

```text
test_tax_estimate.py: 9 passed
```

覆盖：

- 小规模正常估算；
- 一般纳税人销项-进项；
- 留抵负数；
- 一般纳税人缺进/销项拒绝；
- 非法金额/精度/NaN；
- 非法税率。

## 未完成

- 未接账套税务配置和发票数据；
- 未做 UI；
- 未做申报表；
- 未验证真实税率、税收优惠和地区差异；
- 未接真实客户/真实税期。

该模块不能作为报税依据。
