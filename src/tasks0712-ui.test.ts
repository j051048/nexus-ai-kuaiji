import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { expect, it } from 'vitest';
import { TaxWorkpaperPanel } from './TaxWorkpaperPanel';
import { FormalBooksWorkbench } from './FormalBooksWorkbench';
import { IntakeWizard } from './IntakeWizard';
import type { Session } from './types';
import type { BusinessContext } from './business-types';

const session: Session = {
  token: 'offline',
  mode: 'local',
  firm_id: 'f',
  user_id: 'u',
  firm_name: '虚构事务所',
  user_name: '测试',
  role: 'owner',
  scope_key: 'f:u',
};
it('底稿页面不能在已经人工申报后仍一概宣称尚未申报', () => {
  const html = renderToStaticMarkup(
    createElement(TaxWorkpaperPanel, { session, bookId: 'b', companyId: 'c', period: '2026-10' }),
  );
  expect(html).toContain('生成底稿不代表已申报');
  expect(html).toContain('以人工登记及回执为准');
});
it('内部正式账簿不代表外部软件已过账', () => {
  const html = renderToStaticMarkup(
    createElement(FormalBooksWorkbench, { session, bookId: 'b', onBack: () => {} }),
  );
  expect(html).toContain('不代表外部财务软件已过账');
});
it('员工未上传时有就地下一步，不会自动识别或确认', () => {
  const context: BusinessContext = {
    companies: [],
    templates: [],
    scopes: { own: true, department: false, company: false, accounting: false },
  };
  const html = renderToStaticMarkup(
    createElement(IntakeWizard, {
      session,
      context,
      onClose: () => {},
      onCompleted: () => {},
      onBusyChange: () => {},
    }),
  );
  expect(html).toContain('票据办理下一步');
  expect(html).toContain('先选择企业，再拍照或上传原件');
  expect(html).not.toContain('checked=""');
});
