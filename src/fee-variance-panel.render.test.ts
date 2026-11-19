import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it, vi } from 'vitest';
import { FeeVariancePanel } from './FeeVariancePanel';
import { api } from './api';
import type { Session } from './types';

vi.mock('./api', () => ({
  api: vi.fn(),
  errorMessage: () => '请求失败',
  ApiError: class ApiError extends Error {},
}));

const session: Session = {
  token: 'never-render-variance-secret',
  mode: 'local',
  firm_id: 'firm-a',
  firm_name: '虚构事务所',
  user_id: 'user-a',
  user_name: '虚构用户',
  role: 'viewer',
  scope_key: 'private-variance-scope',
};

describe('fee variance panel first render', () => {
  it('states the policy boundary and does not leak the session', () => {
    const html = renderToStaticMarkup(
      createElement(FeeVariancePanel, {
        session,
        bookId: 'book-a',
        period: '2026-10',
      }),
    );
    expect(html).toContain('aria-label="规费差异"');
    expect(html).toContain('未配置时只能记待查');
    expect(html).toContain('正在读取规费差异');
    expect(html).not.toContain(session.token);
    expect(html).not.toContain(session.scope_key);
    expect(api).not.toHaveBeenCalled();
  });
});
