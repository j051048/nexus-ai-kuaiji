import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it, vi } from 'vitest';
import { SubjectAliasPanel } from './SubjectAliasPanel';
import { api } from './api';
import type { Session } from './types';

vi.mock('./api', () => ({ api: vi.fn(), errorMessage: () => '请求失败' }));

const session: Session = {
  token: 'never-render-alias-secret',
  mode: 'local',
  firm_id: 'firm-a',
  firm_name: '虚构事务所',
  user_id: 'user-a',
  user_name: '虚构用户',
  role: 'viewer',
  scope_key: 'private-alias-scope',
};

describe('subject alias panel first render', () => {
  it('states the uniqueness boundary and does not leak the session', () => {
    const html = renderToStaticMarkup(
      createElement(SubjectAliasPanel, {
        session,
        companyId: 'company-a',
        entities: [],
        canManage: true,
      }),
    );
    expect(html).toContain('aria-label="客商通用别名"');
    expect(html).toContain('同一有效期不能指向两个客商');
    expect(html).toContain('正在读取客商别名');
    expect(html).not.toContain(session.token);
    expect(html).not.toContain(session.scope_key);
    expect(api).not.toHaveBeenCalled();
  });
});
