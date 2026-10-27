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
      warnings: [],
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
      superseded: false,
      can_open: true,
      customer_opening_status: 'not_opened',
      customer_receipt_status: 'not_recorded',
      external_import_status: 'separate_workflow',
      opening: null,
      acknowledgement: null,
      can_acknowledge: false,
    };
    expect(validatePackage(item, { bookId: 'b1', companyId: 'c1', period: '2026-10' })).toBe(true);
    expect(validatePackage(item, { bookId: 'b2' })).toBe(false);
    expect(validatePackage(item, { bookId: 'b1', companyId: 'c2' })).toBe(false);
    expect(validatePackage({ ...item, version: true }, { bookId: 'b1' })).toBe(false);
    expect(validatePackage({ ...item, manifest_sha256: 'pending' }, { bookId: 'b1' })).toBe(false);
    expect(validatePackage({ ...item, customer_opening_status: 'opened' }, { bookId: 'b1' })).toBe(
      false,
    );
    expect(
      validatePackage(
        { ...item, opening: { opened_at: '2026-10-26T09:00:00Z', opened_by: 'someone' } },
        { bookId: 'b1' },
      ),
    ).toBe(false);
    expect(
      validatePackage(
        {
          ...item,
          files: [{ path: '../escape.csv', size_bytes: 10, sha256: 'b'.repeat(64) }],
          file_count: 1,
        },
        { bookId: 'b1' },
      ),
    ).toBe(false);
    expect(validatePackage({ ...item, files: [], file_count: 0 }, { bookId: 'b1' })).toBe(false);
    expect(
      validatePackage(
        {
          ...item,
          files: [
            { path: 'report.html', size_bytes: 2048, sha256: 'b'.repeat(64) },
            { path: 'account_balances.csv', size_bytes: 512, sha256: 'c'.repeat(64) },
          ],
          file_count: 2,
        },
        { bookId: 'b1' },
      ),
    ).toBe(true);
    // 清单字段名由后端 manifest 冻结为 size_bytes；写成 bytes 必须被拒，否则页面会显示 NaN。
    expect(
      validatePackage(
        {
          ...item,
          files: [{ path: 'report.html', bytes: 2048, sha256: 'b'.repeat(64) } as never],
          file_count: 1,
        },
        { bookId: 'b1' },
      ),
    ).toBe(false);
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
  it('客户视角状态区分未查看、已查看与已签收，并拒绝坏阻断项', async () => {
    const { packageOpeningLabel, validateBlocker, formatBytes } =
      await import('./monthly-package-types');
    const base = {
      customer_receipt_status: 'not_recorded' as const,
      customer_opening_status: 'not_opened' as const,
    };
    expect(packageOpeningLabel(base as never)).toBe('待客户查看');
    expect(packageOpeningLabel({ ...base, customer_opening_status: 'opened' } as never)).toBe(
      '客户已查看 · 待签收',
    );
    expect(packageOpeningLabel({ ...base, customer_receipt_status: 'acknowledged' } as never)).toBe(
      '客户已签收',
    );
    expect(validateBlocker({ code: 'contact_missing', message: '未配置联系人' })).toBe(true);
    expect(validateBlocker({ code: '', message: '空' })).toBe(false);
    expect(validateBlocker({ code: 'x', message: 1 })).toBe(false);
    expect(formatBytes(512)).toBe('512 B');
    expect(formatBytes(2048)).toBe('2.0 KB');
    expect(formatBytes(3 * 1024 * 1024)).toBe('3.0 MB');
  });
});
