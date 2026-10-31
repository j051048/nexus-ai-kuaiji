import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it, vi } from 'vitest';
import { SubledgerSetupPanel } from './SubledgerSetupPanel';
import { api } from './api';
import type { Session } from './types';

vi.mock('./api', () => ({ api: vi.fn(), errorMessage: () => '请求失败' }));
const session: Session = {
  token: 'never-render-subledger-secret',
  mode: 'local',
  firm_id: 'firm-a',
  firm_name: '虚构事务所',
  user_id: 'user-a',
  user_name: '虚构用户',
  role: 'viewer',
  scope_key: 'private-session-scope',
};
const render = (panel: 'subledger' | 'bank' = 'subledger') =>
  renderToStaticMarkup(
    createElement(SubledgerSetupPanel, {
      session,
      bookId: 'book-a',
      period: '2026-10',
      panel,
      accountLabels: { '1122': '1122 应收账款' },
      onClose: () => undefined,
      onChanged: () => undefined,
    }),
  );

describe('month-close configuration panel first render', () => {
  it('states that configuration alone does not pass the check', () => {
    const html = render();
    expect(html).toContain('aria-label="月结阻断办理"');
    expect(html).toContain('配置本身不会通过检查');
    expect(html).toContain('role="status"');
    expect(html).toContain('正在读取当前配置');
  });

  it('does not render a write form before the server answered', () => {
    const html = render('bank');
    expect(html).not.toContain('<form');
    expect(html).not.toContain('登记覆盖');
    expect(api).not.toHaveBeenCalled();
    expect(html).not.toContain(session.token);
    expect(html).not.toContain(session.scope_key);
  });

  it('keeps both panels distinct and readable', () => {
    expect(render('subledger')).toContain('控制科目配置');
    expect(render('bank')).toContain('银行账户与覆盖证据');
  });
});
