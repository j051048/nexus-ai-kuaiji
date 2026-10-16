import { describe, expect, it } from 'vitest';
import type { BusinessEvent } from './business-types';
import {
  eligibleMaterialEvent,
  materialAttachmentIssue,
  materialSubmissionIssue,
  servicePlanAssignmentIssue,
} from './service-plan-types';

const material = { company_id: 'company-a', period: '2026-10' };
const event = {
  company_id: 'company-a',
  applicant_id: 'employee-a',
  payload: { occurred_on: '2026-10-16' },
  attachments: [{ id: 'file-a', superseded: false }],
  status: 'submitted',
} as BusinessEvent;

describe('周期资料的范围与独立验收提示', () => {
  it('银行和工资资料可直接提供原件，不需要构造含金额的业务事项', () => {
    expect(materialSubmissionIssue('provided', '本月完整银行对账单', [], ['file-a'])).toBe('');
    expect(materialAttachmentIssue({ name: '银行流水.csv', size: 200 }, [])).toBe('');
    expect(materialAttachmentIssue({ name: '工资汇总.xlsx', size: 200 }, [])).toBe('');
    expect(materialAttachmentIssue({ name: '宏表.xlsm', size: 200 }, [])).toContain('支持的格式');
    expect(materialAttachmentIssue({ name: '资料.pdf', size: 10 * 1024 * 1024 + 1 }, [])).toContain(
      '超过',
    );
  });
  it('只允许本人、本企业、本月且有有效原件的已提交事项成为候选', () => {
    expect(eligibleMaterialEvent(event, material, 'employee-a')).toBe(true);
    expect(
      eligibleMaterialEvent({ ...event, company_id: 'company-b' }, material, 'employee-a'),
    ).toBe(false);
    expect(
      eligibleMaterialEvent({ ...event, applicant_id: 'employee-b' }, material, 'employee-a'),
    ).toBe(false);
    expect(
      eligibleMaterialEvent(
        { ...event, payload: { ...event.payload, occurred_on: '2026-09-30' } },
        material,
        'employee-a',
      ),
    ).toBe(false);
    expect(
      eligibleMaterialEvent(
        { ...event, attachments: [{ ...event.attachments[0], superseded: true }] },
        material,
        'employee-a',
      ),
    ).toBe(false);
    expect(eligibleMaterialEvent({ ...event, status: 'draft' }, material, 'employee-a')).toBe(
      false,
    );
  });
  it('无发生和不适用都必须提供依据；提供资料必须有已选原件事项', () => {
    expect(materialSubmissionIssue('no_activity', '', [])).toContain('说明');
    expect(materialSubmissionIssue('not_applicable', '  ', [])).toContain('说明');
    expect(materialSubmissionIssue('provided', '已核对完整资料', [])).toContain('带附件');
    expect(materialSubmissionIssue('provided', '已核对完整资料', ['event-a'])).toBe('');
    expect(materialSubmissionIssue('provided', '已核对完整资料', ['event-a', 'event-a'])).toContain(
      '重复',
    );
    expect(materialSubmissionIssue('no_activity', '已核对银行流水，本月无此业务', [])).toBe('');
  });
  it('不能把同一人同时选作主办与复核，也不能继续使用失权候选', () => {
    const candidates = [
      { user_id: 'a', name: '主办', can_lead: true, can_review: true, can_contact: false },
      { user_id: 'b', name: '独立审核', can_lead: false, can_review: true, can_contact: false },
    ];
    expect(servicePlanAssignmentIssue('a', 'a', candidates)).toContain('不同人员');
    expect(servicePlanAssignmentIssue('b', 'a', candidates)).toContain('主办会计');
    expect(servicePlanAssignmentIssue('a', 'removed', candidates)).toContain('权限已变更');
    expect(servicePlanAssignmentIssue('a', 'b', candidates)).toBe('');
    expect(servicePlanAssignmentIssue('', '', candidates)).toBe('');
  });
});
