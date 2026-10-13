import { describe, expect, it } from 'vitest';
import { canConfirmClose, closingTarget, type ClosingSnapshot } from './closing-types';
describe('月结必须当前明确复核', () => {
  const snapshot: ClosingSnapshot = {
    id: 's',
    book_id: 'b',
    period: '2026-10',
    input_fingerprint: 'fp',
    created_by: 'u',
    created_at: 'now',
    report: {
      engine_version: 1,
      passed: true,
      issue_count: 0,
      truncated: false,
      counts: {},
      issues: [],
    },
    stale: false,
    historical: false,
    reviewed_by_me: true,
  };
  it('阻断、陈旧、历史或未复核快照不能锁期', () => {
    expect(canConfirmClose(snapshot, 'open')).toBe(true);
    expect(canConfirmClose({ ...snapshot, stale: true }, 'open')).toBe(false);
    expect(canConfirmClose({ ...snapshot, reviewed_by_me: false }, 'open')).toBe(false);
    expect(canConfirmClose({ ...snapshot, historical: true }, 'open')).toBe(false);
    expect(canConfirmClose(snapshot, 'closed')).toBe(false);
    expect(canConfirmClose(null, 'open')).toBe(false);
  });
  it('未公开业务总数没有私有详情跳转', () => {
    expect(closingTarget('book', 'hidden-event')).toBeNull();
    expect(closingTarget('export', 'id:1')).toBe('export:id%3A1');
  });
});
