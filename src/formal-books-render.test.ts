import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import { FormalBooksWorkbench } from './FormalBooksWorkbench';
import type { Session } from './types';

const session: Session = {
  token: 'never-render-secret',
  mode: 'local',
  firm_name: '虚构事务所',
  user_name: '虚构会计',
  firm_id: 'firm-a',
  user_id: 'user-a',
  role: 'viewer',
  scope_key: 'scope-a',
};
describe('formal book workspace presentation', () => {
  it('uses only existing visual tokens so lazy loading does not lose colors or typography', () => {
    const css = readFileSync(new URL('./formal-books.css', import.meta.url), 'utf8');
    const tokens = readFileSync(new URL('./tokens.css', import.meta.url), 'utf8');
    for (const match of css.matchAll(/var\((--[a-z0-9-]+)\)/g))
      expect(tokens, match[1]).toContain(`${match[1]}:`);
  });
  it('exposes the four read-only books with a clear formal-data boundary', () => {
    const html = renderToStaticMarkup(
      createElement(FormalBooksWorkbench, { session, bookId: 'book-a', onBack() {} }),
    );
    expect(html).toContain('正式账簿');
    expect(html).toContain('科目余额表');
    expect(html).toContain('科目明细账');
    expect(html).toContain('现金与银行日记账');
    expect(html).toContain('辅助账');
    expect(html).toContain('未审核、未记账草稿不计入余额');
    expect(html).toContain('aria-label="账簿类型"');
    expect(html).not.toContain(session.token);
    expect(html).not.toMatch(/>(审核通过|立即记账|自动过账)</);
  });
  it('does not infer download permission from a role name or show a fabricated zero balance', () => {
    const html = renderToStaticMarkup(
      createElement(FormalBooksWorkbench, {
        session: { ...session, role: 'owner' },
        bookId: 'book-a',
        onBack() {},
      }),
    );
    expect(html).toContain('下载权限由账套授权决定');
    expect(html).not.toContain('>0.00<');
    expect(html).toContain('正在读取正式账簿');
    expect(html).toMatch(/disabled=""[^>]*>下载当前账簿 CSV/);
  });
  it('escapes provided names and provides native labelled filters and a return action', () => {
    const html = renderToStaticMarkup(
      createElement(FormalBooksWorkbench, {
        session,
        bookId: 'book-a',
        bookName: '<script>bad()</script>',
        onBack() {},
      }),
    );
    expect(html).toContain('&lt;script&gt;');
    expect(html).not.toContain('<script>');
    expect(html).toContain('返回工作台');
    expect(html).toContain('截止日期');
    expect(html).toContain('role="status"');
  });
});
