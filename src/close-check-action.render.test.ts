import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it, vi } from 'vitest';
import { CloseCheckAction } from './CloseCheckAction';
import type { CloseActionContext, CloseActionTarget } from './close-action-target';

vi.mock('./api', () => ({ api: vi.fn(), errorMessage: () => '请求失败' }));

const context: CloseActionContext = {
  bookId: 'book-a',
  period: '2026-10',
  companyId: 'company-a',
  canReview: true,
};
const render = (target?: CloseActionTarget, ctx: CloseActionContext = context, owner?: string) =>
  renderToStaticMarkup(
    createElement(CloseCheckAction, {
      target,
      context: ctx,
      ownerRole: owner,
      onOpenPanel: () => undefined,
    }),
  );

describe('blocked check action row', () => {
  it('names the responsible role and explains a missing destination', () => {
    const html = render(undefined, context, '账套会计');
    expect(html).toContain('负责岗位：账套会计');
    expect(html).toContain('历史预检');
    expect(html).not.toContain('<a ');
    expect(html).not.toContain('<button');
  });

  it('never renders a cross-book link when the page shows another ledger', () => {
    const html = render(
      { kind: 'periodic', book_id: 'book-b', period: '2026-10' },
      context,
      '复核会计',
    );
    expect(html).toContain('其他账套或月份');
    expect(html).not.toContain('book-b');
    expect(html).not.toContain('href=');
  });

  it('renders a scoped review link with no server-provided string injected into the URL', () => {
    const html = render({ kind: 'review', book_id: 'book-a', period: '2026-10' }, context);
    expect(html).toContain('href="/workspace/review?review_book=book-a');
    expect(html).toContain('去复核待审凭证');
  });

  it('hides configuration writes from a reader and keeps role context visible', () => {
    const html = render({ kind: 'subledger', book_id: 'book-a', period: '2026-10' }, {
      ...context,
      canReview: false,
    });
    expect(html).toContain('复核权限');
    expect(html).not.toContain('<button');
    expect(html).not.toContain('去配置控制科目');

    const reviewer = render({ kind: 'subledger', book_id: 'book-a', period: '2026-10' }, context);
    expect(reviewer).toContain('<button type="button"');
    expect(reviewer).toContain('去配置控制科目');
  });

  it('does not offer a guessed destination for an unknown kind', () => {
    const html = render({ kind: 'sql_admin', book_id: 'book-a', period: '2026-10' }, context);
    expect(html).toContain('sql_admin');
    expect(html).toContain('不会猜测页面');
    expect(html).not.toContain('href=');
  });
});
