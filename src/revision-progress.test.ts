import { describe, expect, it } from 'vitest';
import { revisionIsDirty } from './revision-progress';
import type { BusinessPayload } from './business-types';

const original: BusinessPayload = {
  occurred_on: '2026-10-24',
  amount: '120.00',
  description: '虚构差旅事项',
  counterparty: '虚构供方',
  direction: 'expense',
  payment_status: 'paid',
  category: 'travel',
};
const baseline = {
  context: { payload: original, mapping: { book_id: 'book-a', draft_id: 'draft-a' } },
  payload: { ...original },
  targetBook: 'book-a',
  reason: '',
  reviewReason: '',
};

describe('V06 修订未提交进度', () => {
  it('原事项刚打开、未编辑时可以直接返回', () => {
    expect(revisionIsDirty(baseline)).toBe(false);
  });
  it.each([
    { ...original, amount: '121.00' },
    { ...original, occurred_on: '2026-11-01' },
    { ...original, counterparty: '另一供方' },
    { ...original, description: '补充后的业务用途' },
  ])('业务事实编辑后必须保护未提交内容', (payload) => {
    expect(revisionIsDirty({ ...baseline, payload })).toBe(true);
  });
  it('只修改目标账套也不能静默离开', () => {
    expect(revisionIsDirty({ ...baseline, targetBook: 'book-next' })).toBe(true);
  });
  it.each(['reason', 'reviewReason'] as const)('只填写%s同样是未提交工作', (field) => {
    expect(revisionIsDirty({ ...baseline, [field]: '本地测试：核对原件后的说明' })).toBe(true);
  });
  it('只有空白说明不产生虚假未提交状态', () => {
    expect(revisionIsDirty({ ...baseline, reason: '  ', reviewReason: '\n' })).toBe(false);
  });
  it('没有读取到原事实时不根据空目标账套制造脏状态', () => {
    expect(revisionIsDirty({ ...baseline, context: null, payload: null, targetBook: '' })).toBe(
      false,
    );
  });
});
