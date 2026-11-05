import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it, vi } from 'vitest';
import { MemoryRouter } from 'react-router-dom';
import { MonthlyClientWorkbench } from './MonthlyClientWorkbench';
import { WorkspaceHome } from './WorkspaceHome';
import type { MonthlyClients, MonthlyClientRow } from './monthly-workbench-types';
import type { Session } from './types';
import { WorkspaceProgress } from './workspace-progress';
import { FirmCockpitContent } from './FirmCockpit';
import type { FirmSnapshot } from './firm-cockpit-focus';

const monthly = vi.hoisted(() => ({ data: null as MonthlyClients | null }));
vi.mock('./useMonthlyData', () => ({
  useMonthlyData: () => ({
    data: monthly.data,
    loading: false,
    error: '',
    revoked: false,
    load: () => {},
  }),
}));
const session: Session = {
  mode: 'local',
  token: 'offline',
  user_id: 'u',
  user_name: '虚构财务',
  firm_id: 'f',
  firm_name: '虚构所',
  scope_key: 'offline-scope',
  role: 'owner',
};
const row: MonthlyClientRow = {
  row_key: 'c:b',
  company_id: 'c',
  company_name: '虚构客户',
  book_id: 'b',
  book_name: '虚构账套',
  ledger_id: null,
  period: '2026-10',
  status: 'open',
  warnings: [],
  materials: { sources: 1, business_pending: 0, intake_pending: 0, missing: 0 },
  drafts: { pending: 2, approved: 0, exported: 0 },
  exceptions: { open: 0, awaiting_info: 0, overdue: 0 },
  delivery: { unverified: 0, unknown: 0, differences: 0, verified: 0 },
  closing: { state: 'not_checked', blockers: null, snapshot_id: null },
  efficiency: { reviews: null, manual_actions: null, handling_seconds: null },
  permissions: { edit: true, review: true, export: true, manage: false },
  work: {
    owner: '王复核',
    due_on: '2026-11-08',
    reason: '有待复核凭证；须核对当前版本制单责任',
    target: 'review',
    label: '继续复核',
  },
};
function monthView(changes: Partial<MonthlyClientRow> = {}) {
  monthly.data = {
    rows: [{ ...row, ...changes }],
    period: '2026-10',
    notes: [],
    updated_at: '',
    pagination: { total: 1, offset: 0, limit: 20, has_more: false },
  };
  return renderToStaticMarkup(
    createElement(
      MemoryRouter,
      null,
      createElement(MonthlyClientWorkbench, {
        session,
        period: '2026-10',
        onPeriodChange: () => {},
        onOpen: () => {},
      }),
    ),
  );
}

const snapshot: FirmSnapshot = {
  period: '2026-10',
  summary: {
    clients: 8,
    opened: 8,
    closed: 2,
    overdue: 6,
    exceptions: 0,
    risk_clients: 0,
    projection_stale: 0,
  },
  stages: {},
  workload: [],
  risks: [],
  companies: Array.from({ length: 6 }, (_, n) => ({
    company_id: `c${n}`,
    company_name: `虚构长名称企业-${n}`,
    book_id: `b${n}`,
    period: '2026-10',
    period_status: 'open',
    stage: 'accounting',
    tasks: { intake: 0, review: 0, exception: 0, delivery: 0, overdue: 1 },
    lead_user_id: 'lead',
    lead_name: '林会计',
    reviewer_name: '王复核',
    due_on: '2026-11-08',
    risk: 0,
  })),
};
const html = () =>
  renderToStaticMarkup(
    createElement(FirmCockpitContent, {
      data: snapshot,
      onOpenBook: () => {},
      onOpenInbox: () => {},
      searchControls: createElement('form', { 'aria-label': '客户检索' }),
    }),
  );

describe('工作台 4–8 的信息层级', () => {
  it('全所进度在协调清单前，搜索和客户明细放在同一区域', () => {
    const view = html();
    expect(view.indexOf('全所运营概览')).toBeLessThan(view.indexOf('需要你协调'));
    expect(view).toMatch(
      /aria-label="客户查找与进度"[\s\S]*aria-label="客户检索"[\s\S]*全部客户与阶段明细/,
    );
  });
  it('协调清单默认只展开三家，其余通过明确入口查看，不丢失异常', () => {
    const view = html();
    const coordination = view.slice(
      view.indexOf('cockpit-coordination'),
      view.indexOf('cockpit-team'),
    );
    expect(
      coordination.split('<details')[0].match(/class="cockpit-client needs-coordination"/g),
    ).toHaveLength(3);
    expect(coordination).toContain('其余 3 家需协调客户');
    expect(coordination).not.toMatch(/<details[^>]*\bopen/);
  });
  it('月度清单只露一个客户名、责任人和当前卡点，六组指标折叠', () => {
    const view = monthView();
    expect(view).toContain('class="monthly-row-details"');
    const collapsed = view.slice(view.indexOf('class="monthly-row-details"'));
    expect(collapsed).toContain('2 待审');
    const visible = view.slice(0, view.indexOf('class="monthly-row-details"'));
    expect(visible).toContain('王复核');
    expect(visible).toContain('须核对当前版本制单责任');
    expect(view.match(/虚构客户/g)).toHaveLength(1);
    expect(view).not.toMatch(/<details[^>]*\bopen/);
  });
  it('摘要动作也遵守原有权限，不出现能点击却无响应的复核按钮', () => {
    const view = monthView({
      permissions: { edit: true, review: false, export: false, manage: false },
    });
    expect(view).toMatch(/<button[^>]*disabled=""[^>]*>继续复核/);
  });
  it('未知进度不当作完成，仍提供明细核对', () => {
    const view = monthView({ work: undefined });
    expect(view).toContain('当前办理进度待核实');
    expect(view).toContain('查看各环节明细');
    expect(view).not.toContain('全部完成');
  });
  it('返回月度清单保留展开项，但不同期间和非法值不复用', () => {
    const memory = new WorkspaceProgress('scope');
    memory.saveMonthly({
      period: '2026-10',
      query: '',
      search: '',
      offset: 0,
      expandedRows: ['c:b', 'c:b'],
    });
    expect(memory.getMonthly('2026-10')?.expandedRows).toEqual(['c:b']);
    expect(memory.getMonthly('2026-11')).toBeUndefined();
    memory.saveMonthly({
      period: '2026-10',
      query: '',
      search: '',
      offset: 0,
      expandedRows: [1, null] as never,
    });
    expect(memory.getMonthly('2026-10')?.expandedRows).toEqual([]);
  });
  it('财务首页先给办理清单，岗位说明靠后且不重复下一项内容', () => {
    const view = renderToStaticMarkup(
      createElement(WorkspaceHome, {
        session,
        profile: {
          role: 'finance',
          display_role: 'finance',
          can_submit: false,
          can_approve: false,
          can_finance: true,
          can_review: false,
          can_accounting: false,
          can_manage: false,
          book_count: 1,
          companies: [],
        },
        profileError: '',
        onRetryProfile: () => {},
        onOpen: () => {},
        onNew: () => {},
        onAccounting: () => {},
        onReview: () => {},
        revision: 0,
      }),
    );
    expect(view.indexOf('workspace-inbox')).toBeLessThan(view.indexOf('workspace-role-guide'));
    expect(view).toContain('workspace-professional');
    expect(view).not.toContain('workspace-next-task');
  });
});
