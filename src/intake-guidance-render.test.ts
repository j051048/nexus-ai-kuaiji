import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { IntakeWizard } from './IntakeWizard';
import type { BusinessContext, BusinessEvent } from './business-types';
import type { Session } from './types';

const session: Session = {
  token: 'offline',
  mode: 'local',
  firm_name: '虚构所',
  user_name: '员工 A',
  firm_id: 'firm-a',
  user_id: 'employee-a',
  role: 'client_member',
  scope_key: 'offline-a',
};
const context: BusinessContext = {
  companies: [
    {
      id: 'company-a',
      name: '虚构企业 A',
      role: 'employee',
      department_id: 'department-a',
      departments: [{ id: 'department-a', name: '行政部' }],
      can_submit: true,
      can_finance: false,
      can_manage_approvers: false,
      approver_department_ids: [],
      submission_policy: { mode: 'department', version: 1, reason: '', updated_at: null },
    },
  ],
  templates: [],
  scopes: { own: true, department: false, company: false, accounting: false },
};
function render(changes: Partial<BusinessContext> = {}, event?: BusinessEvent) {
  return renderToStaticMarkup(
    createElement(IntakeWizard, {
      session,
      context: { ...context, ...changes },
      initialEvent: event,
      onClose: () => {},
      onCompleted: () => {},
      onBusyChange: () => {},
    }),
  );
}

describe('员工录入分步引导接入现有表单', () => {
  it('表单顶部显示企业模板、附票填写、核对提交三个步骤，填写不等于已提交', () => {
    const html = render();
    expect(html).toContain('aria-label="员工录入引导"');
    expect(html).toContain('确认企业与模板');
    expect(html).toContain('附票并填写');
    expect(html).toContain('核对后提交');
    expect(html).toContain('尚未提交');
    expect(html.indexOf('aria-label="员工录入引导"')).toBeLessThan(html.indexOf('选择业务模板'));
  });
  it('部门路径解释部门审批、财务核验与会计复核，识别不成为新增必经步骤', () => {
    const html = render();
    expect(html).toContain('部门审批 → 企业财务核验 → 会计复核');
    expect(html).toContain('识别不是必填步骤');
  });
  it('直提路径说明授权会计接收，不声称已经审批或记账', () => {
    const html = render({
      companies: [
        {
          ...context.companies[0],
          department_id: null,
          submission_policy: { mode: 'direct', version: 1, reason: '', updated_at: null },
        },
      ],
    });
    expect(html).toContain('由授权会计接收资料');
    expect(html).toContain('会计复核后才能导出');
    expect(html).not.toContain('已提交财务');
  });
  it('没有当前企业提交权限时展示范围阻断，不能提示上传或提交', () => {
    const html = render({ companies: [{ ...context.companies[0], can_submit: false }] });
    expect(html).toContain('当前没有可提交的企业');
    expect(html).toContain('联系企业管理员确认企业和提交权限');
  });
});
