import { describe, expect, it } from 'vitest';
import {
  initialBusinessPayload,
  initialBusinessScope,
  normalizeBusinessPayload,
  MAX_ATTACHMENT_BYTES,
  MAX_TOTAL_ATTACHMENT_BYTES,
  validateAttachment,
  validateBusinessPayload,
} from './business-forms';
import type {
  BusinessCompany,
  BusinessContext,
  BusinessPayload,
  BusinessTemplate,
} from './business-types';

function valid(template: BusinessTemplate): BusinessPayload {
  return {
    ...initialBusinessPayload(template),
    occurred_on: '2026-10-11',
    description: '上海客户拜访费用',
    counterparty: '某交通服务公司',
    amount: '128.50',
  };
}
describe('business intake boundaries', () => {
  it('月度资料下钻对选中企业的财务兼会计身份优先企业范围，不漏掉部门审批事项', () => {
    const company: BusinessCompany = {
      id: 'selected',
      name: '合成选中企业',
      department_id: null,
      departments: [],
      can_manage_approvers: false,
      role: 'finance',
      can_finance: true,
      can_submit: true,
      approver_department_ids: [],
    };
    const context: BusinessContext = {
      companies: [company],
      templates: [],
      scopes: { own: true, company: true, department: false, accounting: true },
    };
    expect(initialBusinessScope(context, 'selected')).toBe('company');
  });
  it('其他企业的财务身份不决定当前企业范围，授权会计仅按会计范围读取', () => {
    const selected: BusinessCompany = {
      id: 'selected',
      name: '合成选中企业',
      department_id: null,
      departments: [],
      can_manage_approvers: false,
      role: null,
      can_finance: false,
      can_submit: false,
      approver_department_ids: [],
    };
    const other: BusinessCompany = {
      id: 'other',
      name: '合成其他企业',
      department_id: null,
      departments: [],
      can_manage_approvers: false,
      role: 'finance',
      can_finance: true,
      can_submit: true,
      approver_department_ids: [],
    };
    const context: BusinessContext = {
      companies: [selected, other],
      templates: [],
      scopes: { own: true, company: true, department: false, accounting: true },
    };
    expect(initialBusinessScope(context, 'selected')).toBe('accounting');
    expect(
      initialBusinessScope(
        { ...context, scopes: { ...context.scopes, accounting: false } },
        'selected',
      ),
    ).toBe('own');
  });
  it.each(['reimbursement', 'procurement_expense', 'daily_cashflow'] as BusinessTemplate[])(
    'accepts a valid %s payload with its fixed defaults',
    (template) => {
      expect(validateBusinessPayload(template, valid(template))).toBe('');
    },
  );
  it.each(['0', '0.00', '-1.00', '1.001', '1e3', 'NaN', '01.00', '1,000.00', ' 1.00 '])(
    'rejects ambiguous or invalid amount %s without floating point conversion',
    (amount) => {
      expect(
        validateBusinessPayload('reimbursement', { ...valid('reimbursement'), amount }),
      ).toContain('金额');
    },
  );
  it('preserves exact cents at the supported large amount boundary', () => {
    expect(
      validateBusinessPayload('reimbursement', {
        ...valid('reimbursement'),
        amount: '999999999999.99',
      }),
    ).toBe('');
    expect(
      validateBusinessPayload('reimbursement', {
        ...valid('reimbursement'),
        amount: '1000000000000.00',
      }),
    ).toContain('金额');
  });
  it('canonicalizes semantically identical amounts before building the retry signature', () => {
    expect(normalizeBusinessPayload({ ...valid('reimbursement'), amount: '0.3' })).toEqual(
      normalizeBusinessPayload({ ...valid('reimbursement'), amount: '0.30' }),
    );
    expect(
      normalizeBusinessPayload({ ...valid('reimbursement'), amount: '999999999999.99' }).amount,
    ).toBe('999999999999.99');
  });
  it.each([
    '1999-01-01',
    '2100-01-01',
    '2026-02-29',
    '2026-13-01',
    '2026-10-32',
    '2026-1-01',
    'invalid',
  ])('rejects invalid calendar date %s safely', (occurred_on) => {
    expect(
      validateBusinessPayload('reimbursement', { ...valid('reimbursement'), occurred_on }),
    ).toContain('日期');
  });
  it('prevents template changes from silently changing the accounting intent', () => {
    expect(
      validateBusinessPayload('reimbursement', {
        ...valid('reimbursement'),
        payment_status: 'paid',
      }),
    ).toContain('报销');
    expect(
      validateBusinessPayload('procurement_expense', {
        ...valid('procurement_expense'),
        direction: 'income',
      }),
    ).toContain('支出');
    expect(
      validateBusinessPayload('daily_cashflow', {
        ...valid('daily_cashflow'),
        payment_status: 'unpaid',
      }),
    ).toContain('已经收付');
  });
  it('rejects blank business evidence fields', () => {
    expect(
      validateBusinessPayload('reimbursement', { ...valid('reimbursement'), description: '  ' }),
    ).toContain('摘要');
    expect(
      validateBusinessPayload('reimbursement', { ...valid('reimbursement'), counterparty: '' }),
    ).toContain('对方');
  });
  it('preserves project evidence while normalizing equivalent retry commands', () => {
    const input = { ...valid('reimbursement'), project: '  客户实施项目  ' };
    const normalized = normalizeBusinessPayload(input);
    expect(normalized.project).toBe('客户实施项目');
    expect(normalized).toEqual(normalizeBusinessPayload({ ...input, project: '客户实施项目' }));
    expect(JSON.stringify(normalized)).not.toBe(
      JSON.stringify(normalizeBusinessPayload({ ...input, project: '另一个客户项目' })),
    );
    expect(
      normalizeBusinessPayload({ ...valid('reimbursement'), project: undefined }).project,
    ).toBe('');
  });
  it('allows optional project details within the server length boundary', () => {
    expect(
      validateBusinessPayload('reimbursement', { ...valid('reimbursement'), project: '' }),
    ).toBe('');
    expect(
      validateBusinessPayload('reimbursement', {
        ...valid('reimbursement'),
        project: '项'.repeat(200),
      }),
    ).toBe('');
    expect(
      validateBusinessPayload('reimbursement', {
        ...valid('reimbursement'),
        project: '项'.repeat(201),
      }),
    ).toContain('项目');
  });
});
describe('business attachment limits', () => {
  const file = { name: '交通发票.pdf', type: 'application/pdf', size: 1024 };
  it.each(['application/pdf', 'image/png', 'image/jpeg', 'image/webp'])(
    'accepts supported %s',
    (type) => expect(validateAttachment({ ...file, type }, 0, 0)).toBe(''),
  );
  it('enforces per-file, per-event count and aggregate byte limits', () => {
    expect(validateAttachment({ ...file, size: 0 }, 0, 0)).toContain('大于零');
    expect(validateAttachment({ ...file, size: MAX_ATTACHMENT_BYTES + 1 }, 0, 0)).toContain(
      '10 MB',
    );
    expect(validateAttachment(file, 10, 0)).toContain('10 个');
    expect(validateAttachment(file, 9, MAX_TOTAL_ATTACHMENT_BYTES)).toContain('50 MB');
  });
  it('rejects an executable renamed as a PDF when the browser MIME is unsupported', () => {
    expect(validateAttachment({ ...file, type: 'application/x-msdownload' }, 0, 0)).toContain(
      '仅支持',
    );
  });
});
