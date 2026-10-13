import { describe, expect, it } from 'vitest';
import { caseCanClose, caseMissingFields, caseReasonError, caseStateLabels } from './case-types';
import type { CaseTask } from './case-types';

describe('异常与补件交互边界', () => {
  it('待补件和待核验不能直接关闭', () => {
    const row = { permissions: { manage: true }, state: 'awaiting_info' } as CaseTask;
    expect(caseCanClose(row)).toBe(false); expect(caseCanClose({ ...row, state: 'response_submitted' })).toBe(false);
    expect(caseCanClose({ ...row, state: 'in_review' })).toBe(true);
    expect(caseCanClose({ ...row, state: 'open', permissions: { manage: false, respond: false, upload: false } })).toBe(false);
  });
  it('必填补件字段不能空白', () => {
    expect(caseMissingFields({ required_fields: ['用途', '合同编号'] }, { 用途: ' ', 合同编号: 'HT-01' })).toEqual(['用途']);
    expect(caseReasonError(' ')).toBeTruthy(); expect(caseReasonError('已核实')).toBe('');
  });
  it('接受补件后仍提示核验后处理中', () => { expect(caseStateLabels.in_review).toBe('核验后处理中'); });
});
