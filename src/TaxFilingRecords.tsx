import { useCallback, useEffect, useRef, useState } from 'react';
import { api, apiBlob, errorMessage } from './api';
import { prepareUploadIntent } from './upload-client';
import type { Session } from './types';
import { formatYuan, parseFilings, type TaxFiling } from './tax-filings';

const KINDS = [
  { value: 'vat', label: '增值税' },
  { value: 'surtax', label: '附加税费' },
];

const ENTRY_LABELS = { registration: '首次登记', correction: '更正', void: '作废' } as const;

export function FilingList({
  rows,
  session,
  onCorrect,
}: {
  rows: TaxFiling[];
  session: Session;
  onCorrect?: (row: TaxFiling, kind: 'correction' | 'void') => void;
}) {
  const [failure, setFailure] = useState('');
  if (!rows.length) {
    return <p className="tax-filing-empty">尚未登记外部办理结果；登记前一律视为「未申报」。</p>;
  }
  async function download(row: TaxFiling) {
    if (!row.attachment) return;
    setFailure('');
    try {
      const blob = await apiBlob(
        `/tax/filings/${encodeURIComponent(row.id)}/attachments/${encodeURIComponent(row.attachment.id)}`,
        session,
      );
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = row.attachment.filename;
      link.click();
      URL.revokeObjectURL(url);
    } catch (error) {
      setFailure(errorMessage(error));
    }
  }
  return (
    <>
      <ul className="tax-filing-list">
        {rows.map((row) => (
          <li key={row.id || `${row.kind}-${row.external_ref}`} data-status={row.status}>
            <div className="tax-filing-head">
              <strong>
                {row.kind_label} · {row.status_label} · {ENTRY_LABELS[row.entry_kind]}
                {row.current ? ' · 当前' : ' · 历史'}
              </strong>
              <span>{row.occurred_at}</span>
            </div>
            <p className="tax-filing-meta">
              回执/流水号 {row.external_ref} · 金额 {formatYuan(row.amount_cents)} 元
            </p>
            <p className="tax-filing-meta">
              底稿版本 {row.workpaper_version ? `v${row.workpaper_version}` : '无'} · 金额核对{' '}
              {row.amount_check === 'match'
                ? '一致'
                : row.amount_check === 'mismatch'
                  ? '不一致'
                  : '无底稿'}
            </p>
            {row.correction_reason && (
              <p className="tax-filing-meta">原因：{row.correction_reason}</p>
            )}
            {row.evidence_note && <p className="tax-filing-meta">{row.evidence_note}</p>}
            {row.attachment ? (
              <p className="tax-filing-meta">
                <button type="button" className="link-button" onClick={() => void download(row)}>
                  下载原件 {row.attachment.filename}
                </button>{' '}
                <span title={row.attachment.sha256}>
                  SHA-256 {row.attachment.sha256.slice(0, 12)}…
                </span>
              </p>
            ) : (
              <p className="tax-filing-meta">未绑定回执原件</p>
            )}
            {row.current && onCorrect && (
              <p className="tax-filing-meta">
                <button
                  type="button"
                  className="link-button"
                  onClick={() => onCorrect(row, 'correction')}
                >
                  更正此回执
                </button>{' '}
                <button
                  type="button"
                  className="link-button"
                  onClick={() => onCorrect(row, 'void')}
                >
                  作废此回执
                </button>
              </p>
            )}
          </li>
        ))}
      </ul>
      {failure && (
        <p role="alert" className="tax-alert">
          {failure}
        </p>
      )}
    </>
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
  const [entryKind, setEntryKind] = useState<'registration' | 'correction' | 'void'>(
    'registration',
  );
  const [supersedes, setSupersedes] = useState<TaxFiling | null>(null);
  const [reason, setReason] = useState('');
  const [file, setFile] = useState<File | null>(null);
  const [failure, setFailure] = useState('');
  const [busy, setBusy] = useState(false);
  const live = useRef(true);
  const formRef = useRef<HTMLFormElement | null>(null);

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

  function startCorrection(row: TaxFiling, next: 'correction' | 'void') {
    setEntryKind(next);
    setSupersedes(row);
    setKind(row.kind);
    setStatus(row.status);
    setReason('');
    setReference('');
    setFile(null);
    setFailure('');
    formRef.current?.scrollIntoView({ block: 'start' });
  }

  function resetCorrection() {
    setEntryKind('registration');
    setSupersedes(null);
    setReason('');
  }

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setFailure('');
    const cents = Math.round(Number(amount) * 100);
    try {
      const created = await api<{ id: string }>('/tax/filings', session, {
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
          entry_kind: entryKind,
          supersedes_id: supersedes?.id ?? null,
          correction_reason: reason.trim(),
        },
      });
      if (entryKind === 'registration' && file && created.id) {
        const uploadId = await prepareUploadIntent(session, file, 'tax_filing_attachment');
        await api(`/tax/filings/${encodeURIComponent(created.id)}/attachments`, session, {
          method: 'POST',
          body: { upload_id: uploadId },
        });
      }
      if (live.current) {
        setReference('');
        setNote('');
        setFile(null);
        resetCorrection();
        await load();
        onRecorded?.();
      }
    } catch (error) {
      if (live.current) setFailure(errorMessage(error));
      await load();
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
      <FilingList
        rows={rows}
        session={session}
        onCorrect={canRegister ? startCorrection : undefined}
      />
      {canRegister ? (
        <form className="tax-filing-form" onSubmit={submit} ref={formRef}>
          {entryKind !== 'registration' && (
            <p className="tax-filing-hint">
              正在{ENTRY_LABELS[entryKind]}回执 {supersedes?.external_ref}
              ；原记录仍保留，不会被覆盖。
              <button type="button" className="link-button" onClick={resetCorrection}>
                取消
              </button>
            </p>
          )}
          <label>
            税种
            <select
              value={kind}
              onChange={(event) => setKind(event.target.value)}
              disabled={entryKind !== 'registration'}
            >
              {KINDS.map((item) => (
                <option key={item.value} value={item.value}>
                  {item.label}
                </option>
              ))}
            </select>
          </label>
          <label>
            办理结果
            <select
              value={status}
              onChange={(event) => setStatus(event.target.value as 'filed' | 'paid')}
              disabled={entryKind === 'void'}
            >
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
            <input
              type="date"
              value={occurredAt}
              onChange={(event) => setOccurredAt(event.target.value)}
              required
            />
          </label>
          <label>
            缴款金额（元）
            <input
              value={amount}
              inputMode="decimal"
              onChange={(event) => setAmount(event.target.value)}
            />
          </label>
          <label>
            证据说明
            <input value={note} maxLength={500} onChange={(event) => setNote(event.target.value)} />
          </label>
          {entryKind === 'registration' ? (
            <label>
              回执原件（PDF/图片，可选）
              <input
                type="file"
                accept="application/pdf,image/png,image/jpeg"
                onChange={(event) => setFile(event.target.files?.[0] ?? null)}
              />
            </label>
          ) : (
            <label>
              {ENTRY_LABELS[entryKind]}原因（必填，3–1000 字）
              <textarea
                value={reason}
                minLength={3}
                maxLength={1000}
                onChange={(event) => setReason(event.target.value)}
                required
              />
            </label>
          )}
          <button className="button" disabled={busy}>
            {busy ? '提交中…' : `登记${ENTRY_LABELS[entryKind]}结果`}
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
