import { describe, expect, it } from 'vitest';

describe('月度交付包范围、版本与客户签收', () => {
  it('概览逐项验证报表、阻断原因与权限，坏响应保持关闭', async () => {
    const { validateMonthlyOverview } = await import('./monthly-package-types');
    const value = {
      book_id: 'b1',
      company_id: 'c1',
      period: '2026-10',
      ready: true,
      blockers: [],
      packages: [],
      permissions: { generate: true },
      contact: { user_id: 'client', eligible: true, status: 'configured' },
      reports: [
        { id: 'r1', version: 1, status: 'published', current: true, content_hash: 'a'.repeat(64) },
      ],
    };
    expect(validateMonthlyOverview(value, 'b1')).toBe(true);
    expect(validateMonthlyOverview({ ...value, company_id: {} }, 'b1')).toBe(false);
    expect(
      validateMonthlyOverview({ ...value, blockers: [{ code: 'bad', message: {} }] }, 'b1'),
    ).toBe(false);
    expect(validateMonthlyOverview({ ...value, permissions: { generate: 'true' } }, 'b1')).toBe(
      false,
    );
    for (const patch of [
      { version: true },
      { current: 'false' },
      { status: 'draft' },
      { content_hash: 'none' },
      { id: '' },
    ])
      expect(
        validateMonthlyOverview({ ...value, reports: [{ ...value.reports[0], ...patch }] }, 'b1'),
      ).toBe(false);
  });
  it('拒绝错企业、错月份和伪造校验值', async () => {
    const { validatePackage } = await import('./monthly-package-types');
    const item = {
      id: 'p1',
      version: 1,
      book_id: 'b1',
      company_id: 'c1',
      period: '2026-10',
      report_id: 'r1',
      report_version: 2,
      manifest_sha256: 'a'.repeat(64),
      created_at: '2026-10-25',
      created_by: 'reviewer',
      status: 'frozen',
      contact_user_id: 'client',
      customer_receipt_status: 'not_recorded',
      external_import_status: 'separate_workflow',
      acknowledgement: null,
      can_acknowledge: false,
    };
    expect(validatePackage(item, { bookId: 'b1', companyId: 'c1', period: '2026-10' })).toBe(true);
    expect(validatePackage(item, { bookId: 'b2' })).toBe(false);
    expect(validatePackage(item, { bookId: 'b1', companyId: 'c2' })).toBe(false);
    expect(validatePackage({ ...item, version: true }, { bookId: 'b1' })).toBe(false);
    expect(validatePackage({ ...item, manifest_sha256: 'pending' }, { bookId: 'b1' })).toBe(false);
  });
  it('会计不能代客户签收，导入回执不代表客户已收到', async () => {
    const { packageReceiptLabel, canAcknowledgePackage } = await import('./monthly-package-types');
    const base = {
      contact_user_id: 'client',
      created_by: 'accountant',
      can_acknowledge: true,
      customer_receipt_status: 'not_recorded' as const,
    };
    expect(canAcknowledgePackage(base, 'accountant')).toBe(false);
    expect(canAcknowledgePackage(base, 'client')).toBe(true);
    expect(canAcknowledgePackage({ ...base, can_acknowledge: false }, 'client')).toBe(false);
    expect(packageReceiptLabel('not_recorded')).toBe('待客户签收');
    expect(packageReceiptLabel('acknowledged')).toBe('客户已签收');
  });
});
