import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { IntakeWizard } from './IntakeWizard';
import { initialBusinessPayload } from './business-forms';
import type { BusinessContext, BusinessEvent } from './business-types';
import type { Session } from './types';

const session: Session = {
  token: 'offline',
  mode: 'local',
  firm_id: 'f',
  user_id: 'u',
  firm_name: '虚构事务所',
  user_name: '虚构员工',
  role: 'client_member',
  scope_key: 'a',
};
const context: BusinessContext = {
  companies: [
    {
      id: 'a',
      name: '虚构企业 A',
      role: 'employee',
      department_id: 'd',
      departments: [{ id: 'd', name: '业务部' }],
      can_submit: true,
      can_finance: false,
      can_manage_approvers: false,
      approver_department_ids: [],
    },
  ],
  templates: [],
  scopes: { own: true, department: false, company: false, accounting: false },
};
const event: BusinessEvent = {
  id: 'original',
  firm_id: 'f',
  company_id: 'a',
  department_id: 'd',
  applicant_id: 'u',
  template_id: 'reimbursement',
  template_version: 1,
  version: 6,
  status: 'returned',
  payload: {
    ...initialBusinessPayload('reimbursement'),
    amount: '128.50',
    description: '客户拜访',
    counterparty: '虚构出租车',
    purpose: '保留原用途',
  },
  attachments: [
    {
      id: 'att',
      filename: '保留原票.pdf',
      sha256: 'a'.repeat(64),
      size_bytes: 128,
      media_type: 'application/pdf',
    },
  ],
  actions: [
    { stage: 'department', decision: 'return', reason: '旧轮次退回', submitted_version: 2 },
    { stage: 'finance', decision: 'return', reason: '请补充本次差旅用途', submitted_version: 5 },
  ],
  versions: [],
  permissions: {
    edit: true,
    submit: true,
    department_approve: false,
    finance_approve: false,
    return: false,
    handoff: false,
  },
};
function render(initialEvent?: BusinessEvent) {
  return renderToStaticMarkup(
    createElement(IntakeWizard, {
      session,
      context,
      initialEvent,
      onClose: () => {},
      onCompleted: () => {},
      onBusyChange: () => {},
    }),
  );
}
describe('员工录入与退回补件呈现', () => {
  it('分别命名办理建议和草稿保存状态，读屏和恢复断言不混用两者', () => {
    const html = render();
    expect(html).toContain('aria-label="票据办理下一步"');
    expect(html).toMatch(/<span[^>]*role="status"[^>]*aria-label="草稿保存状态"/);
  });
  it('退回原因置顶，保留原值和附件，明确重新提交而不是新建', () => {
    const html = render(event);
    expect(html).toContain('请补充本次差旅用途');
    expect(html.indexOf('请补充本次差旅用途')).toBeLessThan(html.indexOf('intake-company-grid'));
    expect(html).not.toContain('旧轮次退回');
    expect(html).toContain('修改后重新提交');
    expect(html).toContain('保留原票.pdf');
    expect(html).toContain('128.50');
    expect(html).toContain('保留原用途');
    expect(html).toContain('补充附件');
    expect(html).toContain('修改信息');
  });
  it('没有退回理由时明确提示，不猜测需要修改的字段', () => {
    const html = render({ ...event, actions: [] });
    expect(html).toContain('尚未取得具体退回原因，请先查看审批记录或联系处理人。');
    expect(html).not.toContain('请补充本次差旅用途');
  });
  it('旧退回记录不冒充当前非退回事项；模板和通用说明默认收折', () => {
    const html = render({ ...event, status: 'submitted' });
    expect(html).not.toContain('请补充本次差旅用途');
    expect(html).not.toContain('修改后重新提交');
    expect(html).toMatch(/<details class="intake-template-picker">/);
    expect(html).toMatch(/<details class="intake-help">/);
  });
});
