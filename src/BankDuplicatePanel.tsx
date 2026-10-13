import { useEffect, useRef, useState } from 'react';
import { Archive, FileSearch, LoaderCircle, ShieldCheck, TriangleAlert } from 'lucide-react';
import { api, errorMessage } from './api';
import { formatMoney } from './csv';
import type { Draft, Session } from './types';
import { canIsolateDuplicate, sameTransactionCommand, type DispositionOverview } from './disposition-types';
import './bank-duplicates.css';

type Check = {
  source_id: string; fingerprint: string; total: number; has_more: boolean;
  identity: { namespace: string; bank_account: string; stable_transaction_id: string; original_external_id: string } | null;
  permissions: { review: boolean };
  candidates: { id: string; external_id: string; stable_transaction_id: string; date: string; description: string; amount: string; fingerprint: string; status: string; distinguished: boolean }[];
};

export function BankDuplicatePanel({ session, bookId, draft, disabled = false, onBusyChange, onChanged }: {
  session: Session; bookId: string; draft: Draft; disabled?: boolean; onBusyChange?: (value: boolean) => void; onChanged: () => void;
}) {
  const [value, setValue] = useState<Check | null>(null); const [error, setError] = useState('');
  const [disposition, setDisposition] = useState<DispositionOverview | null>(null);
  const [reason, setReason] = useState(''); const [busy, setBusy] = useState(false);
  const active = useRef(true); const lock = useRef(false);
  const isolationCommand = useRef<{ identity: string; key: string } | null>(null);
  const scope = `${session.scope_key}:${bookId}:${draft.id}:${draft.version}`; const current = useRef(scope); current.current = scope;
  useEffect(() => { active.current = true; return () => { active.current = false; }; }, []);
  useEffect(() => { onBusyChange?.(busy); return () => onBusyChange?.(false); }, [busy, onBusyChange]);
  useEffect(() => {
    const controller = new AbortController(); setValue(null); setDisposition(null); setReason(''); setError(''); isolationCommand.current = null;
    const path = `/books/${encodeURIComponent(bookId)}/sources/${encodeURIComponent(draft.source_id)}`;
    void Promise.all([api<Check>(`${path}/duplicate-check`, session, { signal: controller.signal }),
      api<DispositionOverview>(`${path}/disposition`, session, { signal: controller.signal })])
      .then(([result, state]) => { if (!controller.signal.aborted) { setValue(result); setDisposition(state); } })
      .catch(failure => { if (!controller.signal.aborted) setError(errorMessage(failure)); });
    return () => controller.abort();
  }, [session, bookId, draft.source_id, draft.version]);
  async function distinguish(other: Check['candidates'][number]) {
    if (!value || disabled || lock.current || reason.trim().length < 3) return;
    const started = scope; lock.current = true; setBusy(true); setError('');
    try {
      await api(`/books/${encodeURIComponent(bookId)}/sources/${encodeURIComponent(draft.source_id)}/duplicates/${encodeURIComponent(other.id)}/distinguish`, session,
        { method: 'POST', body: { expected_version: draft.version, source_fingerprint: value.fingerprint, other_fingerprint: other.fingerprint, reason: reason.trim() } });
      if (active.current && current.current === started) onChanged();
    } catch (failure) { if (active.current && current.current === started) setError(errorMessage(failure)); }
    finally { lock.current = false; if (active.current) setBusy(false); }
  }
  async function isolate(other: Check['candidates'][number]) {
    if (!value || !canIsolateDuplicate(draft, disposition) || other.distinguished || disabled || lock.current || reason.trim().length < 3) return;
    const started = scope; lock.current = true; setBusy(true); setError('');
    try {
      const identity = JSON.stringify([scope, value.fingerprint, other.id, other.fingerprint, reason.trim()]);
      if (isolationCommand.current?.identity !== identity) isolationCommand.current = { identity, key: crypto.randomUUID() };
      await api(`/books/${encodeURIComponent(bookId)}/sources/${encodeURIComponent(draft.source_id)}/duplicates/${encodeURIComponent(other.id)}/same-transaction`, session,
        { method: 'POST', body: sameTransactionCommand(draft.version, value.fingerprint, other.fingerprint, reason, isolationCommand.current.key) });
      if (active.current && current.current === started) onChanged();
    } catch (failure) { if (active.current && current.current === started) setError(errorMessage(failure)); }
    finally { lock.current = false; if (active.current) setBusy(false); }
  }
  if (!value?.identity && !value?.total && !error) return null;
  return <section className="bank-duplicate-panel" aria-label="银行交易身份与重复核验">
    <div className="bank-duplicate-heading"><FileSearch size={16} /><strong>银行交易身份与重复核验</strong></div>
    {value?.identity && <p className="field-help">{value.identity.namespace} · {value.identity.bank_account} · 交易编号 {value.identity.stable_transaction_id}</p>}
    {!value?.identity && !!value?.total && <p className="field-help">当前为旧格式来源，账户身份尚未登记；请与带账户标识的相似流水核对。</p>}
    {disposition?.inactive && <p className="bank-duplicate-warning"><Archive size={15} />当前来源已核验为重复副本并隔离。原件与历史记录保留，不能再生成、关联、审核或导出。依据：{disposition.disposition?.reason}</p>}
    {!disposition?.inactive && value?.total ? <><p className="bank-duplicate-warning"><TriangleAlert size={15} />发现 {value.total} 条日期、金额、方向及交易对方相同的流水。核对银行交易编号、账户与原件后，选择独立交易或保留主来源并隔离当前副本。</p>
      <div className="bank-duplicate-candidates">{value.candidates.map(other => <article key={other.id}><div><strong>{other.description}</strong><span>{other.date} · {formatMoney(other.amount)} 元 · {other.stable_transaction_id}</span></div>
        {other.distinguished ? <span className="bank-distinguished"><ShieldCheck size={14} />已核验为独立交易</span> : value.permissions.review && draft.status === 'draft' && <div className="bank-duplicate-actions">
          <button className="button secondary small" disabled={disabled || busy || reason.trim().length < 3} onClick={() => void distinguish(other)}>{busy ? <LoaderCircle size={14} className="spinner" /> : <ShieldCheck size={14} />}确认两笔独立交易</button>
          {canIsolateDuplicate(draft, disposition) && <button className="button secondary small bank-isolate-button" disabled={disabled || busy || reason.trim().length < 3} onClick={() => void isolate(other)}>{busy ? <LoaderCircle size={14} className="spinner" /> : <Archive size={14} />}同一交易：保留此来源，隔离当前副本</button>}
        </div>}
      </article>)}</div>
      {value.permissions.review && draft.status === 'draft' && value.candidates.some(other => !other.distinguished) && <label><span className="field-label">交易核验依据</span><textarea aria-label="区分交易的核验依据" rows={2} maxLength={1000} value={reason} disabled={disabled || busy} onChange={event => setReason(event.target.value)} placeholder="填写银行原始交易编号、回执与业务凭据的核对结果" /></label>}
      {canIsolateDuplicate(draft, disposition) && <p className="field-help">确认同一交易后，上方选中的来源保持原状，当前未审核副本退出待办。已关联或已审核的来源不能作为副本隔离；历史核验决定不可覆盖。</p>}
      {value.has_more && <p className="field-help">每次显示最多 50 条，核验后刷新继续读取；未核验项仍阻止审核。</p>}
    </> : !disposition?.inactive && value?.identity && <p className="field-help">当前未发现同账号的相似流水；仍须核对业务实质和原件。</p>}
    {error && <p className="inline-error" role="alert">{error}</p>}
  </section>;
}
