import { useCallback, useEffect, useState } from 'react';
import { api, errorMessage } from './api';
import type { Session } from './types';

type Order = {
  id: string;
  payee: string;
  payee_account: string;
  amount_cents: number;
  status: string;
  maker_user_id: string;
  approver_user_id: string | null;
  escalated: number;
  reason: string;
  created_at: string;
};

export interface PaymentDesk {
  book_id: string;
  period: string;
  roles: string[];
  permissions: { pay: boolean; approve_payment: boolean };
  orders: Order[];
  separation: string;
}

const STATUS: Record<string, string> = {
  draft: '草稿',
  pending_approval: '待审批',
  approved: '已批准',
  queued_for_bank: '已送银行',
  submitted: '银行处理中',
  succeeded: '已成功',
  failed: '失败',
  bounced: '退票',
  bank_unknown: '银行结果未知',
};

const yuan = (cents: number) => (cents / 100).toLocaleString('zh-CN', { minimumFractionDigits: 2 });

export const orderStatus = (order: Order) => STATUS[order.status] ?? order.status;

/** 审批按钮的唯一判据：有放款能力、不是制单人、且当前状态确实等审批。 */
export function canApprove(desk: PaymentDesk | null, order: Order, userId: string): boolean {
  if (!desk || !desk.permissions.approve_payment) return false;
  if (order.status !== 'pending_approval') return false;
  return order.maker_user_id !== userId;
}

/** 制单/提交同理：有付钱能力，且这笔单还是自己的草稿。 */
export function canSubmit(desk: PaymentDesk | null, order: Order, userId: string): boolean {
  if (!desk || !desk.permissions.pay) return false;
  if (order.status !== 'draft') return false;
  return order.maker_user_id === userId;
}

export function CashierPaymentDesk({ session, bookId }: { session: Session; bookId: string }) {
  const [desk, setDesk] = useState<PaymentDesk | null>(null);
  const [failure, setFailure] = useState('');
  const [notice, setNotice] = useState('');
  const [busy, setBusy] = useState(false);
  const [payee, setPayee] = useState('');
  const [account, setAccount] = useState('');
  const [amount, setAmount] = useState('');

  const load = useCallback(() => {
    api<PaymentDesk>(`/treasury/payment-desk?book_id=${encodeURIComponent(bookId)}`, session)
      .then((raw) => {
        setDesk(raw);
        setFailure('');
      })
      .catch((error) => {
        setDesk(null);
        setFailure(errorMessage(error));
      });
  }, [session, bookId]);

  useEffect(() => {
    load();
  }, [load]);

  async function act(path: string, body: Record<string, unknown>, ok: string) {
    setBusy(true);
    setFailure('');
    setNotice('');
    try {
      await api(path, session, { method: 'POST', body });
      setNotice(ok);
      load();
    } catch (error) {
      setFailure(errorMessage(error));
    } finally {
      setBusy(false);
    }
  }

  async function create() {
    const cents = Number(amount.replace(/,/g, ''));
    if (!payee.trim() || !account.trim() || !Number.isFinite(cents) || cents <= 0) {
      setFailure('请填写收款人、账号与大于 0 的金额（元）。');
      return;
    }
    const idempotency = `${bookId}:${payee.trim()}:${account.trim()}:${Math.round(cents)}`;
    setBusy(true);
    setFailure('');
    setNotice('');
    try {
      const created = await api<Order>('/treasury/payment-orders', session, {
        method: 'POST',
        body: {
          book_id: bookId,
          period: desk?.period ?? '',
          payee: payee.trim(),
          payee_account: account.trim(),
          amount_cents: Math.round(cents),
          idempotency_key: idempotency,
        },
      });
      setNotice(`付款指令已创建（${created.status === 'draft' ? '草稿' : created.status}）。`);
      setPayee('');
      setAccount('');
      setAmount('');
      load();
    } catch (error) {
      setFailure(errorMessage(error));
    } finally {
      setBusy(false);
    }
  }

  const userId = session.user_id;
  return (
    <section aria-label="出纳付款审批台" aria-busy={busy}>
      <header>
        <h3>出纳付款审批台</h3>
        <button type="button" className="button secondary" onClick={load} disabled={busy}>
          刷新
        </button>
      </header>
      <p>
        <strong>钱账分离内控护栏：</strong>
        共享出纳专管待打款池、网银流水导入与回单核销（不开放修改总账凭证科目入口）；出纳制单与资金放款审批必须由不同岗位人员独立完成，大额打款自动触发升级审批。
      </p>
      {failure ? <p role="alert">{failure}</p> : null}
      {notice ? <p role="status">{notice}</p> : null}
      {desk ? (
        <>
          <p>
            我的岗位：{desk.roles.length ? desk.roles.join('、') : '无（仅按账套权限）'} · 制单
            {desk.permissions.pay ? '可用' : '不可用'} · 放款审批
            {desk.permissions.approve_payment ? '可用' : '不可用'}
          </p>
          {desk.permissions.pay ? (
            <div>
              <label>
                收款人
                <input value={payee} onChange={(event) => setPayee(event.target.value)} />
              </label>
              <label>
                收款账号
                <input value={account} onChange={(event) => setAccount(event.target.value)} />
              </label>
              <label>
                金额（元）
                <input
                  inputMode="decimal"
                  value={amount}
                  onChange={(event) => setAmount(event.target.value)}
                />
              </label>
              <button type="button" className="button secondary" disabled={busy} onClick={create}>
                创建付款指令
              </button>
            </div>
          ) : null}
          {desk.orders.length === 0 ? (
            <p>本期还没有付款指令。</p>
          ) : (
            <table>
              <caption>共 {desk.orders.length} 笔付款指令</caption>
              <thead>
                <tr>
                  <th scope="col">收款人</th>
                  <th scope="col">金额</th>
                  <th scope="col">状态</th>
                  <th scope="col">操作</th>
                </tr>
              </thead>
              <tbody>
                {desk.orders.map((order) => (
                  <tr key={order.id}>
                    <td>{order.payee}</td>
                    <td>{yuan(order.amount_cents)}</td>
                    <td>
                      {orderStatus(order)}
                      {order.escalated ? ' · 需二级审批' : ''}
                    </td>
                    <td>
                      {canSubmit(desk, order, userId) ? (
                        <button
                          type="button"
                          className="button secondary"
                          disabled={busy}
                          onClick={() =>
                            act(
                              `/treasury/payment-orders/${order.id}/submit`,
                              {},
                              '已提交，等待资金审批人放款。',
                            )
                          }
                        >
                          提交审批
                        </button>
                      ) : null}
                      {canApprove(desk, order, userId) ? (
                        <button
                          type="button"
                          className="button secondary"
                          disabled={busy}
                          onClick={() =>
                            act(
                              `/treasury/payment-orders/${order.id}/approve`,
                              {},
                              '已批准放款；制单人与审批人不是同一人。',
                            )
                          }
                        >
                          批准放款
                        </button>
                      ) : null}
                      {order.status === 'pending_approval' &&
                      desk.permissions.approve_payment &&
                      order.maker_user_id === userId ? (
                        <span>本人制单，须由他人审批</span>
                      ) : null}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </>
      ) : null}
    </section>
  );
}
