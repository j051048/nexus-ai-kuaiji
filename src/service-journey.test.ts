import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it, vi } from 'vitest';
import { ServicePlanPanel } from './ServicePlanPanel';
import type { ServicePlanOverview } from './service-plan-types';
import type { Session } from './types';
import { serviceJourney } from './service-journey';

const overview: ServicePlanOverview = {
  book: {
    id: 'book-a',
    company_id: 'company-a',
    company_name: '虚构企业A',
    name: '虚构长期账套',
    period: '2026-10',
    ledger_id: 'ledger-a',
    period_status: 'open',
  },
  permissions: { manage: true, review: true },
  plan: null,
  candidates: [],
  requirements: [],
  requirements_pagination: { total: 0, limit: 20, offset: 0, has_more: false },
  templates: [],
};
const session: Session = {
  token: 'offline-only',
  mode: 'local',
  firm_id: 'firm-a',
  firm_name: '虚构事务所',
  user_id: 'accountant-a',
  user_name: '虚构会计',
  scope_key: 'offline-a',
  role: 'owner',
};
const resource = {
  data: overview as ServicePlanOverview | null,
  loading: false,
  busy: false,
  revoked: false,
  error: '',
  notice: '',
  reset: 0,
  load: vi.fn(),
  mutate: vi.fn(),
  invalidate: vi.fn(),
};
vi.mock('./service-plan-data', () => ({ useServicePlanData: () => resource }));
vi.mock('./ServicePlanMaterials', () => ({
  ServicePlanMaterials: () => createElement('p', null, '原有资料清单'),
}));

function render(extra: Partial<typeof resource> = {}) {
  Object.assign(
    resource,
    { data: overview, loading: false, busy: false, revoked: false, error: '' },
    extra,
  );
  const html = renderToStaticMarkup(
    createElement(ServicePlanPanel, {
      session,
      bookId: 'book-a',
      companyId: 'company-a',
      companyName: '虚构企业A',
      period: '2026-10',
      onOpenStage: vi.fn(),
    }),
  );
  return html;
}

describe('V06 服务单串联已有资料、复核与交付页面', () => {
  it('已核验服务单显示三个直接办理入口，不冒充业务进度', () => {
    const html = render();
    expect(html).toContain('aria-label="本月办理路径"');
    expect(html).toContain('打开本期资料');
    expect(html).toContain('打开会计复核');
    expect(html).toContain('核验交付回执');
    expect(html).toContain('审核通过后才能导出');
    expect(html).toContain('导出不代表外部已接收');
    expect(html).toContain('原有资料清单');
    expect(html).not.toContain('已完成 3/3');
  });
  it('无复核资格仍可查资料，不能出现可点击的审核或交付入口', () => {
    const html = render({ data: { ...overview, permissions: { manage: true, review: false } } });
    expect(html).toMatch(/<button[^>]*data-service-stage="materials"(?![^>]*disabled)/);
    expect(html).toMatch(/<button[^>]*data-service-stage="review"[^>]*disabled=""/);
    expect(html).toMatch(/<button[^>]*data-service-stage="delivery"[^>]*disabled=""/);
  });
  it.each([{ loading: true }, { busy: true }, { revoked: true }])(
    '资源不可办理时三个入口均不能跳转 %j',
    (state) => {
      const html = render(state);
      for (const stage of ['materials', 'review', 'delivery']) {
        expect(html).toMatch(
          new RegExp(`<button[^>]*data-service-stage="${stage}"[^>]*disabled=""`),
        );
      }
    },
  );
  it.each([{ id: 'book-b' }, { company_id: 'company-b' }, { period: '2026-11' }])(
    '其他范围响应不暴露办理入口 %j',
    (patch) => {
      const html = render({ data: { ...overview, book: { ...overview.book, ...patch } } });
      expect(html).not.toContain('data-service-stage=');
      expect(html).toContain('不一致');
    },
  );
  it('响应未加载时不把数据缺失当成资料齐全或已交付', () => {
    const html = render({ data: null });
    expect(html).not.toContain('data-service-stage=');
    expect(html).not.toContain('资料齐全');
    expect(html).not.toContain('已交付');
  });
});

describe('V06 下钻目标绑定与未保存保护', () => {
  const scope = { bookId: 'book-a', companyId: 'company-a', period: '2026-10' };
  it('三个目标精确保留当前服务单身份，不带其他企业和草稿的选择', () => {
    const steps = serviceJourney(overview, scope);
    expect(steps.map((step) => step.target)).toEqual([
      { ...scope, view: 'materials' },
      { ...scope, view: 'review' },
      { ...scope, view: 'delivery' },
    ]);
    expect(steps.every((step) => !step.disabled)).toBe(true);
  });
  it.each([
    { dirty: true },
    { busy: true },
    { loading: true },
    { revoked: true },
    { enabled: false },
  ])('不安全的离开状态使全部入口失效 %j', (state) => {
    const steps = serviceJourney(overview, scope, state);
    expect(steps.every((step) => step.disabled && step.reason.length > 0)).toBe(true);
  });
  it('编辑还未保存时明确告诉用户先保存，不默默丢掉分工或补件说明', () => {
    const steps = serviceJourney(overview, scope, { dirty: true });
    expect(steps[0].reason).toContain('先保存或取消编辑');
  });
  it.each([
    { bookId: 'book-b' },
    { companyId: 'company-b' },
    { period: '2026-11' },
    { bookId: '' },
    { companyId: '' },
    { period: '2026-13' },
    { period: '2026-10-01' },
  ])('范围不一致或非法时不构造目标 %j', (patch) => {
    expect(serviceJourney(overview, { ...scope, ...patch })).toEqual([]);
  });
  it('管理能力不能代替独立会计复核能力', () => {
    const steps = serviceJourney(
      { ...overview, permissions: { manage: true, review: false } },
      scope,
    );
    expect(steps[0].disabled).toBe(false);
    expect(steps.slice(1).every((step) => step.disabled)).toBe(true);
  });
});
