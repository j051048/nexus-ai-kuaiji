import { useCallback, useEffect, useState } from 'react';
import { api, errorMessage } from './api';
import type { Session } from './types';

interface Entry {
  account: string;
  label: string;
  debit_cents: number;
  credit_cents: number;
}

export interface AccrualPreview {
  entries: Entry[];
  debit_cents: number;
  credit_cents: number;
  posted: false;
  note: string;
}

const yuan = (cents: number) => (cents / 100).toLocaleString('zh-CN', { minimumFractionDigits: 2 });

export const previewTotals = (data: AccrualPreview) =>
  `借方 ${yuan(data.debit_cents)} · 贷方 ${yuan(data.credit_cents)}`;

export function AccrualPreviewView({
  data,
  blocked,
}: {
  data: AccrualPreview | null;
  blocked: string;
}) {
  if (blocked)
    return (
      <p className="tax-accrual-blocked" role="note">
        {blocked}
      </p>
    );
  if (!data || data.entries.length === 0)
    return <p className="tax-accrual-empty">本期没有可计提的税金，或底稿尚未复核。</p>;
  return (
    <div className="tax-accrual-preview">
      <p className="tax-accrual-totals">{previewTotals(data)}</p>
      <table>
        <thead>
          <tr>
            <th scope="col">科目</th>
            <th scope="col">摘要</th>
            <th scope="col">借方</th>
            <th scope="col">贷方</th>
          </tr>
        </thead>
        <tbody>
          {data.entries.map((entry) => (
            <tr key={`${entry.account}-${entry.label}`}>
              <td>{entry.account}</td>
              <td>{entry.label}</td>
              <td>{entry.debit_cents ? yuan(entry.debit_cents) : ''}</td>
              <td>{entry.credit_cents ? yuan(entry.credit_cents) : ''}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="tax-accrual-note">{data.note}</p>
      <p className="tax-accrual-note">
        预览不写账：计提凭证仍须走「建议 → 复核 → 过账」，制单人与复核人必须是不同的人。
      </p>
    </div>
  );
}

export function TaxAccrualPanel({
  session,
  bookId,
  period,
}: {
  session: Session;
  bookId: string;
  period: string;
}) {
  const [data, setData] = useState<AccrualPreview | null>(null);
  const [blocked, setBlocked] = useState('');
  const [loading, setLoading] = useState(true);

  const load = useCallback(
    (signal?: AbortSignal) => {
      const body = { book_id: bookId, period, surtax_items: [] };
      api<AccrualPreview>('/tax/accrual-preview', session, {
        method: 'POST',
        body,
        signal,
      })
        .then((raw) => {
          setData(raw);
          setBlocked('');
        })
        .catch((error) => {
          // A missing reviewed workpaper is a normal state, not a failure:
          // show the server's own explanation instead of inventing numbers.
          setData(null);
          setBlocked(errorMessage(error));
        })
        .finally(() => setLoading(false));
    },
    [session, bookId, period],
  );

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    load(controller.signal);
    return () => controller.abort();
  }, [load]);

  return (
    <section className="tax-accrual" aria-label="四税计提预览" aria-busy={loading}>
      <header>
        <h3>四税计提预览</h3>
        <button className="button secondary" disabled={loading} onClick={() => load()}>
          重新计算预览
        </button>
      </header>
      <p className="tax-boundary">
        只按已复核的增值税底稿派生附加税、所得税与印花税；金额为整数分，不在这里过账。
      </p>
      <AccrualPreviewView data={data} blocked={blocked} />
    </section>
  );
}
