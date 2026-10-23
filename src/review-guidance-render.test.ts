import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { Reviewer } from './VoucherReviewer';
import type { BookDetail, Draft, Session } from './types';

const noop = () => {};
const asyncNoop = async () => {};
const session: Session = {
  token: 'offline',
  mode: 'local',
  firm_name: '虚构所',
  user_name: '会计',
  firm_id: 'firm-a',
  user_id: 'user-a',
  role: 'reviewer',
  scope_key: 'a',
};
const draft: Draft = {
  id: 'draft-a',
  source_id: 'source-a',
  description: '虚构办公费',
  status: 'draft',
  version: 1,
  explanation: '模板建议',
  issues: [],
  lines: [
    { account_code: '6602', debit: '100.00', credit: '0.00' },
    { account_code: '1002', debit: '0.00', credit: '100.00' },
  ],
};
const detail: BookDetail = {
  book: {
    id: 'book-a',
    name: '虚构账套',
    company_id: 'company-a',
    company_name: '虚构企业 A',
    period: '2026-10',
    period_status: 'open',
    pending_count: 1,
    exception_count: 0,
    approved_count: 0,
    permissions: { edit: true, review: true, export: true, manage: false },
  },
  accounts: [
    { code: '6602', name: '管理费用' },
    { code: '1002', name: '银行存款' },
  ],
  sources: [
    {
      id: 'source-a',
      external_id: 's-a',
      date: '2026-10-12',
      description: '虚构办公费',
      counterparty: '虚构商户',
      amount: '100.00',
      direction: 'expense',
      evidence_ref: 'ref-a',
      status: 'active',
    },
  ],
  drafts: [draft],
};
const render = (
  draftChanges: Partial<Draft> = {},
  bookChanges: Partial<BookDetail['book']> = {},
  operationError = '',
) =>
  renderToStaticMarkup(
    createElement(Reviewer, {
      session,
      draft: { ...draft, ...draftChanges },
      detail: { ...detail, book: { ...detail.book, ...bookChanges } },
      busy: '',
      externalBusy: false,
      onIntelligenceBusyChange: noop,
      onIntelligenceChanged: asyncNoop,
      onDirtyChange: noop,
      onOpenCase: noop,
      onSave: asyncNoop,
      onApprove: asyncNoop,
      onEvidence: asyncNoop,
      onBusinessEvidence: noop,
      onOpenBatch: noop,
      onAssistant: noop,
      operationError,
    }),
  );
describe('常规会计复核接入引导而不新增业务动作', () => {
  it('原有原件、分录、保存和审核入口保留，前面提供四步引导', () => {
    const html = render();
    expect(html).toContain('aria-label="会计复核引导"');
    expect(html).toContain('四步完成会计复核');
    expect(html.indexOf('四步完成会计复核')).toBeLessThan(html.indexOf('原始流水'));
    expect(html).toContain('同屏查看原件与凭证');
    expect(html).toContain('保存草稿');
    expect(html).toContain('确认审核');
    expect(html).not.toContain('type="checkbox"');
  });
  it('审核完成时明确还需返回列表导出', () => {
    expect(render({ status: 'approved' })).toContain('已审核，尚未导出');
    expect(render({ status: 'approved' })).toContain('返回列表');
  });
  it('关期明确解释限制而不新增解锁按钮', () => {
    const html = render({}, { period_status: 'closed' });
    expect(html).toContain('期间已关闭');
    expect(html).not.toContain('解锁期间');
  });
  it('关期草稿的现有摘要、金额、审核操作同时只读，与服务端期间约束一致', () => {
    const html = render({}, { period_status: 'closed' });
    expect(html).toMatch(/<textarea[^>]*id="journal-description"[^>]*disabled=""/);
    expect(html).toMatch(/<input[^>]*aria-label="第 1 行借方金额"[^>]*disabled=""/);
    expect(html).toMatch(/<button class="button primary" disabled=""[^>]*>[\s\S]*?确认审核/);
  });
  it('只读读者得到交由授权会计处理的提示', () => {
    const html = render(
      {},
      { permissions: { edit: false, review: false, export: false, manage: false } },
    );
    expect(html).toContain('只读');
    expect(html).toContain('授权会计');
  });
  it('常规入口仅复核权限仍可核对审核，不要求新增编辑权限', () => {
    const html = render(
      {},
      { permissions: { edit: false, review: true, export: false, manage: false } },
    );
    expect(html).not.toContain('当前凭证为只读权限');
    expect(html).toMatch(/<button class="button primary">[\s\S]*?确认审核/);
    expect(html).toMatch(/<textarea[^>]*id="journal-description"[^>]*disabled=""/);
  });
  it('外层精确绑定的保存或审核失败进入引导，要求重新读取', () => {
    const html = render({}, {}, '版本已变化');
    expect(html).toContain('本次操作未确认完成');
    expect(html).toContain('重新读取当前版本与授权');
    expect(render({ status: 'approved' }, {}, '版本已变化')).toContain(
      '已审核，尚未导出；本次操作未确认完成',
    );
  });
});
