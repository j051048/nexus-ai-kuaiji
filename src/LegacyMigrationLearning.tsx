import { useCallback, useEffect, useRef, useState } from 'react';
import { CheckCircle2, FileCheck2, History, LoaderCircle, Plus, RefreshCw, ShieldCheck, TriangleAlert, X } from 'lucide-react';
import { ApiError, api, errorMessage } from './api';
import type { LearningDefinition } from './learning-types';
import type { Session, Pagination } from './types';
import type { MigrationRecord, MigrationRecords } from './legacy-migration-types';

type LegacyShadow = { id: string; passed: boolean; report?: { positive_count?: number; negative_count?: number; failures?: { reason: string; record_id?: string }[] }; current?: boolean };
type LegacyCandidate = { id: string; version: number; status: string; definition: LearningDefinition; positive_record_ids: string[]; eligibility?: { eligible: boolean; blocked_record_ids?: string[]; issues?: string[] }; issues?: unknown[]; permissions: { review: boolean; shadow: boolean; publish: boolean; retire: boolean }; review?: { decision: string; created_at?: string } | null; latest_shadow?: LegacyShadow | null; reason?: string };
type LegacyLearning = { candidates: LegacyCandidate[]; counterparties: { id: string; code: string; name: string }[]; pagination: Pagination; permissions?: { create?: boolean }; can_create?: boolean };
const encoded = encodeURIComponent;
const labels: Record<string, string> = { candidate: '待独立审核', draft: '待独立审核', proposed: '待独立审核', approved: '已审核待回放', reviewed: '已审核待回放', rejected: '已驳回', published: '已发布', retired: '已停用' };

/** Opt-in historical experience; archive status alone never publishes a rule. */
export function LegacyMigrationLearning({ session, bookId, projectId, canCreate, onBusyChange, onChanged, onEvidence, onLostAccess }: { session: Session; bookId: string; projectId: string; canCreate: boolean; onBusyChange: (busy: boolean) => void; onChanged?: () => void; onEvidence: (record: MigrationRecord) => void; onLostAccess: () => void }) {
  const base = `/books/${encoded(bookId)}/legacy-migrations/${encoded(projectId)}`;
  const [data, setData] = useState<LegacyLearning | null>(null), [records, setRecords] = useState<MigrationRecords | null>(null);
  const [candidateOffset, setCandidateOffset] = useState(0), [recordOffset, setRecordOffset] = useState(0);
  const [loading, setLoading] = useState(true), [busy, setBusy] = useState(false), [error, setError] = useState(''), [notice, setNotice] = useState('');
  const [selected, setSelected] = useState(''), [positiveIds, setPositiveIds] = useState<string[]>([]), [negativeIds, setNegativeIds] = useState<string[]>([]);
  const [editor, setEditor] = useState(false), [name, setName] = useState(''), [party, setParty] = useState(''), [direction, setDirection] = useState<'expense' | 'income'>('expense');
  const [debit, setDebit] = useState(''), [credit, setCredit] = useState(''), [reason, setReason] = useState(''), [confirmed, setConfirmed] = useState(false);
  const active = useRef(true), locked = useRef(false), epoch = useRef(0), keys = useRef<Record<string, string>>({});
  const target = `${session.scope_key}:${bookId}:${projectId}`, targetRef = useRef(target); targetRef.current = target;
  const callback = useRef(onBusyChange); callback.current = onBusyChange;
  useEffect(() => { active.current = true; return () => { active.current = false; epoch.current++; callback.current(false); }; }, [target]);
  useEffect(() => { callback.current(busy || editor); }, [busy, editor]);
  const load = useCallback(async (signal?: AbortSignal) => {
    const generation = ++epoch.current, commandTarget = target;
    setLoading(true);
    try {
      const [overview, archive] = await Promise.all([api<LegacyLearning>(`${base}/sample-candidates?limit=20&offset=${candidateOffset}`, session, { signal }), api<MigrationRecords>(`${base}/records?kind=voucher&limit=20&offset=${recordOffset}`, session, { signal })]);
      if (active.current && !signal?.aborted && epoch.current === generation && targetRef.current === commandTarget) { setData(overview); setRecords(archive); setError(''); }
    } catch (failure) { if (active.current && !signal?.aborted && epoch.current === generation) { setData(null); setRecords(null); if (failure instanceof ApiError && [401, 403, 404].includes(failure.status)) onLostAccess(); setError(errorMessage(failure)); } }
    finally { if (active.current && !signal?.aborted && epoch.current === generation) setLoading(false); }
  }, [base, candidateOffset, recordOffset, session, target]);
  useEffect(() => { const controller = new AbortController(); void load(controller.signal); return () => controller.abort(); }, [load]);
  useEffect(() => { setData(null); setRecords(null); setSelected(''); setPositiveIds([]); setNegativeIds([]); setEditor(false); setReason(''); setConfirmed(false); keys.current = {}; }, [target]);
  const candidate = data?.candidates.find(item => item.id === selected);
  const candidateIssues = [...(candidate?.issues ?? []), ...(candidate?.eligibility?.issues ?? [])];
  function key(action: string) { return keys.current[action] ||= crypto.randomUUID(); }
  async function run(action: () => Promise<void>) {
    if (locked.current) return;
    locked.current = true; setBusy(true); setError(''); setNotice(''); const commandTarget = target;
    try { await action(); }
    catch (failure) { if (active.current && targetRef.current === commandTarget) { if (failure instanceof ApiError && [401, 403, 404].includes(failure.status)) onLostAccess(); setError(errorMessage(failure)); } }
    finally { locked.current = false; if (active.current && targetRef.current === commandTarget) setBusy(false); }
  }
  function choose(id: string) { setSelected(id); setNegativeIds([]); setReason(''); setConfirmed(false); keys.current = {}; }
  function check(id: string, value: boolean, kind: 'positive' | 'negative') {
    const setter = kind === 'positive' ? setPositiveIds : setNegativeIds;
    setter(previous => value ? [...previous, id] : previous.filter(item => item !== id)); setConfirmed(false); keys.current = {};
  }
  async function create() {
    await run(async () => {
      const currentParty = data?.counterparties.find(value => value.id === party);
      if (!currentParty || !positiveIds.length || !name.trim() || !debit.trim() || !credit.trim() || debit.trim() === credit.trim() || !reason.trim()) throw new Error('请选择稳定往来方、正例和完整借贷科目，填写明确的处理依据。');
      const definition: LearningDefinition = { name: name.trim(), source_kind: 'bank', conditions: { business_type: 'daily_cashflow', direction, category: null, payment_status: 'paid', counterparty: currentParty.name, counterparty_id: currentParty.id }, lines: [{ account_code: debit.trim(), side: 'debit', amount_field: 'gross' }, { account_code: credit.trim(), side: 'credit', amount_field: 'gross' }] };
      const result = await api<LegacyCandidate>(`${base}/sample-candidates`, session, { method: 'POST', body: { definition, positive_record_ids: positiveIds, reason: reason.trim(), idempotency_key: key('create') } });
      if (active.current && targetRef.current === target) { keys.current = {}; setEditor(false); setSelected(result.id); setPositiveIds([]); setReason(''); setNotice('候选已留存，须独立审核、真实正反例回放和明确发布后才会用于新草稿。'); await load(); }
    });
  }
  async function command(action: 'review' | 'shadow' | 'publish' | 'retire', decision?: 'approve' | 'reject') {
    if (!candidate) return;
    await run(async () => {
      if (!reason.trim()) throw new Error('请填写本次审核、回放或发布依据。');
      if (['review', 'shadow'].includes(action) && !confirmed) throw new Error('请先确认原件、事实和当前正反例范围。');
      const body = { expected_version: candidate.version, reason: reason.trim(), idempotency_key: key(`${candidate.id}:${action}:${decision ?? ''}`), ...(action === 'review' ? { decision, evidence_confirmed: true } : {}), ...(action === 'shadow' ? { negative_record_ids: negativeIds, evidence_confirmed: true } : {}), ...(action === 'publish' ? { shadow_id: candidate.latest_shadow?.id } : {}) };
      await api(`${base}/sample-candidates/${encoded(candidate.id)}/${action}`, session, { method: 'POST', body });
      if (active.current && targetRef.current === target) { keys.current = {}; setConfirmed(false); setReason(''); setNegativeIds([]); await load(); setNotice(action === 'review' ? '独立审核决定已保存，尚未直接发布企业规则。' : action === 'shadow' ? '影子回放已记录，请读取结果及原例依据。' : action === 'publish' ? '企业规则已明确发布，后续只生成待会计复核的草稿。' : '候选已停用，后续使用暂停，原凭证与已生成草稿保留依据。'); onChanged?.(); }
    });
  }
  return <section className="legacy-migration-learning" aria-label="历史经验复用"><div className="legacy-migration-card-title"><History size={23} /><div><h2>让核对过的经验帮助新月份</h2><p>先形成企业候选，再独立审核、核对正反例回放，最后明确发布。旧软件的状态不会直接成为正确答案。</p></div></div>
    {error && <p className="inline-error" role="alert"><TriangleAlert size={16} />{error}</p>}{notice && <p className="form-success" role="status">{notice}</p>}
    {loading && !data ? <div className="loading-block"><LoaderCircle size={17} className="spinner" />读取历史候选与凭证…</div> : data && <>
      {!data.counterparties.length && <p className="legacy-migration-help">当前接管快照没有已确认的稳定往来方可用于普通流水规则，此历史资料继续保留为核对参考。</p>}
      <div className="legacy-migration-learning-actions"><button className="button secondary small" disabled={busy || editor || loading} onClick={() => void load()}><RefreshCw size={15} />读取历史候选</button>{canCreate && data.can_create !== false && data.permissions?.create !== false && <button className="button primary small" disabled={busy || editor || !positiveIds.length || !data.counterparties.length} onClick={() => { setEditor(true); setReason(''); setName(''); setParty(''); setDebit(''); setCredit(''); keys.current = {}; }}><Plus size={15} />以所选正例建立候选</button>}<span>已选正例 {positiveIds.length} · 反例 {negativeIds.length}</span></div>
      <div className="legacy-migration-learning-columns"><section><h3>历史凭证与真实反例</h3><p className="legacy-migration-help">只选择能确认银行收支事实的普通已记账凭证。红字、结转、复杂分摊和事实不清的记录不能直接成为普通规则。</p><div className="legacy-migration-learning-records">{records?.records.map(record => { const payload = record.payload ?? {}; const isPositive = candidate?.positive_record_ids.includes(record.id ?? '') ?? false; return <article key={record.id}><div><strong>{String(payload.number ?? record.external_id ?? '历史凭证')}</strong><small>{record.period} · {String(payload.status === 'posted' ? '已记账' : payload.status === 'unposted' ? '未记账' : payload.status === 'void' ? '作废' : payload.status ?? '')}</small><button className="text-button" disabled={busy} onClick={() => onEvidence(record)}>核对原件与分录</button></div><div className="legacy-migration-learning-checks">{canCreate && !editor && <label className="legacy-migration-check"><input type="checkbox" aria-label={`历史规则正例 ${record.external_id}`} checked={positiveIds.includes(record.id ?? '')} disabled={busy || payload.status !== 'posted'} onChange={event => check(record.id!, event.target.checked, 'positive')} />选为候选正例</label>}{candidate && candidate.permissions.shadow && <label className="legacy-migration-check"><input type="checkbox" aria-label={`历史规则反例 ${record.external_id}`} checked={negativeIds.includes(record.id ?? '')} disabled={busy || editor || isPositive || payload.status !== 'posted'} onChange={event => check(record.id!, event.target.checked, 'negative')} />{isPositive ? '已是当前候选正例' : '选为不会命中本规则的真实反例'}</label>}</div></article>; })}</div>{records?.pagination && <LearningPage page={records.pagination} disabled={busy || editor} onPage={setRecordOffset} />}</section>
        <section><h3>版本化企业候选</h3><div className="legacy-migration-learning-candidates">{data.candidates.length ? data.candidates.map(item => <button key={item.id} type="button" disabled={busy || editor} className={selected === item.id ? 'selected' : ''} onClick={() => choose(item.id)}><span><strong>{item.definition.name}</strong><small>{item.definition.conditions.counterparty} · v{item.version}</small></span><span className={`badge ${item.status === 'published' ? 'approved' : 'pending'}`}>{labels[item.status] ?? item.status}</span></button>) : <p className="legacy-migration-help">暂无历史候选。先核对原件，再选定正例建立版本。</p>}</div>{data.pagination && <LearningPage page={data.pagination} disabled={busy || editor} onPage={setCandidateOffset} />}
          {candidate && <div className="legacy-migration-learning-candidate-detail"><strong>{candidate.definition.conditions.direction === 'expense' ? '支出' : '收入'} · {candidate.definition.conditions.counterparty}</strong><div className="legacy-migration-learning-formula">{candidate.definition.lines.map((line, index) => <span key={index}>{line.side === 'debit' ? '借' : '贷'} {line.account_code} · 总额</span>)}</div><p className="legacy-migration-help">正例 {candidate.positive_record_ids.length} 条 · {candidate.reason ?? '处理依据随候选版本留存'}</p>{candidateIssues.length ? <div className="legacy-migration-learning-errors">{candidateIssues.map((issue, index) => <p key={index}>{typeof issue === 'string' ? issue : typeof issue === 'object' && issue !== null && 'message' in issue ? String(issue.message) : JSON.stringify(issue)}</p>)}</div> : null}
            {candidate.latest_shadow && <div className={`legacy-migration-learning-shadow ${candidate.latest_shadow.passed && candidate.latest_shadow.current !== false ? 'passed' : 'failed'}`} role="status"><strong>{candidate.latest_shadow.current === false ? '回放依据已变化，需重新核对' : candidate.latest_shadow.passed ? '当前正反例回放通过' : '回放未通过，请查看差异'}</strong><p>正例 {candidate.latest_shadow.report?.positive_count ?? '—'} · 反例 {candidate.latest_shadow.report?.negative_count ?? '—'}</p>{candidate.latest_shadow.report?.failures?.map((failure, index) => <p key={index}>{failure.reason}</p>)}</div>}
            {(candidate.permissions.review || candidate.permissions.shadow || candidate.permissions.publish || candidate.permissions.retire) && <><label>本次候选处理依据<input aria-label="历史规则候选处理依据" value={reason} disabled={busy || editor} maxLength={1000} onChange={event => { setReason(event.target.value); setConfirmed(false); keys.current = {}; }} /></label>{(candidate.permissions.review || candidate.permissions.shadow) && <label className="legacy-migration-check"><input aria-label="已核对历史候选原件与正反例" type="checkbox" checked={confirmed} disabled={busy || editor} onChange={event => setConfirmed(event.target.checked)} />已核对当前版本原件、银行事实及所选正反例。</label>}<div className="legacy-migration-actions">{candidate.permissions.review && <><button className="button secondary small" disabled={busy || editor || !confirmed || !reason.trim()} onClick={() => void command('review', 'reject')}>独立驳回</button><button className="button primary small" disabled={busy || editor || !confirmed || !reason.trim()} onClick={() => void command('review', 'approve')}><ShieldCheck size={14} />独立审核候选</button></>}{candidate.permissions.shadow && <button className="button secondary small" disabled={busy || editor || !confirmed || !reason.trim() || !negativeIds.length} onClick={() => void command('shadow')}><FileCheck2 size={14} />核对正反例回放</button>}{candidate.permissions.publish && <button className="button primary small" disabled={busy || editor || !reason.trim() || !candidate.latest_shadow?.passed || candidate.latest_shadow.current === false} onClick={() => void command('publish')}><CheckCircle2 size={14} />明确发布企业规则</button>}{candidate.permissions.retire && <button className="text-button" disabled={busy || editor || !reason.trim()} onClick={() => void command('retire')}>停用此候选</button>}</div></>}
          </div>}
        </section></div>
      {editor && <section className="legacy-migration-learning-editor"><div><h3>建立待审核企业候选</h3><button className="icon-button" aria-label="关闭历史规则候选编辑" disabled={busy} onClick={() => setEditor(false)}><X size={19} /></button></div><p className="legacy-migration-help">固定精确交易对方与方向，两行使用总额。无法证明普通银行收支事实的历史凭证会保留阻断说明。</p><div className="legacy-migration-formgrid"><label>候选名称<input aria-label="历史规则候选名称" value={name} disabled={busy} maxLength={120} onChange={event => { setName(event.target.value); keys.current = {}; }} /></label><label>企业稳定往来方<select aria-label="历史规则候选稳定往来方" value={party} disabled={busy} onChange={event => { setParty(event.target.value); keys.current = {}; }}><option value="">选择当前有效企业档案</option>{data.counterparties.map(item => <option key={item.id} value={item.id}>{item.name} · {item.code}</option>)}</select></label><label>收支方向<select aria-label="历史规则候选收支方向" value={direction} disabled={busy} onChange={event => { setDirection(event.target.value as 'expense' | 'income'); keys.current = {}; }}><option value="expense">支出</option><option value="income">收入</option></select></label><label>借方企业科目编码<input aria-label="历史规则候选借方科目编码" value={debit} disabled={busy} onChange={event => { setDebit(event.target.value); keys.current = {}; }} /></label><label>贷方企业科目编码<input aria-label="历史规则候选贷方科目编码" value={credit} disabled={busy} onChange={event => { setCredit(event.target.value); keys.current = {}; }} /></label><label>会计处理依据<input aria-label="历史规则候选建立依据" value={reason} disabled={busy} maxLength={1000} onChange={event => { setReason(event.target.value); keys.current = {}; }} /></label></div><div className="legacy-migration-actions"><button className="button secondary" disabled={busy} onClick={() => setEditor(false)}>暂不保存</button><button className="button primary" disabled={busy || !positiveIds.length || !name.trim() || !party || !debit.trim() || !credit.trim() || !reason.trim()} onClick={() => void create()}>保存待审核候选</button></div></section>}
    </>}
  </section>;
}
function LearningPage({ page, disabled, onPage }: { page: Pagination; disabled: boolean; onPage: (offset: number) => void }) {
  return <div className="legacy-migration-pagination"><span>共 {page.total} 项 · 第 {Math.floor(page.offset / 20) + 1} 页</span><button className="button secondary small" disabled={disabled || page.offset === 0} onClick={() => onPage(Math.max(0, page.offset - 20))}>上一页</button><button className="button secondary small" disabled={disabled || !page.has_more} onClick={() => onPage(page.offset + 20)}>下一页</button></div>;
}
