import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { WorkspaceContextBar } from './WorkspaceContextBar';
import type { Session } from './types';

const session: Session = {
  token: 'must-not-render-secret',
  mode: 'local',
  firm_id: 'f1',
  firm_name: '虚构示例事务所',
  user_id: 'u1',
  user_name: '虚构员工',
  role: 'reviewer',
  scope_key: 'firm:f1:user:u1',
};

const render = (overrides: Record<string, unknown> = {}) =>
  renderToStaticMarkup(
    createElement(WorkspaceContextBar, {
      session,
      companyName: '虚构示例制造有限公司',
      period: '2026-10',
      bookId: 'book-a',
      onPeriodChange: () => {},
      onOpenBooks: () => {},
      ...overrides,
    }) as never,
  );

describe('workspace context bar', () => {
  it('shows firm, working unit, identity and period', () => {
    const html = render();
    expect(html).toContain('虚构示例事务所');
    expect(html).toContain('虚构示例制造有限公司');
    expect(html).toContain('总账复核主管');
    expect(html).toContain('value="2026-10"');
  });

  it('does not render the bearer token', () => {
    expect(render()).not.toContain('must-not-render-secret');
  });

  it('says the unit is not chosen instead of guessing one', () => {
    expect(render({ companyName: undefined })).toContain('未选择');
  });

  it('reports an unknown lock state without a book', () => {
    expect(render({ bookId: undefined })).toContain('未选择账套');
  });
});
