import { describe, expect, it } from 'vitest';
import {
  labelIdentity,
  measuredReplayRate,
  qualityEligible,
  qualityGroupRate,
  qualityMeasurement,
} from './quality-helpers';
import type { QualityStandard } from './quality-types';

describe('明确评测范围', () => {
  it('未覆盖和证据失效均不能计为通过，空评测不是零错误', () => {
    expect(measuredReplayRate({ passed: 0, different: 0, uncovered: 8, stale: 2 })).toBe(
      '尚无已测样本',
    );
    expect(measuredReplayRate({ passed: 1, different: 1, uncovered: 90, stale: 8 })).toBe('50%');
  });
  it('缺少工时测量必须显示尚未采集；独立确认但失效不能回放', () => {
    expect(qualityMeasurement(null)).toBe('尚未采集');
    expect(qualityMeasurement(0, '分钟')).toBe('0分钟');
    expect(qualityEligible({ confirmed: true, stale: true } as QualityStandard)).toBe(false);
    expect(
      labelIdentity({ extraction_id: 'original', document_index: 1, field: 'tax_amount' }),
    ).toBe('original:1:tax_amount');
  });
  it('过时的历史通过比例不得当作当前比例展示', () => {
    expect(qualityGroupRate({ context_stale: true, measured_match_rate: 1 })).toBe(
      '配置或证据已变化',
    );
    expect(qualityGroupRate({ context_stale: false, measured_match_rate: null })).toBe(
      '尚无已测样本',
    );
    expect(qualityGroupRate({ context_stale: false, measured_match_rate: 0.5 })).toBe('50%');
  });
});
