import { useCallback, useEffect, useRef, useState } from 'react';
import { ArrowLeft, Download, FileUp, Link2, LoaderCircle } from 'lucide-react';
import { api, errorMessage } from './api';
import type { Session } from './types';
import { uploadSizeError } from './transport-limits';
import './integration.css';

type Mapping = { local_code: string; external_code: string };
type Config = { version: number; external_book_id: string; product_version: string; deployment: 'unknown' | 'on_premise' | 'tplus_cloud'; accounts: Mapping[]; auxiliaries: { kind: string; local_value: string; external_code: string }[] };
type Preview = { id: string; fingerprint: string; state: string; record_count: number; duplicates: number; conflicts: number; records: { kind: string; external_id: string; name: string; period: string }[] };
type Overview = { configuration: Config | null; can_configure: boolean; can_review: boolean; period: string; pagination: { has_more: boolean; total: number }; imports: { id: string; state: string; record_count: number; created_at: string }[]; outbox: { id: string; export_id: string; state: string; external_code: string }[] };
type Intent = { id: string; package: unknown };

function download(value: unknown, filename: string) {
  const url = URL.createObjectURL(new Blob([JSON.stringify(value, null, 2)], { type: 'application/json;charset=utf-8' }));
  const a = document.createElement('a'); a.href = url; a.download = filename; a.click(); setTimeout(() => URL.revokeObjectURL(url), 0);
}

export function IntegrationWorkbench({ session, bookId, onBack, onBusyChange }: { session: Session; bookId: string; onBack: () => void; onBusyChange?: (busy: boolean) => void }) {
  const base = `/books/${encodeURIComponent(bookId)}/integrations/tplus`;
  const [data, setData] = useState<Overview | null>(null), [preview, setPreview] = useState<Preview | null>(null);
  const [exports, setExports] = useState<{ id: string; filename: string }[]>([]);
  const [externalBook, setExternalBook] = useState(''), [version, setVersion] = useState('待确认'), [deployment, setDeployment] = useState<Config['deployment']>('unknown');
  const [mappings, setMappings] = useState(''), [auxiliaries, setAuxiliaries] = useState(''), [reason, setReason] = useState(''), [exportId, setExportId] = useState('');
  const [file, setFile] = useState<File | null>(null), [busy, setBusy] = useState(false), [error, setError] = useState(''), [message, setMessage] = useState(''), [offset, setOffset] = useState(0);
  const live = useRef(true), lock = useRef(false), epoch = useRef(0), keys = useRef<Record<string, string>>({});
  useEffect(() => { live.current = true; return () => { live.current = false; epoch.current++; }; }, []);
  useEffect(() => { onBusyChange?.(busy); return () => onBusyChange?.(false); }, [busy, onBusyChange]);
  const load = useCallback(async () => {
    const id = ++epoch.current;
    try {
      const [overview, list] = await Promise.all([api<Overview>(`${base}?limit=20&offset=${offset}`, session), api<{ exports: { id: string; filename: string }[] }>(`/books/${encodeURIComponent(bookId)}/exports`, session)]);
      if (!live.current || id !== epoch.current) return;
      setData(overview); setExports(list.exports);
      const c = overview.configuration;
      if (c) { setExternalBook(c.external_book_id); setVersion(c.product_version); setDeployment(c.deployment); setMappings(c.accounts.map(x => `${x.local_code},${x.external_code}`).join('\n')); setAuxiliaries(c.auxiliaries.map(x => `${x.kind},${x.local_value},${x.external_code}`).join('\n')); }
    } catch (e) { if (live.current && id === epoch.current) setError(errorMessage(e)); }
  }, [base, bookId, offset, session]);
  useEffect(() => { void load(); }, [load]);
  async function run(action: () => Promise<void>) {
    if (lock.current) return; lock.current = true; setBusy(true); setError(''); setMessage('');
    try { await action(); } catch (e) { if (live.current) setError(errorMessage(e)); }
    finally { lock.current = false; if (live.current) setBusy(false); }
  }
  function key(kind: string) { return keys.current[kind] ||= crypto.randomUUID(); }
  function changed() { keys.current = {}; setPreview(null); }
  async function configure() {
    await run(async () => {
      const accounts = mappings.split('\n').filter(x => x.trim()).map(row => { const values = row.split(',').map(x => x.trim()); if (values.length !== 2 || values.some(x => !x)) throw new Error('科目映射每行填写“本地科目,外部科目”'); return { local_code: values[0], external_code: values[1] }; });
      const aux = auxiliaries.split('\n').filter(x => x.trim()).map(row => { const values = row.split(',').map(x => x.trim()); if (values.length !== 3 || !['department', 'employee', 'counterparty', 'project'].includes(values[0]) || values.some(x => !x)) throw new Error('辅助项每行填写“类型,本地值,外部编号”'); return { kind: values[0], local_value: values[1], external_code: values[2] }; });
      await api(`${base}/configuration`, session, { method: 'POST', body: ({ expected_version: data?.configuration?.version ?? 0, external_book_id: externalBook, product_version: version, deployment, accounts, auxiliaries: aux, reason, idempotency_key: key('config') }) });
      if (!live.current) return; keys.current = {}; await load(); setMessage('新映射版本已保存，旧交付包仍保留原映射。');
    });
  }
  async function importFile() {
    if (!file) return;
    await run(async () => {
      const invalidSize = uploadSizeError(file.size, 2_000_000);
      if (invalidSize) throw new Error(invalidSize);
      if (!/\.(csv|json)$/i.test(file.name)) throw new Error('请选择 UTF-8 JSON 或 CSV 文件');
      const content = new TextDecoder('utf-8', { fatal: true }).decode(await file.arrayBuffer());
      const result = await api<Preview>(`${base}/imports`, session, { method: 'POST', body: ({ format: /\.csv$/i.test(file.name) ? 'csv' : 'json', content, reason, idempotency_key: key('import') }) });
      if (live.current) { setPreview(result); await load(); }
    });
  }
  async function prepare() {
    await run(async () => {
      const intent = await api<Intent>(`${base}/outbox`, session, { method: 'POST', body: ({ export_id: exportId, configuration_version: data?.configuration?.version, reason, idempotency_key: key('export') }) });
      if (live.current) { download(intent.package, `tplus-package-${intent.id}.json`); await load(); setMessage('冻结交付包已生成。请按客户软件版本转换并导入，再到回执核验页核对。'); }
    });
  }
  return <main className="integration-workbench"><header className="integration-heading"><button className="button secondary small" disabled={busy} onClick={onBack}><ArrowLeft size={15} />返回账套</button><span className="section-eyebrow">T+ · 文件接入验证</span><h1>财务软件接入</h1><p>先核验账套与映射，再保留客户导出资料、准备已审核凭证的交付包。</p><span className="integration-offline"><Link2 size={15} />文件模式 · 真实连接未启用</span></header>
    {error && <div role="alert" className="inline-error">{error}</div>}{message && <p role="status" className="integration-message">{message}</p>}
    {!data ? <p><LoaderCircle className="spinner" size={16} />正在读取接入配置…</p> : <>
      <section className="integration-section"><div className="integration-section-title"><span>01</span><h2>账套与科目映射 <small>版本 {data.configuration?.version ?? 0}</small></h2></div>
        <div className="integration-fields"><label>客户 T+ 账套标识<input disabled={busy || !data.can_configure} value={externalBook} onChange={e => { setExternalBook(e.target.value); changed(); }} /></label><label>产品版本<input disabled={busy || !data.can_configure} value={version} onChange={e => { setVersion(e.target.value); changed(); }} /></label><label>部署方式<select disabled={busy || !data.can_configure} value={deployment} onChange={e => { setDeployment(e.target.value as Config['deployment']); changed(); }}><option value="unknown">待确认</option><option value="on_premise">本地或云主机部署</option><option value="tplus_cloud">T+Cloud</option></select></label></div>
        <div className="integration-fields"><label>科目映射 · 每行“本地科目,外部科目”<textarea rows={5} disabled={busy || !data.can_configure} value={mappings} placeholder={'1002,1002\n6602.02,6602.02'} onChange={e => { setMappings(e.target.value); changed(); }} /></label><label>辅助项映射 · 每行“类型,本地值,外部编号”<textarea rows={5} disabled={busy || !data.can_configure} value={auxiliaries} placeholder={'department,部门ID,外部部门编号\ncounterparty,原始往来名称,外部往来编号'} onChange={e => { setAuxiliaries(e.target.value); changed(); }} /><small>类型：department 部门、employee 人员、counterparty 往来、project 项目。</small></label></div>
        <label>本次操作依据<input value={reason} maxLength={1000} disabled={busy || !data.can_review} placeholder="填写配置、导入或交付的核对依据" onChange={e => { setReason(e.target.value); changed(); }} /></label>
        <button className="button secondary" disabled={busy || !data.can_configure || !externalBook.trim() || !mappings.trim() || !reason.trim()} onClick={() => void configure()}>保存新映射版本</button>{!data.can_configure && <p>映射由有明确长期账套管理授权的会计维护。</p>}
      </section>
      <section className="integration-section"><div className="integration-section-title"><span>02</span><h2>保留客户导出资料</h2></div><p>科目、往来、项目、银行账户、凭证与余额按来源编号保留。中性模板需按客户实际导出文件转换；导入的凭证与余额只作核对参考。</p><div className="integration-actions"><button className="button secondary" disabled={busy || !data.can_review || !data.configuration} onClick={() => void run(async () => { const value = await api(`${base}/template`, session); if (live.current) download(value, 'tplus-reference-template.json'); })}><Download size={15} />下载中性模板</button><label className="integration-file"><FileUp size={15} /><input type="file" accept=".json,.csv" disabled={busy || !data.can_review} onChange={e => { setFile(e.target.files?.[0] ?? null); changed(); }} /></label><button className="button primary" disabled={busy || !data.can_review || !data.configuration || !file || !reason.trim()} onClick={() => void importFile()}>预览导入</button></div>
        {preview && <div className="integration-preview"><h3>共 {preview.record_count} 条 · 重复 {preview.duplicates} · 冲突 {preview.conflicts}</h3><ul>{preview.records.slice(0, 20).map(r => <li key={`${r.kind}:${r.external_id}`}>{r.external_id} · {r.name || r.kind} · {r.period}</li>)}</ul>{preview.record_count > 20 && <p>此处显示前 20 条，确认时校验完整文件。</p>}<button className="button primary" disabled={busy || !data.can_review || preview.conflicts > 0 || preview.state === 'confirmed'} onClick={() => void run(async () => { const value = await api<Preview>(`${base}/imports/${preview.id}/confirm`, session, { method: 'POST', body: ({ fingerprint: preview.fingerprint }) }); if (live.current) { setPreview(value); await load(); setMessage('原始参考记录已保留，没有生成重复凭证。'); } })}>{preview.state === 'confirmed' ? '已确认保留' : '确认保留参考记录'}</button></div>}
        {data.imports.map(row => <button className="integration-history" key={row.id} disabled={busy} onClick={() => void run(async () => { const value = await api<Preview>(`${base}/imports/${row.id}`, session); if (live.current) setPreview(value); })}>{row.record_count} 条参考记录 <span>{row.state === 'confirmed' ? '已保留' : '待确认'}</span></button>)}
      </section>
      <section className="integration-section"><div className="integration-section-title"><span>03</span><h2>准备已审核凭证交付包</h2></div><p>每个导出只创建一个持久意图，科目与辅助项映射随包冻结。当前包为中性 JSON，尚未验证为客户 T+ 的原生导入格式。导入后请按原导出的回执模板核对本地科目与辅助项，并保留客户软件的外部凭证号。</p><div className="integration-actions"><select aria-label="选择已审核导出" disabled={busy || !data.can_review} value={exportId} onChange={e => { setExportId(e.target.value); keys.current.export = ''; }}><option value="">选择已审核导出</option>{exports.map(e => <option key={e.id} value={e.id}>{e.filename}</option>)}</select><button className="button primary" disabled={busy || !data.can_review || !exportId || !data.configuration || !reason.trim()} onClick={() => void prepare()}>冻结并下载交付包</button></div>{data.outbox.map(row => <div className="integration-history" key={row.id}><span>{row.external_code.slice(0, 12)} · 等待版本适配</span><button className="button secondary small" disabled={busy} onClick={() => void run(async () => { const intent = await api<Intent>(`${base}/outbox/${row.id}`, session); if (live.current) download(intent.package, `tplus-package-${intent.id}.json`); })}>下载原包</button></div>)}</section>
      {data.pagination.total > 20 && <div className="integration-actions"><button className="button secondary" disabled={busy || offset === 0} onClick={() => setOffset(x => Math.max(0, x - 20))}>上一页</button><span>第 {Math.floor(offset / 20) + 1} 页</span><button className="button secondary" disabled={busy || !data.pagination.has_more} onClick={() => setOffset(x => x + 20)}>下一页</button></div>}
    </>}
  </main>;
}
