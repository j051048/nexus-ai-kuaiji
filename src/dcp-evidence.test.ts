import { describe, expect, it } from 'vitest';
import { investigationCommand, readInvestigation } from './dcp-evidence';

describe('DCP evidence scope', () => {
  const result = {
    book_id: 'a',
    period: '2026-10',
    status: 'complete',
    model_calls: 0,
    coverage: { read: 3, of: 3 },
    findings: { voucher_status: {}, close_blockers: {}, delivery_status: {} },
    missing: [],
    steps: ['voucher_status', 'close_blockers', 'delivery_status'].map((tool) => ({
      tool,
      ok: true,
    })),
    evidence: { firm_id: 'f', company_id: 'c', fingerprint: 'a'.repeat(64) },
  };
  it('accepts exact scope and rejects wrong book/period/company or malformed counts', () => {
    expect(readInvestigation(result, 'a', '2026-10', 'c', 'f')).toEqual(result);
    for (const bad of [
      { ...result, book_id: 'b' },
      { ...result, period: '2026-11' },
      { ...result, evidence: { ...result.evidence, company_id: 'other' } },
      { ...result, coverage: { read: true, of: 3 } },
      { ...result, model_calls: 1 },
      { ...result, missing: ['voucher_status'] },
    ])
      expect(() => readInvestigation(bad, 'a', '2026-10', 'c', 'f')).toThrow();
  });
  it('does not accept stale facts attached to revoked results', () => {
    expect(() =>
      readInvestigation({ ...result, status: 'blocked', evidence: null }, 'a', '2026-10', 'c', 'f'),
    ).toThrow();
  });
  it('checks actual parallel read counts and preserves zero model calls', () => {
    const execution = {
      mode: 'bounded_parallel',
      parallelism: 2,
      max_reads: 3,
      max_total_reads: 6,
      attempted_reads: 3,
      validation_reads: 3,
      accepted_reads: 3,
      model_calls: 0,
      tokens: 0,
      cost_cny: null,
      elapsed_ms: 12,
      budget_seconds: 3,
      stop_reason: null,
    };
    expect(
      readInvestigation({ ...result, execution }, 'a', '2026-10', 'c', 'f').execution?.parallelism,
    ).toBe(2);
    for (const patch of [
      { validation_reads: 0 },
      { attempted_reads: true },
      { parallelism: 4 },
      { elapsed_ms: NaN },
      { cost_cny: 0 },
      { stop_reason: 'timeout' },
    ]) {
      expect(() =>
        readInvestigation(
          { ...result, execution: { ...execution, ...patch } },
          'a',
          '2026-10',
          'c',
          'f',
        ),
      ).toThrow();
    }
  });
  it('freezes unknown attempt and package in manual command', () => {
    const item = {
      id: 'n',
      state: 'unknown',
      attempts: 1,
      recipient: { user_id: 'u' },
      package: { id: 'p', version: 2, manifest_sha256: 'a'.repeat(64) },
      investigation: { revision: 3 },
    };
    expect(
      investigationCommand(item, 'not_sent', '合成通道记录', '人工核查依据', 'key-12345'),
    ).toMatchObject({
      expected_attempts: 1,
      expected_revision: 3,
      expected_package_version: 2,
      expected_recipient_user_id: 'u',
    });
    expect(() =>
      investigationCommand(
        { ...item, state: 'sending' },
        'not_sent',
        '合成记录',
        '核查依据',
        'key-12345',
      ),
    ).toThrow();
  });
});
