import { describe, expect, it } from 'vitest';
import { emptyWorkspaceRoute, readWorkspaceRoute, workspaceRouteUrl } from './workspace-route';
import { readReviewFilters } from './useReviewFilters';

describe('URL 工作位置只保存标识和筛选，不承担授权', () => {
  it('保留企业账套、凭证与授权读取后的期间提示', () => {
    const route = {
      ...emptyWorkspaceRoute,
      view: 'accounting' as const,
      bookId: 'book-一',
      draftId: 'd1',
      companyId: 'company-a',
      period: '2026-10',
      filter: 'pending' as const,
    };
    const url = workspaceRouteUrl(route, { companyId: 'company-a', period: '2026-10' });
    const parsed = new URL(url, 'https://example.test');
    expect(parsed.searchParams.get('company')).toBe('company-a');
    expect(parsed.searchParams.get('period')).toBe('2026-10');
    expect(readWorkspaceRoute(parsed.pathname, parsed.search)).toEqual(route);
  });
  it('未知工作区与错误筛选回到有效默认值，不传递令牌或业务事实', () => {
    const route = readWorkspaceRoute(
      '/workspace/not-a-page',
      '?filter=approved-all&tab=html&book=%00&draft=d1&token=secret&amount=100',
    );
    expect(route.view).toBe('home');
    expect(route.filter).toBe('all');
    expect(route.listTab).toBe('drafts');
    expect(route.bookId).toBe('');
    expect(route.draftId).toBe('');
    expect(workspaceRouteUrl(route, undefined, '?token=secret&amount=100')).toBe('/workspace/home');
  });
  it('同屏原件需要完整的账套与凭证标识', () => {
    expect(readWorkspaceRoute('/workspace/review', '?review_draft=d1').reviewDraftId).toBe('');
    expect(
      readWorkspaceRoute('/workspace/review', '?review_book=b2&review_draft=d1').reviewDraftId,
    ).toBe('d1');
  });
  it('月度服务与本人资料请求保留准确标识，刷新不借用上一账套', () => {
    const service = readWorkspaceRoute('/workspace/service', '?company=c2&book=b2&period=2026-10');
    expect(workspaceRouteUrl(service)).toContain('/workspace/service?book=b2');
    expect(service.companyId).toBe('c2');
    const material = readWorkspaceRoute('/workspace/materials', '?material=request-2&company=c2');
    expect(material.materialId).toBe('request-2');
    expect(material.bookId).toBe('');
    const url = new URL(workspaceRouteUrl(material), 'https://example.test');
    expect(readWorkspaceRoute(url.pathname, url.search)).toEqual(material);
  });
  it('跨企业月度下钻保留精确企业、账套与期间，非法月份不会进入查询', () => {
    const route = readWorkspaceRoute('/workspace/review', '?company=c2&book=b8&period=2026-11');
    expect(route.companyId).toBe('c2');
    expect(route.bookId).toBe('b8');
    expect(route.period).toBe('2026-11');
    expect(readReviewFilters('?company=c2&period=2026-11')).toMatchObject({
      company: 'c2',
      period: '2026-11',
    });
    expect(readWorkspaceRoute('/workspace/monthly', '?period=2026-19').period).toBe('');
    expect(workspaceRouteUrl({ ...route, view: 'balances' })).toContain('/workspace/balances?');
  });
  it('复核筛选在刷新后恢复，并拒绝非法页码及期间', () => {
    expect(
      readReviewFilters(
        '?review_company=c1&review_period=2026-10&review_template=bank&review_risk=basic_clear&review_offset=40',
      ),
    ).toEqual({
      company: 'c1',
      period: '2026-10',
      template: 'bank',
      risk: 'basic_clear',
      offset: 40,
    });
    expect(
      readReviewFilters(
        '?review_period=2026-99&review_offset=-20&review_template=script&review_risk=unknown',
      ),
    ).toEqual({ company: '', period: '', template: 'all', risk: 'all', offset: 0 });
  });
});
