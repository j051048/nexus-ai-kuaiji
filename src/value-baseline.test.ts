import { describe, expect, it } from 'vitest';
import { cny, percent, readValueBaseline } from './value-baseline';

const sample = {
  company_id: 'company-1',
  period: '2026-10',
  counts: { submitted_events: 10, mapped_drafts: 9, approved_drafts: 8, returned_events: 1 },
  rates: {
    draft_mapping_rate: 0.9,
    first_pass_rate: 0.9,
    exception_rate: 0.1,
    review_conversion_rate: 0.8889,
  },
  elapsed: {
    system_elapsed_hours_median: 3.2,
    system_elapsed_hours_samples: 8,
    labour_time_verified: false,
    note: '不代表人工工时',
  },
  cost: {
    ai_model_cost_micro: 1_250_000,
    ai_cost_per_approved_micro: 156_250,
    scope: 'ai_model_only',
    note: '不含人工',
  },
};

describe('value baseline contract', () => {
  it('formats rates and AI cost without pretending labour time is known', () => {
    const value = readValueBaseline(sample);
    expect(percent(value.rates.first_pass_rate)).toBe('90.0%');
    expect(percent(null)).toBe('暂无');
    expect(cny(value.cost.ai_cost_per_approved_micro)).toBe('¥0.16');
    expect(value.elapsed.labour_time_verified).toBe(false);
  });

  it('rejects rates outside 0..1 and a labour-time claim', () => {
    expect(() =>
      readValueBaseline({ ...sample, rates: { ...sample.rates, first_pass_rate: 1.2 } }),
    ).toThrow('价值基线比率无效');
    expect(() =>
      readValueBaseline({ ...sample, elapsed: { ...sample.elapsed, labour_time_verified: true } }),
    ).toThrow('价值基线口径无效');
  });
});
