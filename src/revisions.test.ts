import { describe, expect, it } from 'vitest';
import { revisionChanges, revisionSubmitCommand, type RevisionContext } from './revision-types';
import { canIsolateDuplicate, sameTransactionCommand } from './disposition-types';
import type { BusinessPayload } from './business-types';
import type { Draft } from './types';

const payload: BusinessPayload = {
  occurred_on: '2026-10-03',
  amount: '100.00',
  description: '合成办公费用',
  counterparty: '合成供应商',
  category: 'office',
  direction: 'expense',
  payment_status: 'unpaid',
  purpose: '',
  project: '',
};
describe('事实修订输入与重复隔离边界', () => {
  it('明确显示金额和日期修订，不将金额转换为浮点数', () => {
    expect(
      revisionChanges(payload, { ...payload, amount: '80.01', occurred_on: '2026-11-03' }).map(
        (item) => item.key,
      ),
    ).toEqual(['occurred_on', 'amount']);
  });
  it('修订携带事实、事项与草稿版本以及稳定操作编号', () => {
    const context = { event_version: 7, fact_version: 3, draft_version: 2 } as RevisionContext;
    expect(
      revisionSubmitCommand(
        context,
        { ...payload, amount: '80.01' },
        'target',
        ' 核对原件金额 ',
        'stable-command',
      ),
    ).toEqual({
      expected_event_version: 7,
      expected_fact_version: 3,
      expected_draft_version: 2,
      target_book_id: 'target',
      payload: { ...payload, amount: '80.01' },
      reason: '核对原件金额',
      idempotency_key: 'stable-command',
    });
  });
  it('禁止前端把已审记录作为隔离副本', () => {
    expect(
      canIsolateDuplicate(
        { status: 'approved' } as Draft,
        { can_isolate: true, inactive: false } as never,
      ),
    ).toBe(false);
    expect(
      canIsolateDuplicate(
        { status: 'draft' } as Draft,
        { can_isolate: false, inactive: false } as never,
      ),
    ).toBe(false);
    expect(
      sameTransactionCommand(
        3,
        'a'.repeat(64),
        'b'.repeat(64),
        ' 两张原件对应同一交易 ',
        'stable-key',
      ),
    ).toMatchObject({
      expected_version: 3,
      reason: '两张原件对应同一交易',
      idempotency_key: 'stable-key',
    });
  });
});
