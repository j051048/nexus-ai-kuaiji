import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it, vi } from 'vitest';
import { MonthlyBriefing } from './MonthlyBriefing';
import type { Session } from './types';

vi.mock('./useScopedQuery', () => ({
  useScopedQuery: () => ({
    data: null,
    loading: false,
    error: '月结锁期后才能查看月度财税简报',
  }),
}));
const session: Session = {
  token: 'secret-never-render',
  mode: 'local',
  firm_id: 'f1',
  user_id: 'client',
  role: 'client_member',
  firm_name: '虚构事务所',
  user_name: '客户',
  scope_key: 'private',
};
describe('正式交付包独立于旧参考简报', () => {
  it('参考简报不可用时仍读取正式冻结包，交付包自行核验企业权限', () => {
    const html = renderToStaticMarkup(
      createElement(MonthlyBriefing, { session, bookId: 'b1', onBack() {} }),
    );
    expect(html).toContain('aria-label="月度交付包"');
    expect(html).toContain('正在检查本月交付条件');
    expect(html.indexOf('aria-label="月度交付包"')).toBeLessThan(
      html.indexOf('暂不能查看月度财税简报'),
    );
    expect(html).not.toContain(session.token);
    expect(html).not.toContain('生成本月交付包');
  });
});
