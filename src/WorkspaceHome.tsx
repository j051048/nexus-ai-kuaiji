import { useCallback, useEffect, useRef, useState } from 'react';
import { ArrowRight, CheckCircle2, Clock3, FilePlus2, LoaderCircle, RefreshCw, TriangleAlert } from 'lucide-react';
import { api, errorMessage } from './api';
import { roleCopy, taskLabels } from './workspace-types';
import type { WorkspaceProfile, WorkspaceTask, WorkspaceTasks, TaskKind } from './workspace-types';
import type { Session } from './types';
import type { BusinessTemplate } from './business-types';
import './workspace.css';

export function WorkspaceHome({ session, profile, profileError, onRetryProfile, onOpen, onNew, onAccounting, onReview, revision, paused = false }: {
  session: Session; profile: WorkspaceProfile | null; profileError: string; onRetryProfile: () => void;
  onOpen: (task: WorkspaceTask) => void; onNew: (template: BusinessTemplate) => void; onAccounting: () => void; onReview: () => void; revision: number; paused?: boolean;
}) {
  const [bucket, setBucket] = useState<'action' | 'waiting'>('action');
  const [kind, setKind] = useState<TaskKind | ''>(''); const [company, setCompany] = useState(''); const [offset, setOffset] = useState(0);
  const [value, setValue] = useState<WorkspaceTasks | null>(null); const [error, setError] = useState(''); const [loading, setLoading] = useState(false); const [opening, setOpening] = useState('');
  const [openError, setOpenError] = useState('');
  const epoch = useRef(0); const alive = useRef(true); const scope = useRef(session.scope_key); scope.current = session.scope_key;
  const load = useCallback(async (signal?: AbortSignal) => {
    const request = ++epoch.current; setLoading(true); setError('');
    try { const query = new URLSearchParams({ bucket, limit: '20', offset: String(offset), ...(kind ? { kind } : {}), ...(company ? { company_id: company } : {}) });
      const next = await api<WorkspaceTasks>(`/workspace/tasks?${query}`, session, { signal }); if (alive.current && !signal?.aborted && request === epoch.current) setValue(next);
    } catch (caught) { if (alive.current && !signal?.aborted && request === epoch.current) { setValue(null); setError(errorMessage(caught)); } }
    finally { if (alive.current && !signal?.aborted && request === epoch.current) setLoading(false); }
  }, [session, bucket, kind, company, offset]);
  useEffect(() => { alive.current = true; return () => { alive.current = false; epoch.current++; }; }, [session.scope_key]);
  useEffect(() => { const controller = new AbortController(); setValue(null); void load(controller.signal); return () => { controller.abort(); epoch.current++; }; }, [load, revision]);
  const openingLock = useRef(false);
  async function open(task: WorkspaceTask) {
    if (paused || openingLock.current) return; openingLock.current = true; setOpening(task.key); setOpenError(''); const started = session.scope_key;
    try { const current = await api<WorkspaceTask>(`/workspace/tasks/${encodeURIComponent(task.kind)}/${encodeURIComponent(task.ref)}`, session); if (alive.current && scope.current === started) onOpen(current); }
    catch (caught) { if (alive.current && scope.current === started) { setOpenError(errorMessage(caught)); void load(); } }
    finally { if (alive.current && scope.current === started) setOpening(''); openingLock.current = false; }
  }
  const copy = roleCopy[profile?.role ?? 'viewer'];
  const rows = value?.tasks ?? []; const locked = paused || !!opening;
  return <main className="workspace-home">
    <div className="workspace-heading"><div><p className="workspace-kicker">今天需要关注的事</p><h1>{copy.name}</h1><p>{copy.subtitle}</p></div><button className="button secondary" disabled={loading || locked} onClick={() => { onRetryProfile(); void load(); }}><RefreshCw size={16} />刷新工作台</button></div>
    {profileError && <p className="inline-error" role="alert">{profileError}<button className="text-button" onClick={onRetryProfile}>重新读取权限</button></p>}
    {!profile && !profileError && <p className="loading-block"><LoaderCircle className="spinner" size={16} />正在读取我的工作范围…</p>}
    {profile?.can_submit && <section className="workspace-intake-actions" aria-label="快速录入"><div><FilePlus2 size={22} /><strong>记录一笔业务</strong><span>先上传附件，AI 协助填写，核对后提交。</span></div>{([['reimbursement', '报销'], ['procurement_expense', '采购 / 费用'], ['daily_cashflow', '日常收支']] as const).map(([template, label]) => <button key={template} className="button secondary" disabled={locked} onClick={() => onNew(template)}>{label}<ArrowRight size={15} /></button>)}</section>}
    <section className="workspace-summary" aria-label="我的工作概览"><button className={bucket === 'action' ? 'active' : ''} disabled={locked} onClick={() => { setBucket('action'); setOffset(0); }}><span><TriangleAlert size={18} />需要我处理</span><strong>{value ? value.counts.action ?? 0 : '—'}</strong><small>审批、补件、异常和复核</small></button><button className={bucket === 'waiting' ? 'active' : ''} disabled={locked} onClick={() => { setBucket('waiting'); setOffset(0); }}><span><Clock3 size={18} />等待他人 / 后台</span><strong>{value ? value.counts.waiting ?? 0 : '—'}</strong><small>已提交事项和自动处理进度</small></button>{profile?.can_accounting && <div className="workspace-accounting-shortcuts"><span>会计工作区 · {profile.book_count} 个授权期间</span>{profile.can_review && <button className="button primary" disabled={locked} onClick={onReview}>开始批量复核<ArrowRight size={15} /></button>}<button className="text-button" disabled={locked} onClick={onAccounting}>查看企业账套<ArrowRight size={14} /></button></div>}</section>
    <section className="panel workspace-inbox"><div className="workspace-inbox-heading"><div><h2>{bucket === 'action' ? '需要我处理' : '办理进度'}</h2><p>每项保留对应企业、处理人和下一步。</p></div><div className="workspace-inbox-filters"><label>事项类型<select aria-label="筛选待办类型" value={kind} disabled={locked} onChange={e => { setKind(e.target.value as TaskKind | ''); setOffset(0); }}><option value="">全部类型</option>{Object.entries(taskLabels).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></label>{(profile?.companies.length ?? 0) > 1 && <label>客户企业<select aria-label="筛选待办企业" value={company} disabled={locked} onChange={e => { setCompany(e.target.value); setOffset(0); }}><option value="">全部授权企业</option>{profile?.companies.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}</select></label>}</div></div>
      {(openError || error) && <p className="inline-error" role="alert">{openError || error}</p>}{loading ? <p className="loading-block"><LoaderCircle className="spinner" size={16} />正在读取待办…</p> : rows.length ? <div className="workspace-task-list">{rows.map(task => <article className={'workspace-task ' + (task.overdue ? 'overdue' : '')} key={task.key} data-task-key={task.key}><div className="workspace-task-main"><div><span className="workspace-task-kind">{taskLabels[task.kind]}</span>{task.overdue && <span className="case-overdue">已逾期</span>}</div><h3>{task.title}</h3><p>{task.company_name}</p><small>{task.responsible}{task.due_on ? ` · 期限 ${task.due_on}` : ''}</small></div><button className="button secondary" disabled={locked} onClick={() => void open(task)}>{opening === task.key ? <LoaderCircle className="spinner" size={15} /> : null}{task.action_label}<ArrowRight size={15} /></button></article>)}</div> : !error && <div className="workspace-clear"><CheckCircle2 size={28} /><strong>{bucket === 'action' ? '当前没有需要我处理的事项' : '暂无等待中的事项'}</strong><p>{bucket === 'action' ? '新审批、补件和会计异常出现后，会集中显示在这里。' : '提交的事项和后台任务会保留办理进度。'}</p></div>}
      {value && <div className="pagination-controls"><span>共 {value.pagination.total} 项 · 每页 20 项</span><button className="button secondary small" disabled={loading || locked || offset === 0} onClick={() => setOffset(n => Math.max(0, n - 20))}>上一页待办</button><button className="button secondary small" disabled={loading || locked || !value.pagination.has_more} onClick={() => setOffset(n => n + 20)}>下一页待办</button></div>}
    </section><p className="workspace-boundary">AI 识别与分录建议由后台处理；凭证仍需会计复核后导出。</p>
  </main>;
}
