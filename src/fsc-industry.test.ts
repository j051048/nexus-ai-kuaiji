import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { resolveCompanyIndustry } from './fsc-industry';
import { IndustryPanel } from './IndustryPanel';

describe('第四期：5 家试点子公司业态动态插件与防串台守卫', () => {
  it('按子公司名称自动识别 5 大试点业态，未匹配归为通用商贸', () => {
    expect(resolveCompanyIndustry('c1', '安达出租车客运有限公司', {}).kind).toBe('taxi');
    expect(resolveCompanyIndustry('c2', '星河文化传媒广告有限公司', {}).kind).toBe('advertising');
    expect(resolveCompanyIndustry('c3', '星海集团内部职工医院', {}).kind).toBe('hospital');
    expect(resolveCompanyIndustry('c4', '星海后勤采购物流有限公司', {}).kind).toBe('logistics');
    expect(resolveCompanyIndustry('c5', '星海城市物业服务有限公司', {}).kind).toBe('property');
    expect(resolveCompanyIndustry('c6', '星海科技咨询有限公司', {}).kind).toBe('general');
  });

  it('显式业态档案优先级高于名称关键词', () => {
    const resolved = resolveCompanyIndustry('c-custom', '星海综合服务有限公司', {
      'c-custom': 'taxi',
    });
    expect(resolved.kind).toBe('taxi');
    expect(resolved.label).toBe('客运出租车');
  });

  it('出租车公司只展示单车承包费与交通规费，物业公司绝不串台出现出租车规费', () => {
    const taxiHtml = renderToStaticMarkup(
      createElement(IndustryPanel, {
        companyId: 'c1',
        companyName: '安达出租车公司',
        period: '2026-11',
      }),
    );
    expect(taxiHtml).toContain('单车承包费');
    expect(taxiHtml).toContain('交通规费');
    expect(taxiHtml).not.toContain('预收物业费');

    const propertyHtml = renderToStaticMarkup(
      createElement(IndustryPanel, {
        companyId: 'c5',
        companyName: '星海物业公司',
        period: '2026-11',
      }),
    );
    expect(propertyHtml).toContain('预收的物业费');
    expect(propertyHtml).toContain('代收代付水电');
    expect(propertyHtml).not.toContain('交通规费');
    expect(propertyHtml).not.toContain('单车承包费');
  });

  it('通用商贸公司不渲染额外特色业态插件卡片，保持通用主干简洁', () => {
    const generalHtml = renderToStaticMarkup(
      createElement(IndustryPanel, {
        companyId: 'c6',
        companyName: '普通测试有限公司',
      }),
    );
    expect(generalHtml).toBe('');
  });
});
