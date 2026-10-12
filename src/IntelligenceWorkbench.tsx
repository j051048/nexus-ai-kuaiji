import { useCallback, useEffect, useRef, useState } from 'react';
import { ArrowRight, CheckCircle2, FileSearch, LoaderCircle, RefreshCw, ShieldCheck, TriangleAlert } from 'lucide-react';
import { api, errorMessage } from './api';
import type { Session } from './types';
import type { IntelligenceFinding } from './intelligence-types';

export function IntelligenceWorkbench({ session, onOpenEvent }: { session: Session; onOpenEvent: (eventId: string) => void }) {
  const [findings, setFindings] = useState<IntelligenceFinding[]>([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const active = useRef(true);
  const epoch = useRef(0);
  useEffect(() => { active.current = true; return () => { active.current = false; epoch.current += 1; }; }, [session.scope_key]);
  const load = useCallback(async (signal?: AbortSignal) => {
    const version = ++epoch.current; setLoading(true); setError('');
    try {
      const result = await api<{ findings: IntelligenceFinding[]; total: number }>(`/intelligence/exceptions?limit=50&offset=${offset}`, session, { signal });
      if (signal?.aborted || !active.current || version !== epoch.current) return;
      setFindings(result.findings); setTotal(result.total);
    } catch (failure) { if (!signal?.aborted && active.current && version === epoch.current) setError(errorMessage(failure)); }
    finally { if (!signal?.aborted && active.current && version === epoch.current) setLoading(false); }
  }, [session, offset]);
  useEffect(() => { const controller = new AbortController(); setFindings([]); void load(controller.signal); return () => controller.abort(); }, [load]);
  return <main className="intelligence-workbench"><section className="page-heading"><div><p className="eyebrow">EVIDENCE, BEFORE ENTRIES</p><h1>把判断，留在异常发生处。</h1><p className="page-description">集中核对票据、业务事项与流水之间的差异，处理依据随事项保存。</p></div><button className="button secondary" type="button" disabled={loading} onClick={() => void load()}><RefreshCw size={16} />刷新异常</button></section><div className="intelligence-workbench-intro"><FileSearch size={24} /><div><strong>{total} 项待核对</strong><p>金额、税额、交易对方和重复票据均保留核对依据。</p></div><span><ShieldCheck size={15} />仅当前账号获授权范围</span></div><section className="panel intelligence-exception-panel"><div className="panel-heading"><div><h2>票据核对异常</h2><p>点开业务事项查看原件、识别结果和处理记录</p></div></div>{error && <p className="inline-error" role="alert"><TriangleAlert size={15} />{error}</p>}{loading ? <div className="loading-block"><LoaderCircle className="spinner" size={16} />正在读取异常…</div> : findings.length ? <div className="intelligence-exception-list">{findings.map(finding => <article key={finding.id ?? `${finding.event_id}:${finding.code}:${JSON.stringify(finding.payload)}`} className={`exception-workbench-row ${finding.severity}`}><span className="exception-workbench-icon"><TriangleAlert size={21} /></span><div><span>{finding.company_name} · {finding.severity === 'blocking' ? '阻断' : '提醒'}</span><h3>{finding.event_description || '业务事项'}</h3><p>{finding.payload.message}</p></div>{finding.event_id && <button className="button secondary small" type="button" onClick={() => onOpenEvent(finding.event_id!)}>核对事项<ArrowRight size={14} /></button>}</article>)}</div> : !error && <div className="empty-state"><div className="empty-icon"><CheckCircle2 size={28} /></div><strong>当前没有待处理的票据异常</strong><p>识别票据并核对后，获授权事项的差异将在这里汇总。</p></div>}<div className="business-pagination"><span>{total} 项{total > 0 && ` · 第 ${Math.floor(offset / 50) + 1} 页`}</span><div><button className="text-button" type="button" disabled={loading || offset === 0} onClick={() => { epoch.current += 1; setFindings([]); setOffset(value => Math.max(0, value - 50)); }}>上一页</button><button className="text-button" type="button" disabled={loading || offset + 50 >= total} onClick={() => { epoch.current += 1; setFindings([]); setOffset(value => value + 50); }}>下一页</button></div></div></section></main>;
}
