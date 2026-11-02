import { useCallback, useEffect, useRef, useState } from 'react';
import { api, errorMessage } from './api';
import type { Session } from './types';
import { formatYuan, parseFilings, type TaxFiling } from './tax-filings';

const KINDS = [
  { value: 'vat', label: '增值税' },
  { value: 'surtax', label: '附加税费' },
];

export function FilingList({ rows }: { rows: TaxFiling[] }) {
  if (!rows.length) {
    return <p className="tax-filing-empty">尚未登记外部办理结果；登记前一律视为「未申报」。</p>;
  }
  return (
    <ul className="tax-filing-list">
      {rows.map((row) => (
        <li key={row.id || `${row.kind}-${row.external_ref}`} data-status={row.status}>
          <div className="tax-filing-head">
            <strong>
              {row.kind_label} · {row.status_label}
            </strong>
            <span>{row.occurred_at}</span>
          </div>
          <p className="tax-filing-meta">
            回执/流水号 {row.external_ref} · 金额 {formatYuan(row.amount_cents)} 元
          </p>
          {row.evidence_note && <p className="tax-filing-meta">{row.evidence_note}</p>}
        </li>
      ))}
    </ul>
  );
}

export function TaxFilingRecords({
  session,
  bookId,
  period,
  canRegister,
  onRecorded,
}: {
  session: Session;
  bookId: string;
  period: string;
  canRegister: boolean;
  onRecorded?: () => void;
}) {
  const [rows, setRows] = useState<TaxFiling[]>([]);
  const [kind, setKind] = useState('vat');
  const [status, setStatus] = useState<'filed' | 'paid'>('filed');
  const [reference, setReference] = useState('');
  const [occurredAt, setOccurredAt] = useState('');
  const [amount, setAmount] = useState('0.00');
  const [note, setNote] = useState('');
  const [failure, setFailure] = useState('');
  const [busy, setBusy] = useState(false);
  const live = useRef(true);

  const load = useCallback(async () => {
    try {
      const raw = await api<unknown>(
        `/tax/filings?book_id=${encodeURIComponent(bookId)}&period=${encodeURIComponent(period)}`,
        session,
      );
      if (live.current) setRows(parseFilings(raw));
    } catch {
      if (live.current) setRows([]);
    }
  }, [session, bookId, period]);

  useEffect(() => {
    live.current = true;
    void load();
    return () => {
      live.current = false;
    };
  }, [load]);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setFailure('');
    const cents = Math.round(Number(amount) * 100);
    try {
      await api('/tax/filings', session, {
        method: 'POST',
        body: {
          book_id: bookId,
          period,
          kind,
          status,
          external_ref: reference.trim(),
          occurred_at: occurredAt,
          amount_cents: Number.isFinite(cents) && cents > 0 ? cents : 0,
          evidence_note: note.trim(),
        },
      });
      if (live.current) {
        setReference('');
        setNote('');
        await load();
        onRecorded?.();
      }
    } catch (error) {
      if (live.current) setFailure(errorMessage(error));
    } finally {
      if (live.current) setBusy(false);
    }
  }

  return (
    <section className="tax-filings" aria-label="外部办理记录">
      <header>
        <h3>外部办理记录 · {period}</h3>
        <p className="tax-filing-hint">
          系统不会代客户申报或缴款；请人工登记回执，登记不改变账务与底稿复核状态。
        </p>
      </header>
      <FilingList rows={rows} />
      {canRegister ? (
        <form className="tax-filing-form" onSubmit={submit}>
          <label>
            税种
            <select value={kind} onChange={(event) => setKind(event.target.value)}>
              {KINDS.map((item) => (
                <option key={item.value} value={item.value}>
                  {item.label}
                </option>
              ))}
            </select>
          </label>
          <label>
            办理结果
            <select value={status} onChange={(event) => setStatus(event.target.value as 'filed' | 'paid')}>
              <option value="filed">已申报</option>
              <option value="paid">已缴款</option>
            </select>
          </label>
          <label>
            回执或流水号
            <input
              value={reference}
              maxLength={100}
              onChange={(event) => setReference(event.target.value)}
              required
            />
          </label>
          <label>
            办理日期
            <input type="date" value={occurredAt} onChange={(event) => setOccurredAt(event.target.value)} required />
          </label>
          <label>
            缴款金额（元）
            <input value={amount} inputMode="decimal" onChange={(event) => setAmount(event.target.value)} />
          </label>
          <label>
            证据说明
            <input value={note} maxLength={500} onChange={(event) => setNote(event.target.value)} />
          </label>
          <button className="button" disabled={busy}>
            {busy ? '登记中…' : '登记外部办理结果'}
          </button>
        </form>
      ) : (
        <p className="tax-filing-hint">登记由事务所复核岗位完成；你可以在下方查看已登记的回执。</p>
      )}
      {failure && (
        <p role="alert" className="tax-alert">
          {failure}
        </p>
      )}
    </section>
  );
}
