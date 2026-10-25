import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it, vi } from 'vitest';
import { MonthlyPackagePanel } from './MonthlyPackagePanel';
import { api, apiBlob } from './api';
import type { Session } from './types';

vi.mock('./api', () => ({ api: vi.fn(), apiBlob: vi.fn(), errorMessage: () => '请求失败' }));
const session: Session = {
  token: 'never-render-monthly-secret',
  mode: 'local',
  firm_id: 'firm-a',
  firm_name: '虚构事务所',
  user_id: 'user-a',
  user_name: '<script>not-a-name</script>',
  role: 'viewer',
  scope_key: 'private-session-scope',
};
const render = (role: Session['role'] = 'viewer') =>
  renderToStaticMarkup(
    createElement(MonthlyPackagePanel, { session: { ...session, role }, bookId: 'book-a' }),
  );

describe('monthly package first-render safety and accessibility', () => {
  it('announces pending verification instead of claiming that an unseen package is delivered', () => {
    const html = render();
    expect(html).toContain('aria-label="月度交付包"');
    expect(html).toContain('<h2>');
    expect(html).toContain('role="status"');
    expect(html).toContain('正在检查本月交付条件');
    expect(html).not.toContain('客户已签收');
    expect(html).not.toContain('已交付完成');
    expect(html).not.toContain('确认收到 V');
  });

  it('does not derive write permission from an owner label before checking the book response', () => {
    const html = render('owner');
    expect(html).not.toContain('<form');
    expect(html).not.toContain('生成本月交付包');
    expect(html).not.toContain('确认收到');
    expect(api).not.toHaveBeenCalled();
    expect(apiBlob).not.toHaveBeenCalled();
    expect(html).not.toContain(session.token);
    expect(html).not.toContain(session.scope_key);
  });

  it('states the originals-index boundary and makes refresh a disabled native button while loading', () => {
    const html = render();
    expect(html).toContain('原件提供索引');
    expect(html).toContain('客户签收单独记录');
    const refresh = html.match(/<button[^>]*>[\s\S]*?重新检查交付条件<\/button>/)?.[0];
    expect(refresh).toContain('type="button"');
    expect(refresh).toContain('disabled=""');
    expect(html).not.toContain('<script>');
  });
});
