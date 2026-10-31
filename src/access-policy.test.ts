import { describe, expect, it } from 'vitest';
import { expenseSummary, staffTemplateAllowed } from './access-policy';

describe('岗位和部门汇总契约', () => {
  it('模板不能把只读成员提升为复核员', () => {
    expect(staffTemplateAllowed('viewer', 'reviewer')).toBe(false);
    expect(staffTemplateAllowed('reviewer', 'reviewer')).toBe(true);
    expect(staffTemplateAllowed('operator', 'auditor')).toBe(true);
  });
  it('拒绝错范围、NaN、小数分和字符串金额', () => {
    const scope = { company_id: 'a', department_id: 'd', period: '2026-10' };
    expect(expenseSummary({ ...scope, expense_cents: 123 }, scope)).toBe(123);
    for (const amount of [NaN, Infinity, 0.5, '123', null])
      expect(expenseSummary({ ...scope, expense_cents: amount }, scope)).toBeNull();
    expect(expenseSummary({ ...scope, company_id: 'b', expense_cents: 123 }, scope)).toBeNull();
  });
});
