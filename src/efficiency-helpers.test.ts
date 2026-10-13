import { describe, expect, it } from 'vitest';
import { canAcknowledgeActivity, durationLabel, measuredRate } from './efficiency-helpers';
describe('explicit human timing', () => {
  it('does not fabricate sample rates', () => {
    expect(measuredRate(0, 0)).toBe('暂无样本');
    expect(measuredRate(2, 5)).toBe('40.0%');
  });
  it('labels only confirmed finite seconds', () => {
    expect(durationLabel(0)).toBe('0秒');
    expect(durationLabel(3661)).toBe('1小时1分钟');
    expect(durationLabel(Number.NaN)).toBe('0秒');
  });
  it('requires visible focused recent interaction', () => {
    expect(canAcknowledgeActivity(true, true, 20_000)).toBe(true);
    expect(canAcknowledgeActivity(false, true, 0)).toBe(false);
    expect(canAcknowledgeActivity(true, false, 0)).toBe(false);
    expect(canAcknowledgeActivity(true, true, 30_001)).toBe(false);
    expect(canAcknowledgeActivity(true, true, -1)).toBe(false);
  });
});
