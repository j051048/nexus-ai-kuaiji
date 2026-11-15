import { useCallback, useEffect, useRef, useState } from 'react';
import { FileCheck2, RefreshCw, ShieldCheck } from 'lucide-react';
import { api, ApiError } from './api';
import type { Session } from './types';

type Report = { id: string; version: number; current: boolean };
type Evidence = {
  id: string;
  period: string;
  report_id: string;
  report_version: number;
  source_kind: 'tplus_report' | 'import_result';
  source_name: string;
  source_version: string;
  sha256: string;
  status: 'pending' | 'verified' | 'rejected';
  note: string;
  created_at: string;
  verified_by: string | null;
  verified_at: string | null;
  review_note: string | null;
  can_verify: boolean;
};

export function ExternalEvidencePanel({
  session,
  bookId,
  reports,
  onChanged,
}: {
  session: Session;
  bookId: string;
  reports: Report[];
  onChanged: () => Promise<void> | void;
}) {
  const base = `/books/${encodeURIComponent(bookId)}/delivery/external-evidence`;
  const current = reports.find((item) => item.current) ?? reports[0];
  const [items, setItems] = useState<Evidence[]>([]);
  const [externalMode, setExternalMode] = useState(false);
  const [reportId, setReportId] = useState(current?.id ?? '');
  const [kind, setKind] = useState<'tplus_report' | 'import_result'>('tplus_report');
  const [sourceName, setSourceName] = useState('合成 T+ 报表（本地）');
  const [sourceVersion, setSourceVersion] = useState('');
  const [content, setContent] = useState('');
  const [note, setNote] = useState('');
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const scope = `${session.scope_key}:${bookId}`;
  const active = useRef(scope);
  active.current = scope;

  const load = useCallback(async () => {
    try {
      const data = await api<{ items: Evidence[]; external_mode: boolean }>(base, session);
      if (active.current !== scope) return;
      setItems(data.items);
      setExternalMode(data.external_mode);
      setError('');
    } catch (failure) {
      if (active.current !== scope) return;
      setError(failure instanceof ApiError ? failure.message : '外部证据读取失败');
    }
  }, [base, scope, session]);

  useEffect(() => {
    void load();
  }, [load]);

  if (!externalMode) return null;

  const create = async () => {
    setBusy(true);
    setError('');
    setMessage('');
    try {
      await api(base, session, {
        method: 'POST',
        body: {
          report_id: reportId,
          expected_report_version: reports.find((item) => item.id === reportId)?.version ?? 0,
          source_kind: kind,
          source_name: sourceName,
          source_version: sourceVersion,
          content,
          note,
        },
      });
      setContent('');
      setSourceVersion('');
      setMessage('外部证据已登记，仍需另一名有复核权限的会计独立核验，才可用于交付。');
      await load();
      await onChanged();
    } catch (failure) {
      setError(failure instanceof ApiError ? failure.message : '外部证据登记失败，未写入');
    } finally {
      setBusy(false);
    }
  };

  const review = async (item: Evidence, decision: 'verified' | 'rejected') => {
    setBusy(true);
    setError('');
    setMessage('');
    try {
      await api(`${base}/${encodeURIComponent(item.id)}/review`, session, {
        method: 'POST',
        body: {
          decision,
          note: note.trim() || (decision === 'verified' ? '与客户 T+ 报表期间金额核对一致' : '证据与报表版本不一致'),
        },
      });
      setMessage(decision === 'verified' ? '已独立核验；仍不等于 T+ 已过账或客户已签收。' : '已拒绝该外部证据。');
      await load();
      await onChanged();
    } catch (failure) {
      setError(failure instanceof ApiError ? failure.message : '外部证据核验失败，未改写结论');
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="external-evidence" aria-label="外部账报表证据">
      <header>
        <div>
          <h3>
            <ShieldCheck size={18} /> T+ 外部证据核验
          </h3>
          <p>
            本账套已接入 T+ 法定账。上传只算登记；必须由另一名复核人核验同企业、同期间、同报表版本的原件，才允许冻结交付包。
          </p>
        </div>
        <button className="button ghost" type="button" disabled={busy} onClick={() => void load()}>
          <RefreshCw size={14} /> 刷新
        </button>
      </header>
      <div className="external-evidence-form">
        <label>
          对应报表版本
          <select value={reportId} disabled={busy} onChange={(event) => setReportId(event.target.value)}>
            {reports.map((item) => (
              <option value={item.id} disabled={!item.current} key={item.id}>
                报表 V{item.version}{item.current ? ' · 当前有效' : ' · 非当前'}
              </option>
            ))}
          </select>
        </label>
        <label>
          证据类型
          <select value={kind} disabled={busy} onChange={(event) => setKind(event.target.value as typeof kind)}>
            <option value="tplus_report">T+ 报表导出</option>
            <option value="import_result">T+ 导入结果</option>
          </select>
        </label>
        <label>
          来源名称
          <input value={sourceName} maxLength={200} disabled={busy} onChange={(event) => setSourceName(event.target.value)} />
        </label>
        <label>
          来源版本/批次
          <input value={sourceVersion} maxLength={100} disabled={busy} placeholder="如 2026-10-v1" onChange={(event) => setSourceVersion(event.target.value)} />
        </label>
        <label className="external-evidence-wide">
          证据正文（本地合成/脱敏；保存摘要，不执行内容）
          <textarea value={content} rows={3} disabled={busy} onChange={(event) => setContent(event.target.value)} />
        </label>
        <label className="external-evidence-wide">
          登记说明
          <input value={note} maxLength={500} disabled={busy} onChange={(event) => setNote(event.target.value)} />
        </label>
        <button
          className="button"
          type="button"
          disabled={busy || !reportId || !sourceName.trim() || !sourceVersion.trim() || !content.trim() || note.trim().length < 3}
          onClick={() => void create()}
        >
          <FileCheck2 size={14} /> 登记待核验证据
        </button>
      </div>
      {message && <p className="external-evidence-ok">{message}</p>}
      {error && <p className="external-evidence-error" role="alert">{error}</p>}
      <ul className="external-evidence-list">
        {items.map((item) => (
          <li key={item.id}>
            <div>
              <strong>{item.source_name}</strong>
              <small>
                {item.source_kind === 'tplus_report' ? 'T+ 报表' : '导入结果'} · V{item.report_version} · {item.source_version} · sha {item.sha256.slice(0, 12)}
              </small>
              <small>登记 {item.created_at} · 状态 {item.status}</small>
              {item.review_note && <small>核验说明：{item.review_note}</small>}
            </div>
            <div className="external-evidence-actions">
              <span className={`external-evidence-status is-${item.status}`}>{item.status}</span>
              {item.can_verify && (
                <>
                  <button className="button" type="button" disabled={busy} onClick={() => void review(item, 'verified')}>独立核验通过</button>
                  <button className="button ghost" type="button" disabled={busy} onClick={() => void review(item, 'rejected')}>拒绝</button>
                </>
              )}
            </div>
          </li>
        ))}
        {items.length === 0 && <li className="external-evidence-empty">尚无外部证据；当前交付包会被阻断。</li>}
      </ul>
    </section>
  );
}
