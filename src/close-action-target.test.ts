import { describe, expect, it } from 'vitest';
import { resolveCloseAction, type CloseActionContext } from './close-action-target';

const context: CloseActionContext = {
  bookId: 'book-a',
  period: '2026-10',
  companyId: 'company-a',
  canReview: true,
};
const target = (overrides: Record<string, string> = {}) => ({
  kind: 'periodic',
  book_id: 'book-a',
  period: '2026-10',
  ...overrides,
});

describe('month-close blocked check destinations', () => {
  it('keeps an unknown destination as a manual explanation instead of guessing a page', () => {
    const destination = resolveCloseAction(target({ kind: 'sql_admin' }), context);
    expect(destination.mode).toBe('manual');
    expect(destination.mode === 'manual' && destination.reason).toContain('sql_admin');
    expect(JSON.stringify(destination)).not.toContain('href');
  });

  it('explains a missing destination on historical preflights', () => {
    const destination = resolveCloseAction(undefined, context);
    expect(destination.mode).toBe('manual');
    expect(destination.mode === 'manual' && destination.reason).toContain('历史预检');
  });

  it('refuses to follow a link that belongs to another book or month', () => {
    for (const stale of [target({ book_id: 'book-b' }), target({ period: '2026-09' })]) {
      const destination = resolveCloseAction(stale, context);
      expect(destination.mode).toBe('manual');
      expect(destination.mode === 'manual' && destination.reason).toContain('其他账套或月份');
    }
  });

  it('binds the review queue link to the exact book, company and month', () => {
    const destination = resolveCloseAction(target(), context);
    expect(destination.mode).toBe('link');
    if (destination.mode !== 'link') return;
    const url = new URL(destination.href, 'http://localhost');
    expect(url.pathname).toBe('/workspace/review');
    expect(url.searchParams.get('review_book')).toBe('book-a');
    expect(url.searchParams.get('company')).toBe('company-a');
    expect(url.searchParams.get('period')).toBe('2026-10');
    expect(url.searchParams.get('book')).toBeNull();
  });

  it('marks every one of the seven declared kinds as actionable', () => {
    const kinds = [
      'periodic',
      'review',
      'manual_vouchers',
      'subledger',
      'bank_reconciliation',
      'formal_books',
      'reports',
    ];
    for (const kind of kinds) {
      expect(resolveCloseAction(target({ kind }), context).mode).not.toBe('manual');
    }
  });

  it('offers configuration work only to a reviewer and otherwise names the missing permission', () => {
    const subledger = resolveCloseAction(target({ kind: 'subledger' }), context);
    expect(subledger).toEqual({ mode: 'inline', panel: 'subledger', label: '去配置控制科目' });
    const bank = resolveCloseAction(target({ kind: 'bank_reconciliation' }), context);
    expect(bank).toEqual({ mode: 'inline', panel: 'bank', label: '去补银行账户与覆盖' });

    const reader = resolveCloseAction(target({ kind: 'bank_reconciliation' }), {
      ...context,
      canReview: false,
    });
    expect(reader.mode).toBe('manual');
    expect(reader.mode === 'manual' && reader.reason).toContain('复核权限');
  });

  it('keeps a read-only entry for report review but no cross-book operation', () => {
    const destination = resolveCloseAction(target({ kind: 'reports' }), context);
    expect(destination).toEqual({
      mode: 'link',
      href: '#formal-close-report',
      label: '查看报表复核',
    });
  });

  it('links the books evidence back to the same workbench with book and month preserved', () => {
    const destination = resolveCloseAction(target({ kind: 'formal_books' }), context);
    expect(destination.mode).toBe('link');
    if (destination.mode !== 'link') return;
    const url = new URL(destination.href, 'http://localhost');
    expect(url.pathname).toBe('/workspace/formal-books');
    expect(url.searchParams.get('book')).toBe('book-a');
    expect(url.searchParams.get('period')).toBe('2026-10');
    expect(destination.href.endsWith('#formal-close')).toBe(true);
  });
});
