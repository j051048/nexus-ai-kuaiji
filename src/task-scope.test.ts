import { describe, expect, it } from 'vitest';
import { taskScope } from './task-scope';

const task = {
  company_id: 'company-b',
  company_name: '乙企业',
  period: '2026-10',
};

describe('taskScope', () => {
  it('uses the task company and period as the target scope', () => {
    expect(taskScope(task, { companyId: 'company-b', period: '2026-10' })).toEqual({
      companyId: 'company-b',
      period: '2026-10',
      switched: false,
      notice: '',
    });
  });

  it('announces a company or period switch with the exact target', () => {
    expect(taskScope(task, { companyId: 'company-a', period: '2026-09' })).toEqual({
      companyId: 'company-b',
      period: '2026-10',
      switched: true,
      notice: '已切换到 乙企业 · 2026-10',
    });
  });

  it('does not invent a period or a switch when the task has none', () => {
    expect(
      taskScope(
        { company_id: 'company-a', company_name: '甲企业', period: null },
        { companyId: 'company-a', period: '2026-10' },
      ),
    ).toEqual({
      companyId: 'company-a',
      period: '',
      switched: false,
      notice: '',
    });
  });
});
