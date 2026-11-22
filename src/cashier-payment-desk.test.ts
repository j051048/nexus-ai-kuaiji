import { describe, expect, it } from 'vitest';
import { canApprove, canSubmit, orderStatus } from './CashierPaymentDesk';
import type { PaymentDesk } from './CashierPaymentDesk';

const order = {
  id: 'po-1',
  payee: '供应商甲',
  payee_account: '6222',
  amount_cents: 120_000,
  status: 'pending_approval',
  maker_user_id: 'local-reviewer',
  approver_user_id: null,
  escalated: 0,
  reason: '',
  created_at: '2026-10-10T00:00:00+00:00',
};

const desk = (pay: boolean, approve: boolean, roles: string[] = []): PaymentDesk => ({
  book_id: 'book-1',
  period: '2026-10',
  roles,
  permissions: { pay, approve_payment: approve },
  orders: [order],
  separation: '制单人与资金审批人必须是不同的人',
});

describe('cashier payment desk gates', () => {
  it("an approver can release someone else's pending order", () => {
    expect(canApprove(desk(false, true), order, 'local-accountant')).toBe(true);
  });

  it('the maker can never approve their own order, even with the approver duty', () => {
    expect(canApprove(desk(false, true), order, 'local-reviewer')).toBe(false);
  });

  it('a cashier without the approver duty cannot approve', () => {
    expect(canApprove(desk(true, false), order, 'local-accountant')).toBe(false);
    expect(canApprove(null, order, 'local-accountant')).toBe(false);
  });

  it('only the maker submits their own draft, and only with the cashier duty', () => {
    const draft = { ...order, status: 'draft' };
    expect(canSubmit(desk(true, false), draft, 'local-reviewer')).toBe(true);
    expect(canSubmit(desk(true, false), draft, 'local-accountant')).toBe(false);
    expect(canSubmit(desk(false, false), draft, 'local-reviewer')).toBe(false);
  });

  it('unknown status is shown verbatim rather than guessed', () => {
    expect(orderStatus({ ...order, status: 'frozen_by_bank' })).toBe('frozen_by_bank');
    expect(orderStatus(order)).toBe('待审批');
  });
});
