import { expect, it } from 'vitest';
import { parseOpeningLines } from './opening-preparation';
const accounts = [{ id: 'a', code: '1002', current_version: 3, status: 'active' }];
it('期初输入绑定已读取的科目版本，保留金额精度', () => {
  expect(parseOpeningLines('1002,0.00,128.50,0.00,128.50', accounts)).toEqual([
    {
      account_id: 'a',
      account_version: 3,
      year_opening: '0.00',
      ytd_debit: '128.50',
      ytd_credit: '0.00',
      opening: '128.50',
    },
  ]);
});
it.each(['9999,0,0,0,0', '1002,0,0,0,NaN', '1002,0,0,0,1.001', '1002,0,0,0,0\n1002,0,0,0,0'])(
  '拒绝未知科目、坏金额、重复行：%s',
  (text) => {
    expect(() => parseOpeningLines(text, accounts)).toThrow();
  },
);
