import { useCallback, useEffect, useRef, useState } from 'react';
import { Building2, CheckCircle2, LoaderCircle, Plus, Trash2, TriangleAlert } from 'lucide-react';
import { api, errorMessage } from './api';
import type { Department } from './business-types';
import type { Company, CompanyRole, Member, Session } from './types';
import { IntelligenceCompanySettings } from './IntelligencePanel';

export function BusinessOrganization({ session, companies, onChanged, onBusyChange }: { session: Session; companies: Company[]; onChanged: () => Promise<void>; onBusyChange: (busy: boolean) => void }) {
  const [companyId, setCompanyId] = useState(companies[0]?.id ?? '');
  const [departments, setDepartments] = useState<Department[]>([]);
  const [members, setMembers] = useState<Member<CompanyRole>[]>([]);
  const [approvers, setApprovers] = useState<Record<string, Member<string>[]>>({});
  const [departmentName, setDepartmentName] = useState('');
  const [choices, setChoices] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(false);
  const [operationBusy, setBusy] = useState(false);
  const [settingsBusy, setSettingsBusy] = useState(false);
  const busy = operationBusy || settingsBusy;
  const changeSettingsBusy = useCallback((value: boolean) => setSettingsBusy(value), []);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const lock = useRef(false);
  useEffect(() => { onBusyChange(busy); return () => onBusyChange(false); }, [busy, onBusyChange]);
  const reload = useCallback(async (signal?: AbortSignal) => {
    if (!companyId) return;
    setLoading(true);
    const [departmentResult, memberResult] = await Promise.all([
      api<{ departments: Department[] }>(`/companies/${encodeURIComponent(companyId)}/departments`, session, { signal }),
      api<{ members: Member<CompanyRole>[] }>(`/companies/${encodeURIComponent(companyId)}/members`, session, { signal }),
    ]);
    const entries = await Promise.all(departmentResult.departments.map(async department => {
      const data = await api<{ approvers: Member<string>[] }>(`/companies/${encodeURIComponent(companyId)}/departments/${encodeURIComponent(department.id)}/approvers`, session, { signal });
      return [department.id, data.approvers] as const;
    }));
    if (!signal?.aborted) { setDepartments(departmentResult.departments); setMembers(memberResult.members); setApprovers(Object.fromEntries(entries)); setLoading(false); }
  }, [companyId, session]);
  useEffect(() => {
    const controller = new AbortController(); setDepartments([]); setMembers([]); setApprovers({}); setError(''); setNotice('');
    void reload(controller.signal).catch(failure => { if (!controller.signal.aborted) { setError(errorMessage(failure)); setLoading(false); } });
    return () => controller.abort();
  }, [reload]);
  async function change(action: () => Promise<unknown>, text: string) {
    if (lock.current) return;
    lock.current = true; setBusy(true); setError(''); setNotice('');
    try { await action(); await reload(); await onChanged(); setNotice(text); }
    catch (failure) { if (!(failure instanceof DOMException && failure.name === 'AbortError')) setError(errorMessage(failure)); }
    finally { lock.current = false; setBusy(false); }
  }
  return <div className="business-organization"><label className="field-label" htmlFor="organization-company">客户企业</label><select id="organization-company" value={companyId} disabled={busy} onChange={event => { setCompanyId(event.target.value); setChoices({}); setDepartmentName(''); }}><option value="">选择客户企业</option>{companies.map(company => <option key={company.id} value={company.id}>{company.name}</option>)}</select>{error && <p className="form-error" role="alert"><TriangleAlert size={16} />{error}</p>}{notice && <p className="form-success" role="status"><CheckCircle2 size={16} />{notice}</p>}{loading ? <div className="loading-block"><LoaderCircle className="spinner" size={16} />正在载入部门与审批人…</div> : departments.map(department => <section key={department.id} className="organization-department"><h3><Building2 size={16} />{department.name}</h3><p className="field-help">审批人可查看此部门事项；申请人不能审批自己的事项。</p>{(approvers[department.id] ?? []).map(approver => <div className="member-row" key={approver.user_id}><div className="member-description"><strong>{approver.name || approver.email || approver.user_id}</strong><span>{approver.email}</span></div><button className="icon-button" type="button" disabled={busy} aria-label={`撤销 ${department.name} 审批人 ${approver.email || approver.name}`} onClick={() => void change(() => api(`/companies/${encodeURIComponent(companyId)}/departments/${encodeURIComponent(department.id)}/approvers/${encodeURIComponent(approver.user_id)}`, session, { method: 'DELETE' }), '部门审批授权已撤销。')}><Trash2 size={16} /></button></div>)}<form className="grant-role-row" onSubmit={event => { event.preventDefault(); void change(() => api(`/companies/${encodeURIComponent(companyId)}/departments/${encodeURIComponent(department.id)}/approvers`, session, { method: 'PUT', body: { user_id: choices[department.id] } }), '部门审批人已配置。'); }}><label><span className="field-label">部门审批人</span><select aria-label={`${department.name} 的部门审批人`} value={choices[department.id] ?? ''} disabled={busy} required onChange={event => setChoices(current => ({ ...current, [department.id]: event.target.value }))}><option value="">选择该企业成员</option>{members.filter(member => !(approvers[department.id] ?? []).some(approver => approver.user_id === member.user_id)).map(member => <option key={member.user_id} value={member.user_id}>{member.name || member.email}</option>)}</select></label><button className="button secondary" type="submit" disabled={busy || !choices[department.id]}><Plus size={15} />添加审批人</button></form></section>)}{companyId && <form className="grant-form" onSubmit={event => { event.preventDefault(); void change(async () => { await api(`/companies/${encodeURIComponent(companyId)}/departments`, session, { method: 'POST', body: { name: departmentName.trim() } }); setDepartmentName(''); }, '部门已建立，请分配成员并配置审批人。'); }}><h3>新建部门</h3><div className="grant-role-row"><label><span className="field-label">部门名称</span><input aria-label="新建部门名称" value={departmentName} required maxLength={100} disabled={busy} onChange={event => setDepartmentName(event.target.value)} placeholder="例如：销售部" /></label><button className="button primary" type="submit" disabled={busy || !departmentName.trim()}><Plus size={15} />新建部门</button></div></form>}<IntelligenceCompanySettings key={companyId} session={session} companyId={companyId} onBusyChange={changeSettingsBusy} />{!companyId && <p className="field-help">先建立客户企业，再配置部门和审批人。</p>}</div>;
}
