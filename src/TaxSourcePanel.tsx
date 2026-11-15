import { useEffect, useRef, useState } from 'react';
import { api, ApiError } from './api';
import type { Session } from './types';
import { EvidenceViewer } from './EvidenceViewer';
import './tax-source.css';

type Row = {
  external_id: string; date: string; status: string; reason: string;
  description: string; amount_cents: number; quantity_milli: number; row_number: number;
};
type Source = {
  id: string; book_id: string; sha256: string; size_bytes: number; version: number; status: string; filename: string; source_system: string;
  external_id: string; sheet_name: string; rule_version: string; amount_total_cents: number;
  quantity_milli: number; fingerprint: string; rows: Row[]; replayed?: boolean;
};
const columns = {
  external_id: '行ID', date: '日期', description: '摘要',
  amount: '金额', quantity: '数量', target_id: '目标',
  driver_external_id: '驾驶员编号', vehicle_external_id: '车辆编号',
  discount: '优惠减免', adjustment: '调整', adjustment_reason: '调整原因',
};
function money(cents: number) {
  const value = Math.abs(cents) / 100;
  const text = value.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  return `${cents < 0 ? '−' : ''}¥${text}`;
}
async function base64(file: File) {
  const buffer = new Uint8Array(await file.arrayBuffer());
  let binary = '';
  for (let index = 0; index < buffer.length; index += 0x8000) {
    binary += String.fromCharCode(...buffer.subarray(index, index + 0x8000));
  }
  return btoa(binary);
}
export function TaxSourcePanel({ session, bookId }: { session: Session; bookId: string }) {
  return <ScopedTaxSourcePanel key={`${session.scope_key}:${session.user_id}:${bookId}`} session={session} bookId={bookId} />;
}
function ScopedTaxSourcePanel({ session, bookId }: { session: Session; bookId: string }) {
  const [items, setItems] = useState<Source[]>([]);
  const [notice, setNotice] = useState('');
  const [failure, setFailure] = useState('');
  const [busy, setBusy] = useState(false);
  const [selected, setSelected] = useState<Source | null>(null);
  const file = useRef<HTMLInputElement>(null);
  const [sourceSystem, setSourceSystem] = useState('客户运输台账');
  const [externalId, setExternalId] = useState('');
  const [version, setVersion] = useState('1');
  const [sheet, setSheet] = useState('运输明细');
  const url = `/tax/books/${encodeURIComponent(bookId)}/sources`;
  function checked(data: Source) {
    if (data.book_id !== bookId) throw new ApiError('返回的来源范围不一致，请刷新后核对。', 409);
    return data;
  }
  useEffect(() => {
    let live = true;
    api<{ items: Source[] }>(url, session)
      .then((data) => { if (live) { data.items.forEach(checked); setItems(data.items); } })
      .catch((error) => { if (live) { setItems([]); setSelected(null); setNotice(''); setFailure(error instanceof ApiError ? error.message : '来源台账读取失败，请刷新重试。'); } });
    return () => { live = false; };
  }, [url, busy, session]);
  async function upload(event: React.FormEvent) {
    event.preventDefault();
    const chosen = file.current?.files?.[0];
    if (!chosen) { setFailure('请选择运输台账文件。'); return; }
    setBusy(true); setFailure(''); setNotice('');
    try {
      const data = await api<Source>(url, session, {
        method: 'POST',
        body: {
          filename: chosen.name, content_base64: await base64(chosen),
          source_system: sourceSystem, external_id: externalId || chosen.name.replace(/\.xlsx?$/i, ''),
          version: Number(version) || 1, sheet_name: sheet,
          column_map: columns, rule_version: 'tax-source-v1',
        },
      });
      setSelected(checked(data));
      setNotice(data.replayed ? '该版本已导入，返回原记录，未重复写入。' : '来源文件已解析并保存为活动版本。');
      setItems((current) => [data, ...current.filter((item) => item.id !== data.id)]);
    } catch (error) {
      setFailure(error instanceof ApiError ? `${error.message}（${error.status}）` : '来源文件读取失败，请按差异核对后重试。');
    } finally {
      setBusy(false);
      if (file.current) file.current.value = '';
    }
  }
  async function reviewRow(row: Row, status: 'adopted' | 'excluded') {
    if (!selected) return;
    setBusy(true); setFailure(''); setNotice('');
    try {
      const data = await api<Source>(`${url}/${encodeURIComponent(selected.id)}/rows/${row.row_number}`, session, {
        method: 'PATCH',
        body: { status, reason: status === 'excluded' ? '人工核对后排除' : '人工核对后改为采用' },
      });
      setSelected(checked(data));
      setItems((current) => current.map((item) => item.id === data.id ? data : item));
      setNotice('来源行核定已保存，未冻结输出需要重新核对。');
    } catch (error) {
      setFailure(error instanceof ApiError ? `${error.message}（${error.status}）` : '来源行核定失败，请刷新后重试。');
    } finally {
      setBusy(false);
    }
  }
  const rows = selected?.rows ?? [];
  return (
    <section className="tax-source" aria-label="客户来源文件">
      <header>
        <div><h3>客户来源台账</h3><p>保留原文件 hash、页签、行号与规范值；不会执行公式或改写客户原文件。</p></div>
        <span className="tax-source-real">合成适配 · 非真实 T+</span>
      </header>
      <form onSubmit={upload}>
        <label>来源系统<input value={sourceSystem} onChange={(e) => setSourceSystem(e.target.value)} required /></label>
        <label>来源批次 ID<input value={externalId} onChange={(e) => setExternalId(e.target.value)} placeholder="留空用文件名" /></label>
        <label>版本<input type="number" min="1" value={version} onChange={(e) => setVersion(e.target.value)} required /></label>
        <label>页签<input value={sheet} onChange={(e) => setSheet(e.target.value)} required /></label>
        <label className="tax-source-file">运输台账（xlsx）
          <input ref={file} type="file" accept=".xlsx,.xls" required />
        </label>
        <button disabled={busy} type="submit">{busy ? '解析中…' : '解析并保存来源'}</button>
      </form>
      {notice && <p className="tax-source-notice" role="status">{notice}</p>}
      {failure && <p className="tax-source-error" role="alert">{failure}</p>}
      {selected && <details><summary>核对来源原件</summary><EvidenceViewer session={session}
        reference={`${selected.sheet_name} · 来源 v${selected.version}`} historical={selected.status !== 'active'}
        attachments={[{ id: selected.id, filename: selected.filename, sha256: selected.sha256,
          size_bytes: selected.size_bytes, media_type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
          contentPath: `${url}/${encodeURIComponent(selected.id)}/original` }]} /></details>}
      {items.length > 0 && (
        <div className="tax-source-list">
          <h4>已保存来源</h4>
          {items.slice(0, 5).map((item) => (
            <button key={item.id} type="button" className={selected?.id === item.id ? 'active' : ''} onClick={() => setSelected(item)}>
              <strong>{item.filename}</strong>
              <span>v{item.version} · {item.status} · {item.sheet_name} · {money(item.amount_total_cents)}</span>
            </button>
          ))}
        </div>
      )}
      {selected && (
        <div className="tax-source-result">
          <p>活动版本 v{selected.version}；已采用 {rows.filter((row) => row.status === 'adopted').length} 行、待核对 {rows.filter((row) => row.status === 'needs_review').length} 行、排除 {rows.filter((row) => row.status === 'excluded').length} 行；金额与数量分别汇总：{money(selected.amount_total_cents)} / {(selected.quantity_milli / 1000).toFixed(3)}。</p>
          <table>
            <thead><tr><th>行</th><th>外部 ID</th><th>日期</th><th>摘要</th><th>金额</th><th>数量</th><th>状态 / 原因</th><th>操作</th></tr></thead>
            <tbody>{rows.slice(0, 12).map((row) => (
              <tr key={`${row.external_id}-${row.row_number}`}>
                <td>{row.row_number}</td><td>{row.external_id || '—'}</td><td>{row.date || '—'}</td>
                <td>{row.description || '—'}</td><td>{money(row.amount_cents)}</td>
                <td>{(row.quantity_milli / 1000).toFixed(3)}</td>
                <td><span className={`tag ${row.status}`}>{row.status === 'adopted' ? '已采用' : row.status === 'needs_review' ? '待核对' : '已排除'}</span>{row.reason && <small>{row.reason}</small>}</td>
                <td><button type="button" disabled={busy} onClick={() => reviewRow(row, row.status === 'adopted' ? 'excluded' : 'adopted')}>{row.status === 'adopted' ? '排除' : '改为采用'}</button></td>
              </tr>
            ))}</tbody>
          </table>
          <small>指纹 {selected.fingerprint.slice(0, 16)}…；读取范围仅当前账套 {session.firm_id ? '与本账期' : ''}，不是客户系统直连。</small>
        </div>
      )}
    </section>
  );
}
