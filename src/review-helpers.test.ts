import { describe, expect, it } from 'vitest';
import { reviewGroups, reviewSelection } from './review-helpers';
import type { ReviewPending } from './review-types';

const first: ReviewPending = {
  book_id: 'book1',
  draft_id: 'd1',
  expected_version: 2,
  company_id: 'c1',
  company_name: '甲企业',
  book_name: '甲账套',
  period: '2026-10',
  template: 'bank',
  risk: 'low',
  risk_reasons: [],
  risk_rule_version: 'review-risk-v1',
  risk_legacy: 'basic_clear',
  issue_count: 0,
  description: '办公费',
  source_date: '2026-10-01',
  source_amount: '100.00',
};
describe('人工批量复核分组与选择', () => {
  it('企业、期间、业务类型和风险分档互相隔离', () => {
    const groups = reviewGroups([
      first,
      { ...first, draft_id: 'd2' },
      { ...first, company_id: 'c2', draft_id: 'd3' },
      { ...first, period: '2026-11', draft_id: 'd4' },
      { ...first, template: 'reimbursement', draft_id: 'd5' },
      { ...first, risk: 'high', draft_id: 'd6' },
    ]);
    expect(groups).toHaveLength(5);
    expect(groups[0].items).toHaveLength(2);
    expect(groups[groups.length - 1].label).toContain('高风险');
  });
  it('选择只包含当前页现时版本，不提交旧范围或不存在条目', () => {
    expect(reviewSelection([first], new Set(['d1', 'old-scope']))).toEqual([
      { book_id: 'book1', draft_id: 'd1', expected_version: 2 },
    ]);
  });
});
