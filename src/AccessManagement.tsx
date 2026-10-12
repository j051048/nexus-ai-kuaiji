import { useCallback, useEffect, useRef, useState } from 'react';
import { Building2, CheckCircle2, LoaderCircle, Plus, ShieldCheck, Trash2, TriangleAlert, X } from 'lucide-react';
import { api, errorMessage } from './api';
import { BusinessOrganization } from './BusinessOrganization';
import type { Department } from './business-types';
import type { Book, BookRole, Company, CompanyRole, FirmRole, Member, Session } from './types';

type Tab = 'firm' | 'company' | 'book' | 'departments';
type AccessRole = FirmRole | CompanyRole | BookRole;
const labels: Record<AccessRole, string> = { owner: '负责人', admin: '管理员', operator: '记账经办员', reviewer: '复核员', viewer: '只读', editor: '经办员', finance: '财务成员', employee: '企业员工', client_employee: '客户企业成员', client_member: '客户企业成员' };
const assignable: Record<Tab, AccessRole[]> = { firm: ['admin', 'operator', 'reviewer', 'viewer'], company: ['admin', 'finance', 'employee'], book: ['editor', 'reviewer', 'viewer'], departments: ['employee'] };

export function AccessManagement({ session, books, onClose, onChanged }: {
  session: Session; books: Book[]; onClose: () => void; onChanged: () => Promise<void>;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [tab, setTab] = useState<Tab>('firm');
  const [firms, setFirms] = useState<Member<FirmRole>[]>([]);
  const [companies, setCompanies] = useState<Company[]>([]);
  const [companyId, setCompanyId] = useState('');
  const [departments, setDepartments] = useState<Department[]>([]);
  const [departmentId, setDepartmentId] = useState('');
  const [bookId, setBookId] = useState(books[0]?.id ?? '');
  const [members, setMembers] = useState<Member<AccessRole>[]>([]);
  const [companyMembers, setCompanyMembers] = useState<Member<CompanyRole>[]>([]);
  const [loadedTarget, setLoadedTarget] = useState('');
  const [email, setEmail] = useState('');
  const [userId, setUserId] = useState('');
  const [role, setRole] = useState<AccessRole>('operator');
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const lock = useRef(false);
  const activeBook = books.find(book => book.id === bookId);
  const target = tab === 'firm' ? 'firm' : tab === 'company' ? companyId : bookId;
  const route = tab === 'firm' ? '/firm/members' : tab === 'company' ? `/companies/${encodeURIComponent(companyId)}/members` : `/books/${encodeURIComponent(bookId)}/members`;

  useEffect(() => { dialog.current?.showModal(); }, []);

  useEffect(() => {
    const controller = new AbortController();
    void Promise.all([
      api<{ members: Member<FirmRole>[] }>('/firm/members', session, { signal: controller.signal }),
      api<{ companies: Company[] }>('/companies', session, { signal: controller.signal }),
    ]).then(([firmResult, companyResult]) => {
      if (controller.signal.aborted) return;
      setFirms(firmResult.members); setCompanies(companyResult.companies); setCompanyId(companyResult.companies[0]?.id ?? '');
    }).catch(failure => { if (!controller.signal.aborted) setError(errorMessage(failure)); });
    return () => controller.abort();
  }, [session]);

  const reload = useCallback(async (signal?: AbortSignal) => {
    if (tab === 'departments') { setLoading(false); return; }
    if (!target) { setMembers([]); setLoadedTarget(''); setLoading(false); return; }
    setLoading(true);
    const result = await api<{ members: Member<AccessRole>[] }>(route, session, { signal });
    if (signal?.aborted) return;
    setMembers(result.members); setLoadedTarget(`${tab}:${target}`);
    if (tab === 'firm') setFirms(result.members as Member<FirmRole>[]);
    if (tab === 'company') {
      const data = await api<{ departments: Department[] }>(`/companies/${encodeURIComponent(companyId)}/departments`, session, { signal });
      if (!signal?.aborted) setDepartments(data.departments);
    }
    if (tab === 'book' && activeBook?.company_id) {
      const eligible = await api<{ members: Member<CompanyRole>[] }>(`/companies/${encodeURIComponent(activeBook.company_id)}/members`, session, { signal });
      if (!signal?.aborted) setCompanyMembers(eligible.members);
    }
    if (!signal?.aborted) setLoading(false);
  }, [route, session, tab, target, activeBook?.company_id, companyId]);

  useEffect(() => {
    const controller = new AbortController();
    setError(''); setNotice(''); setCompanyMembers([]);
    void reload(controller.signal).catch(failure => {
      if (!controller.signal.aborted) { setError(errorMessage(failure)); setLoading(false); }
    });
    return () => controller.abort();
  }, [reload]);

  async function change(action: () => Promise<unknown>, message: string) {
    if (lock.current) return;
    lock.current = true; setBusy(true); setError(''); setNotice('');
    try { await action(); await reload(); await onChanged(); setNotice(message); }
    catch (failure) { if (!(failure instanceof DOMException && failure.name === 'AbortError')) setError(errorMessage(failure)); }
    finally { lock.current = false; setBusy(false); }
  }

  function switchTab(next: Tab) {
    if (busy) return;
    setTab(next); setRole(assignable[next][0]); setMembers([]); setLoadedTarget(''); setEmail(''); setUserId(''); setDepartmentId('');
  }

  const eligible = [...new Map([...firms, ...companyMembers.filter(member => member.role === 'finance' || member.role === 'admin' || member.role === 'owner')].map(member => [member.user_id, member])).values()];
  const rows = loadedTarget === `${tab}:${target}` ? members : [];
  const instructions = tab === 'firm' ? '事务所成员还需账套分工授权。负责人和管理员可以管理本事务所的全部账套。'
    : tab === 'company' ? '企业员工可录入本人业务事项，部门归属决定默认业务范围；财务成员仍需单独授权账套，才能查看或处理账务。'
    : '经办员准备草稿，复核员审核并导出，只读成员查看资料。最终能力还受事务所或企业财务身份限制。';

  return <dialog ref={dialog} className="modal access-modal" aria-labelledby="access-title" onCancel={event => { event.preventDefault(); if (!busy) onClose(); }}><div className="modal-header"><div><h2 id="access-title">成员与账套授权</h2><p>{session.firm_name} · 按职责分配企业与账套访问权限</p></div><button type="button" className="icon-button" disabled={busy} onClick={onClose} aria-label="关闭弹窗"><X size={20} /></button></div><div className="access-tabs" role="tablist" aria-label="授权范围">{(['firm', 'company', 'departments', 'book'] as Tab[]).map(item => <button key={item} role="tab" aria-selected={tab === item} className={tab === item ? 'active' : ''} disabled={busy} onClick={() => switchTab(item)}>{item === 'firm' ? '事务所成员' : item === 'company' ? '客户企业成员' : item === 'departments' ? '部门与审批人' : '账套分工'}</button>)}</div><div className="access-content">{tab === 'departments' ? <BusinessOrganization session={session} companies={companies} onChanged={onChanged} onBusyChange={setBusy} /> : <><p className="access-instructions"><ShieldCheck size={17} />{instructions}</p>{error && <p className="form-error" role="alert"><TriangleAlert size={16} />{error}</p>}{notice && <p className="form-success" role="status"><CheckCircle2 size={16} />{notice}</p>}{tab === 'company' && <><label className="field-label" htmlFor="access-company">客户企业</label><select id="access-company" value={companyId} disabled={busy} onChange={event => { setCompanyId(event.target.value); setLoadedTarget(''); }}>{companies.map(company => <option key={company.id} value={company.id}>{company.name}</option>)}</select></>}{tab === 'book' && <><label className="field-label" htmlFor="access-book">分工账套</label><select id="access-book" value={bookId} disabled={busy} onChange={event => { setBookId(event.target.value); setLoadedTarget(''); setUserId(''); }}>{books.map(book => <option key={book.id} value={book.id}>{book.name} · {book.period}</option>)}</select></>}{loading ? <div className="loading-block"><LoaderCircle className="spinner" size={16} />正在载入授权…</div> : target ? <div className="member-list">{rows.length ? rows.map(member => <div className="member-row" key={member.user_id}><span className="member-avatar">{(member.name || member.email || '员').slice(0, 1)}</span><div className="member-description"><strong>{member.name || member.email || member.user_id}</strong><span>{member.email || '本地验证账户'}</span></div><select aria-label={`修改 ${member.email || member.name} 的权限`} value={member.role} disabled={busy || member.role === 'owner' || member.user_id === session.user_id} onChange={event => void change(() => api(tab === 'book' ? `${route}/${encodeURIComponent(member.user_id)}` : route, session, { method: 'PUT', body: tab === 'book' ? { role: event.target.value } : { email: member.email, role: event.target.value, ...(tab === 'company' ? { department_id: member.department_id ?? null } : {}) } }), '权限已更新，后续请求按最新授权执行。')}>{member.role === 'owner' && <option value="owner">负责人</option>}{assignable[tab].map(value => <option key={value} value={value}>{labels[value]}</option>)}</select>{tab === 'company' && <select aria-label={`修改 ${member.email || member.name} 的部门`} value={member.department_id ?? ''} disabled={busy} onChange={event => void change(() => api(route, session, { method: 'PUT', body: { email: member.email, role: member.role, department_id: event.target.value || null } }), '成员部门已更新。')}><option value="">未分配部门</option>{departments.map(department => <option key={department.id} value={department.id}>{department.name}</option>)}</select>}<button className="icon-button" type="button" disabled={busy || member.role === 'owner' || member.user_id === session.user_id} aria-label={`撤销 ${member.email || member.name} 的授权`} onClick={() => void change(() => api(`${route}/${encodeURIComponent(member.user_id)}`, session, { method: 'DELETE' }), '已撤销授权，后续请求将不再允许访问。')}><Trash2 size={16} /></button></div>) : <div className="member-empty"><Building2 size={23} /><p>{tab === 'book' ? '还没有为此账套分配具体成员' : '还没有成员授权'}</p></div>}</div> : <p className="field-help">{tab === 'company' ? '先创建客户企业，再分配成员。' : '先创建账套，再分配具体成员。'}</p>}{target && <form className="grant-form" onSubmit={event => {
      event.preventDefault();
      void change(() => api(tab === 'book' ? `${route}/${encodeURIComponent(userId)}` : route, session, { method: 'PUT', body: tab === 'book' ? { role } : { email: email.trim(), role, ...(tab === 'company' ? { department_id: departmentId || null } : {}) } }), '成员授权已保存。');
    }}><h3>{tab === 'book' ? '分配账套成员' : '添加已注册成员'}</h3>{tab === 'book' ? <><label className="field-label" htmlFor="grant-user">事务所或企业财务成员</label><select id="grant-user" value={userId} onChange={event => setUserId(event.target.value)} required disabled={busy || loading}><option value="">选择成员</option>{eligible.map(member => <option key={member.user_id} value={member.user_id}>{member.name || member.email} {member.email && `· ${member.email}`}</option>)}</select></> : <><label className="field-label" htmlFor="grant-email">成员登录邮箱</label><input id="grant-email" type="email" autoComplete="off" placeholder="请输入成员已登录过本系统的邮箱" value={email} onChange={event => setEmail(event.target.value)} disabled={busy || loading} required /><p className="field-help">请先在登录服务开通账户，并让成员至少登录一次，以建立账户档案。</p></>}{tab === 'company' && <label className="department-choice"><span className="field-label">所属部门</span><select aria-label="新成员所属部门" value={departmentId} disabled={busy || loading} onChange={event => setDepartmentId(event.target.value)}><option value="">未分配部门</option>{departments.map(department => <option key={department.id} value={department.id}>{department.name}</option>)}</select></label>}<div className="grant-role-row"><label><span className="field-label">权限角色</span><select aria-label="新成员权限角色" value={role} onChange={event => setRole(event.target.value as AccessRole)} disabled={busy || loading}>{assignable[tab].map(value => <option key={value} value={value}>{labels[value]}</option>)}</select></label><button className="button primary" type="submit" disabled={busy || loading || (tab === 'book' ? !userId : !email.trim())}>{busy ? <LoaderCircle className="spinner" size={16} /> : <Plus size={16} />}{tab === 'book' ? '分配账套' : '保存授权'}</button></div></form>}</>}</div><div className="modal-footer"><span className="field-help">授权由服务端校验，关闭窗口不会撤销已保存的更改。</span><button type="button" className="button secondary" disabled={busy} onClick={onClose}>完成</button></div></dialog>;
}
