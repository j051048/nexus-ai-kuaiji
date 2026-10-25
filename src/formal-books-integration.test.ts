import { describe, expect, it } from 'vitest';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { readWorkspaceRoute, workspaceUsesBook } from './workspace-route';
import { navigationCurrent } from './workspace-navigation';
import { AccountingBookToolbar, bookPipelineStep } from './AccountingBookToolbar';

describe('正式账簿复用当前账套与会计导航', () => {
  it('深链接保留账套范围并归属会计工作台', () => {
    const route = readWorkspaceRoute(
      '/workspace/formal-books',
      '?book=b1&company=c1&period=2026-10',
    );
    expect(route.view).toBe('formal-books');
    expect(route.bookId).toBe('b1');
    expect(workspaceUsesBook(route.view)).toBe(true);
    expect(navigationCurrent('formal-books')).toBe('accounting');
    expect(bookPipelineStep(route.view, 'drafts')).toBe('closing');
  });
  it('获授权账套在更多操作中提供正式账簿入口', () => {
    const html = renderToStaticMarkup(
      createElement(AccountingBookToolbar, {
        book: {
          id: 'b1',
          name: '虚构账套',
          period: '2026-10',
          pending_count: 0,
          exception_count: 0,
          approved_count: 0,
          permissions: { edit: true, review: true, export: true, manage: true },
        },
        busy: false,
        loading: false,
        dirty: false,
        onImport: () => {},
        onWorkspace: () => {},
      }),
    );
    expect(html).toContain('更多账套操作');
    expect(html).toMatch(/<button[^>]*>正式账簿<\/button>/);
    expect(html).toContain('④ 对账与月结');
  });
});
