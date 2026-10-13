import { describe, expect, it } from 'vitest';
import { chartImportText, parseAccountImport, parseTemplate, templateText } from './catalog-helpers';
import type { CatalogAccount } from './catalog-types';

describe('客户配置的明确输入格式', () => {
  const account: CatalogAccount = { code: '6401.01', name: '客户配置成本科目', category: 'cost', normal_balance: 'debit', active: true, auxiliary_requirements: ['project'], version: 3 };
  it('复制当前科目保留版本用于防覆盖，不把响应字段写回请求', () => {
    expect(parseAccountImport(chartImportText([account]))).toEqual([{ ...account, version: undefined, expected_version: 3 }]);
  });
  it('重复代码无法进入整批预览', () => {
    expect(() => parseAccountImport(JSON.stringify([{ code: '6401', name: '甲' }, { code: '6401', name: '乙' }]))).toThrow('重复');
  });
  it('不接受未知权限或执行字段', () => {
    expect(() => parseAccountImport('[{"code":"6401","name":"成本","firm_id":"other"}]')).toThrow('不支持');
  });
  it('旧版本编号必须是正整数', () => {
    expect(() => parseAccountImport('[{"code":"6401","name":"成本","expected_version":1.5}]')).toThrow('版本');
  });
  it('模板只支持固定金额字段', () => {
    const value = JSON.parse(templateText()); value.lines[0].amount_field = 'amount * 0.13';
    expect(() => parseTemplate(JSON.stringify(value))).toThrow('不接受表达式');
  });
  it('模板修订保持定义，去掉服务端身份字段', () => {
    const definition = parseTemplate(templateText());
    expect(parseTemplate(templateText({ ...definition, id: 'template-a', version: 4 }))).toEqual(definition);
  });
});
