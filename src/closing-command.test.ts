import { describe, expect, it } from 'vitest';
import { closingRequestBody } from './closing-command';

describe('period-end closing command', () => {
  it('binds the selected profit account and stable period key without amounts', () => {
    const body = closingRequestBody('book-a', '2026-10', {
      id: 'acc-profit',
      version: 3,
      code: '3103',
      name: '本年利润',
    });
    expect(body).toEqual({
      book_id: 'book-a',
      period: '2026-10',
      profit_account_id: 'acc-profit',
      profit_account_version: 3,
      idempotency_key: 'closing-ui-book-a-2026-10',
      reason: 'UI 生成期末结转草稿',
    });
    expect(Object.keys(body)).not.toContain('amount_cents');
    expect(Object.keys(body)).not.toContain('balance');
  });
});
