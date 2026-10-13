import { describe, expect, it } from 'vitest';
import { automationReason, durationLabel } from './queue-automation-helpers';
describe('后台续跑与性能展示', () => {
  it('耗时覆盖毫秒到分钟并拒绝无效数值', () => {
    expect(durationLabel(12)).toBe('12 毫秒'); expect(durationLabel(1500)).toBe('1.5 秒'); expect(durationLabel(90_000)).toBe('1.5 分钟'); expect(durationLabel(Number.NaN)).toBe('—'); expect(durationLabel(-1)).toBe('—');
  });
  it('启用配置须有可阅读依据', () => {
    expect(automationReason('   ')).toBeTruthy(); expect(automationReason('同意')).toBeTruthy(); expect(automationReason(' 核验补件后续跑 ')).toBeNull(); expect(automationReason('甲'.repeat(1001))).toBeTruthy();
  });
});
