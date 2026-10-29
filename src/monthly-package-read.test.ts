import { describe, expect, it } from 'vitest';

import {
  acceptPackage,
  packageFromSearch,
  readRecovery,
  selectPackage,
} from './monthly-package-read';
import type { MonthlyPackage } from './monthly-package-types';

const HASH = 'a'.repeat(64);

function pack(over: Partial<MonthlyPackage> = {}): MonthlyPackage {
  return {
    id: 'pkg-1',
    version: 1,
    period: '2026-10',
    book_id: 'book-1',
    company_id: 'company-1',
    contact_user_id: 'user-1',
    manifest_sha256: HASH,
    superseded: false,
    can_open: true,
    customer_opening_status: 'not_opened',
    report_id: 'report-1',
    report_version: 3,
    created_by: 'maker',
    created_at: '2026-10-26T00:00:00+08:00',
    status: 'frozen',
    can_acknowledge: true,
    customer_receipt_status: 'not_recorded',
    external_import_status: 'separate_workflow',
    opening: null,
    acknowledgement: null,
    ...over,
  } as unknown as MonthlyPackage;
}

const expected = {
  id: 'pkg-1',
  version: 1,
  bookId: 'book-1',
  companyId: 'company-1',
  period: '2026-10',
  manifestSha256: HASH,
};

describe('monthly package acceptance', () => {
  it('accepts only the package that was requested', () => {
    expect(acceptPackage(pack(), expected)?.id).toBe('pkg-1');
    expect(acceptPackage(pack({ id: 'pkg-2' }), expected)).toBeNull();
  });

  it('rejects a different version even when the response reuses the same id and manifest', () => {
    expect(acceptPackage(pack({ version: 2 }), expected)).toBeNull();
  });

  it('distinguishes login expiry, insufficient permission and MFA without offering blind retries', () => {
    expect(readRecovery(401).message).toContain('登录');
    expect(readRecovery(403).message).toContain('授权');
    expect(readRecovery(403, 'admin_mfa_required').message).toContain('二次验证');
    expect(readRecovery(409).message).toContain('版本');
  });

  it('rejects a response from another company or period', () => {
    expect(acceptPackage(pack({ company_id: 'company-2' }), expected)).toBeNull();
    expect(acceptPackage(pack({ period: '2026-11' }), expected)).toBeNull();
  });

  it('rejects a manifest that does not match the version the caller expects', () => {
    expect(acceptPackage(pack({ manifest_sha256: 'b'.repeat(64) }), expected)).toBeNull();
    expect(acceptPackage(pack(), { ...expected, manifestSha256: undefined })?.id).toBe('pkg-1');
  });

  it('never silently swaps an old deep link for the newest version', () => {
    const selection = selectPackage([pack({ id: 'pkg-2', version: 2 })], 'pkg-1');

    expect(selection.kind).toBe('missing');
    expect(selection.item).toBeNull();
    expect(selection.message).toContain('不在当前企业或月份可见范围内');
  });

  it('keeps a superseded version reachable but says it is superseded', () => {
    const selection = selectPackage(
      [pack({ id: 'pkg-1', superseded: true }), pack({ id: 'pkg-2', version: 2 })],
      'pkg-1',
    );

    expect(selection.kind).toBe('superseded');
    expect(selection.item?.id).toBe('pkg-1');
    expect(selection.message).toContain('已被更新版本取代');
  });

  it('treats a failed read as retryable without pretending the receipt changed', () => {
    const recovery = readRecovery(503);

    expect(recovery.retryable).toBe(true);
    expect(recovery.message).toContain('签收状态没有改变');
    expect(readRecovery(403).retryable).toBe(false);
  });

  it('only accepts a deep link that looks like a package id', () => {
    expect(packageFromSearch('?package=pkg-1')).toBe('');
    expect(packageFromSearch('?package=6ace1d4e-f815-6d03-1963-b75911112222')).toBe(
      '6ace1d4e-f815-6d03-1963-b75911112222',
    );
    expect(packageFromSearch('')).toBe('');
  });
  it('rejects ambiguous repeated package targets instead of accepting the first', () => {
    expect(packageFromSearch('?package=aaaaaaaa&package=bbbbbbbb')).toBe('');
    expect(packageFromSearch('?package=aaaaaaaa&package=aaaaaaaa')).toBe('');
  });
});
