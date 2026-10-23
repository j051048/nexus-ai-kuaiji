import { describe, expect, it } from 'vitest';
import { businessGuidance, businessHandoffNotice, businessStatusLabel } from './business-guidance';
import type { BusinessEvent, BusinessStatus } from './business-types';

const permissions: BusinessEvent['permissions'] = {
  edit: false,
  submit: false,
  department_approve: false,
  finance_approve: false,
  return: false,
  handoff: false,
};
function item(status: BusinessStatus, changes: Partial<BusinessEvent> = {}): BusinessEvent {
  return {
    id: 'event-a',
    firm_id: 'firm-a',
    company_id: 'company-a',
    department_id: 'department-a',
    applicant_id: 'employee-a',
    template_id: 'reimbursement',
    template_version: 1,
    version: 3,
    status,
    payload: {
      occurred_on: '2026-10-20',
      description: '虚构差旅',
      counterparty: '虚构酒店',
      amount: '120.00',
      direction: 'expense',
      payment_status: 'paid',
      category: 'travel',
    },
    attachments: [
      {
        id: 'attachment-a',
        filename: '票据.pdf',
        media_type: 'application/pdf',
        sha256: 'a'.repeat(64),
        size_bytes: 123,
      },
    ],
    actions: [],
    versions: [],
    mapping: null,
    permissions,
    ...changes,
  };
}

describe('业务事项引导以当前事实和权限解释下一步', () => {
  it('交接回执没有映射时不把成功提示写成已生成草稿', () => {
    expect(businessHandoffNotice(item('handed_off'))).not.toContain('已生成凭证草稿');
    expect(businessHandoffNotice(item('handed_off'))).toContain('草稿关联暂未返回');
    expect(businessHandoffNotice(undefined)).toContain('结果暂未确认');
    expect(businessHandoffNotice(item('finance_approved'))).toContain('结果暂未确认');
    expect(
      businessHandoffNotice(
        item('handed_off', {
          mapping: { book_id: 'book-a', source_id: 'source-a', draft_id: 'draft-a' },
        }),
      ),
    ).toContain('仍需会计独立复核后导出');
  });
  it('本人草稿缺附件时先上传，不能声称已提交', () => {
    const guide = businessGuidance(
      item('draft', { attachments: [], permissions: { ...permissions, edit: true, submit: true } }),
    );
    expect(guide.status).toContain('尚未提交');
    expect(guide.next).toContain('上传原始附件');
    expect(guide.steps[0].state).toBe('current');
    expect(guide.steps.slice(1).every((step) => step.state === 'upcoming')).toBe(true);
  });
  it('无编辑和提交权限的只读草稿不诱导操作', () => {
    expect(businessGuidance(item('draft')).next).toContain('等待申请人');
    expect(businessGuidance(item('draft')).next).not.toContain('点击');
  });
  it('有提交但无编辑权限不能引导点击不存在的上传按钮', () => {
    const guide = businessGuidance(
      item('draft', { attachments: [], permissions: { ...permissions, submit: true } }),
    );
    expect(guide.next).toContain('不能上传');
    expect(guide.next).not.toContain('先上传');
  });
  it('只有编辑权限的退回事项不诱导越权重提', () => {
    const guide = businessGuidance(
      item('returned', { permissions: { ...permissions, edit: true } }),
    );
    expect(guide.next).toContain('修改事项');
    expect(guide.next).toContain('暂无提交权限');
    expect(guide.next).not.toContain('核对后重新提交');
  });
  it('退回显示最后一条真实退回原因，指导修改重提而非新增重复事项', () => {
    const guide = businessGuidance(
      item('returned', {
        actions: [
          { stage: 'department', decision: 'return', reason: '旧原因' },
          { stage: 'finance', decision: 'return', reason: '请补上完整行程单' },
        ],
        permissions: { ...permissions, edit: true, submit: true },
      }),
    );
    expect(guide.status).toContain('请补上完整行程单');
    expect(guide.status).not.toContain('旧原因');
    expect(guide.next).toContain('修改事项');
    expect(guide.next).toContain('重新提交');
    expect(guide.steps[0].state).toBe('blocked');
  });
  it('退回原因缺失时明确未知，非申请人等待本人补充', () => {
    const guide = businessGuidance(item('returned'));
    expect(guide.status).toContain('未显示退回原因');
    expect(guide.next).toContain('等待申请人');
  });
  it('部门模式提交后不把企业财务或其他只读人当审批人', () => {
    const guide = businessGuidance(item('submitted'));
    expect(guide.status).toContain('等待部门审批');
    expect(guide.next).toContain('等待获授权的部门审批人');
    expect(
      businessGuidance(
        item('submitted', { permissions: { ...permissions, department_approve: true } }),
      ).next,
    ).toContain('部门审批通过');
  });
  it('只有部门通过权限时不提示执行退回', () => {
    const guide = businessGuidance(
      item('submitted', { permissions: { ...permissions, department_approve: true } }),
    );
    expect(guide.next).toContain('部门审批通过');
    expect(guide.next).not.toContain('退回修改');
  });
  it('直提待授权会计接收，不能提示部门审批或财务核验', () => {
    const event = item('submitted', { submission_mode: 'direct' });
    const guide = businessGuidance(event);
    expect(businessStatusLabel(event)).toBe('待会计接收');
    expect(guide.status).toContain('等待授权会计接收');
    expect(guide.next).toContain('等待授权会计');
    expect(guide.steps.map((step) => step.label).join('')).not.toMatch(/部门审批|财务核验/);
  });
  it('直提有接收权时先核对原件并选本企业同月份账套', () => {
    const guide = businessGuidance(
      item('submitted', {
        submission_mode: 'direct',
        permissions: { ...permissions, handoff: true, return: true },
      }),
      { booksReady: true, hasEligibleBook: true },
    );
    expect(guide.next).toContain('本企业');
    expect(guide.next).toContain('2026-10');
    expect(guide.next).toContain('选择');
  });
  it('财务核验中提示核对事实和异常退回，审核成功仍不等于入账', () => {
    const guide = businessGuidance(
      item('department_approved', {
        permissions: { ...permissions, finance_approve: true, return: true },
      }),
    );
    expect(guide.status).toContain('尚未生成正式凭证');
    expect(guide.next).toContain('财务核验通过');
    expect(guide.checks?.join('')).toMatch(/金额.*交易对方.*日期/);
    expect(guide.checks?.join('')).toContain('退回修改');
  });
  it('无财务核验权限时只读，不提供本人批准提示', () => {
    expect(businessGuidance(item('department_approved')).next).toContain('等待本企业获授权财务');
  });
  it('核验通过说明尚未记账，账套读取失败不能建议直接生成', () => {
    const guide = businessGuidance(
      item('finance_approved', { permissions: { ...permissions, handoff: true } }),
      { booksError: '授权已变更' },
    );
    expect(guide.status).toContain('尚未记账');
    expect(guide.next).toContain('重新读取事项账套');
    expect(guide.next).not.toContain('点击“生成凭证草稿”');
    expect(guide.tone).toBe('warning');
  });
  it('核验通过但没有可用账套时明确联系管理员，不跨企业或月份替代', () => {
    const guide = businessGuidance(
      item('finance_approved', { permissions: { ...permissions, handoff: true } }),
      { booksReady: true, hasEligibleBook: false },
    );
    expect(guide.next).toContain('管理员');
    expect(guide.next).toContain('本企业');
    expect(guide.next).toContain('2026-10');
  });
  it('只有当前可交接账套实际选定后才建议生成草稿', () => {
    const event = item('finance_approved', { permissions: { ...permissions, handoff: true } });
    expect(businessGuidance(event, { booksReady: true, hasEligibleBook: true }).next).toContain(
      '选择',
    );
    expect(
      businessGuidance(event, {
        booksReady: true,
        hasEligibleBook: true,
        selectedBookEligible: true,
      }).next,
    ).toContain('点击“生成凭证草稿”');
  });
  it('已交接但没有草稿映射，不伪造已经生成或已审核导出', () => {
    const guide = businessGuidance(item('handed_off'));
    expect(guide.status).toContain('草稿关联暂未显示');
    expect(guide.status).not.toContain('已生成凭证草稿');
    expect(guide.next).toContain('重新读取');
    expect(guide.steps[guide.steps.length - 1]?.state).toBe('blocked');
  });
  it('已交接有映射也只陈述业务交接，不推断凭证审核或导出完成', () => {
    const guide = businessGuidance(
      item('handed_off', {
        mapping: { book_id: 'book-a', source_id: 'source-a', draft_id: 'draft-a' },
      }),
    );
    expect(guide.status).toContain('不代表凭证已审核或已导出');
    expect(guide.next).toContain('授权会计');
    expect(guide.steps[guide.steps.length - 1]?.state).toBe('current');
  });
  it('处理期间与操作失败优先提示等待/重新核对，不引导重复发送', () => {
    const event = item('finance_approved', { permissions: { ...permissions, handoff: true } });
    expect(businessGuidance(event, { busy: true }).next).toContain('不要重复提交');
    const failed = businessGuidance(event, { error: '结果暂未确认' });
    expect(failed.next).toContain('重新读取');
    expect(failed.status).toContain('结果暂未确认');
    expect(failed.tone).toBe('warning');
  });
  it('状态说明不改写直提和部门模式的既有状态', () => {
    for (const status of [
      'draft',
      'returned',
      'submitted',
      'department_approved',
      'finance_approved',
      'handed_off',
    ] as const) {
      const event = item(status);
      const before = JSON.stringify(event);
      expect(businessStatusLabel(event)).toBeTruthy();
      businessGuidance(event);
      expect(JSON.stringify(event)).toBe(before);
    }
  });
});
