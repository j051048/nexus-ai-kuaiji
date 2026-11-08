import { describe, expect, it } from 'vitest';
import { measuredRate } from './efficiency-helpers';
import { readInvestigation } from './dcp-evidence';
import { readFirmSnapshot } from './firm-observability-contract';
import { intakeNextStep } from './intake-helpers';
import type { IntakeRun } from './intake-types';

describe('0712 不把异常输入变成业务结论', () => {
  it.each([
    [2, 1],
    [-1, 3],
    [0.1, 3],
    [NaN, 3],
    [1, Infinity],
  ])('拒绝不守恒比例 %s/%s', (a, b) => {
    expect(measuredRate(a, b)).toBe('数据待核对');
  });
  it('完整调查不能没有执行步骤', () => {
    expect(() =>
      readInvestigation(
        {
          book_id: 'b',
          period: '2026-10',
          status: 'complete',
          model_calls: 0,
          coverage: { read: 3, of: 3 },
          findings: { voucher_status: {}, close_blockers: {}, delivery_status: {} },
          missing: [],
          steps: [],
          evidence: { firm_id: 'f', company_id: 'c', fingerprint: 'a'.repeat(64) },
        },
        'b',
        '2026-10',
        'c',
        'f',
      ),
    ).toThrow();
  });
  it('调查不能混用其他事务所证据', () => {
    expect(() =>
      readInvestigation(
        {
          book_id: 'b',
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
          evidence: { firm_id: 'foreign', company_id: 'c', fingerprint: 'a'.repeat(64) },
        },
        'b',
        '2026-10',
        'c',
        'f',
      ),
    ).toThrow();
  });
});

describe('0712 运行记录只接受可信范围和有效计数', () => {
  const value = {
    firm_id: 'f',
    period: '2026-10',
    queues: {},
    alerts: {},
    projection: { total: 0, dirty: 0, never_built: 0, failing: 0 },
    metrics: {
      buckets: 0,
      transactions: 0,
      writes: 0,
      rollbacks: 0,
      avg_connection_ms: null,
      avg_lock_ms: null,
      max_transaction_ms: null,
    },
    cost_summary: { sample_count: 1, known_micro: null, unknown_count: 1, estimated_count: 0 },
    signals: [
      {
        code: 'cost_unknown',
        owner_role: 'admin',
        next_action: '核定费用',
        target: 'operations',
        automatic_action: false,
      },
    ],
    worker_health: { available: false, last_heartbeat_at: null, stale_after_seconds: 90 },
  };
  it('未知费用不是零且不能自动重发', () => {
    expect(readFirmSnapshot(value, 'f', '2026-10').cost_summary.known_micro).toBeNull();
    for (const bad of [
      { ...value, firm_id: 'other' },
      { ...value, period: '2026-11' },
      { ...value, cost_summary: { ...value.cost_summary, sample_count: 0 } },
      { ...value, signals: [{ ...value.signals[0], automatic_action: true }] },
      { ...value, metrics: { ...value.metrics, avg_lock_ms: NaN } },
      { ...value, worker_health: { ...value.worker_health, available: 'yes' } },
    ])
      expect(() => readFirmSnapshot(bad, 'f', '2026-10')).toThrow();
  });
  it('上传和人工确认是独立步骤', () => {
    expect(intakeNextStep('', 0, [], false).text).toContain('先选择企业');
    expect(intakeNextStep('c', 0, [], false).text).toContain('先拍照');
    expect(intakeNextStep('c', 1, [], false).text).toContain('原件已保存');
    expect(intakeNextStep('c', 1, [], true).text).toContain('确认');
  });
  it('识别失败不能鼓励直接再次调用', () => {
    const run: IntakeRun = {
      id: 'r',
      attachment_id: 'a',
      status: 'failed',
      error_code: 'expired',
      error_detail: '待核定费用',
      result: null,
      checks: [],
    };
    expect(intakeNextStep('c', 1, [run], false).text).toContain('核查费用');
  });
});
