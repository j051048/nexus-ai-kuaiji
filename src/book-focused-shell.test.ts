import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { BookWorkspaceShell, type BookWorkspaceShellProps } from './BookWorkspaceShell';

function render(view: 'accounting' | 'formal-books' | 'delivery') {
  const book = {
    id: 'b1',
    name: '虚构账套',
    period: '2026-10',
    pending_count: 0,
    exception_count: 0,
    approved_count: 0,
    permissions: { edit: true, review: true, export: true, manage: true },
  };
  const props = {
    session: { mode: 'local', scope_key: 'scope', user_id: 'u1', firm_id: 'f1' },
    me: null,
    routeView: view,
    bookView: createElement('section', { 'aria-label': '当前办理内容' }, '办理正文'),
    filter: 'all',
    navItems: [],
    stats: { book_count: 2, pending_count: 0, exception_count: 0, approved_count: 0 },
    workbench: { books: [book] },
    filteredBooks: [book],
    detail: { book },
    bookId: book.id,
    drafts: [],
    approvedDraftIds: [],
    selectedIds: [],
    search: '',
    listTab: 'drafts',
    busy: '',
    operationBusy: '',
    bootLoading: false,
    bootError: '',
    bookLoading: false,
    bookError: '',
    draftLoading: false,
    draftError: '',
    reviewDirty: false,
    historyLoading: false,
    historyError: '',
    exportHistory: [],
    focusBookRef: { current: book.id },
    pendingReview: { current: null },
  } as unknown as BookWorkspaceShellProps;
  return renderToStaticMarkup(createElement(BookWorkspaceShell, props));
}
describe('账簿与交付办理页面优先显示当前任务', () => {
  it.each(['formal-books', 'delivery'] as const)('%s 折叠选账，不重复铺全所统计', (view) => {
    const html = render(view);
    expect(html).not.toContain('aria-label="事务所账务概览"');
    expect(html).not.toContain('每一笔，都有据可循');
    expect(html).toContain('切换账套');
    expect(html).toContain('aria-label="当前办理内容"');
  });
  it('普通会计工作台继续保留全所统计', () => {
    expect(render('accounting')).toContain('aria-label="事务所账务概览"');
  });
});
