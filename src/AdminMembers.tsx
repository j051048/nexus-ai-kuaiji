import { useCallback, useEffect, useRef, useState } from 'react';
import { ArrowLeftRight, Plus, ShieldCheck, Trash2, UserPlus } from 'lucide-react';
import { api, errorMessage } from './api';
import { EmptyState, Modal, Spinner } from './AppPrimitives';
import type { Book, CompanyRole, Member, Session } from './types';
import type { Department, SubmissionPolicy } from './business-types';
import { adminRoleLabels, type AccountRequest, type AdminCompany } from './admin-types';
import { MemberHandoverDialog } from './MemberHandoverDialog';
import { ReviewPolicyPanel } from './ReviewPolicyPanel';
import { BulkEmployeeInvites } from './BulkEmployeeInvites';
import { InvitationDelivery } from './InvitationDelivery';

export function AdminMembers({
  session,
  kind,
  company,
  departments = [],
  books = [],
  initialMembers = [],
  disabled = false,
  onBusy,
  onChanged,
}: {
  session: Session;
  kind: 'firm' | 'company' | 'book';
  company?: AdminCompany;
  departments?: Department[];
  books?: Book[];
  initialMembers?: Member<CompanyRole>[];
  disabled?: boolean;
  onBusy: (value: boolean) => void;
  onChanged?: () => Promise<void>;
}) {
  const [members, setMembers] = useState<Member<string>[]>(
    kind === 'company' ? initialMembers : [],
  );
  const [bookId, setBookId] = useState(books[0]?.id ?? '');
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [create, setCreate] = useState(false);
  const [grant, setGrant] = useState(false);
  const [change, setChange] = useState<{
    member: Member<string>;
    role?: string;
    department?: string;
    remove?: boolean;
  }>();
  const [eligible, setEligible] = useState<Member<string>[]>([]);
  const [requestRevision, setRequestRevision] = useState(0);
  const [handoverFrom, setHandoverFrom] = useState<Member<string>>();
  const route =
    kind === 'firm'
      ? '/firm/members'
      : kind === 'company'
        ? `/companies/${encodeURIComponent(company!.id)}/members`
        : `/books/${encodeURIComponent(bookId)}/members`;
  const roleOptions =
    kind === 'firm'
      ? ['admin', 'operator', 'reviewer', 'viewer']
      : kind === 'company'
        ? ['admin', 'finance', 'employee']
        : ['editor', 'reviewer', 'viewer'];
  const lock = useRef(false);
  useEffect(() => {
    onBusy(busy);
    return () => onBusy(false);
  }, [busy, onBusy]);
  const reload = useCallback(
    async (signal?: AbortSignal) => {
      if (disabled || (kind === 'book' && !bookId)) return;
      const result = await api<{ members: Member<string>[] }>(route, session, { signal });
      if (!signal?.aborted) setMembers(result.members);
    },
    [disabled, kind, bookId, route, session],
  );
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError('');
    if (kind === 'book') setMembers([]);
    void reload(controller.signal)
      .catch((failure) => {
        if (!controller.signal.aborted) setError(errorMessage(failure));
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [reload, kind]);
  useEffect(() => {
    if (kind !== 'book' || disabled) return;
    const controller = new AbortController();
    const firmManager = session.role === 'owner' || session.role === 'admin';
    void Promise.all([
      api<{ members: Member<string>[] }>(
        `/companies/${encodeURIComponent(company!.id)}/members`,
        session,
        { signal: controller.signal },
      ),
      firmManager
        ? api<{ members: Member<string>[] }>('/firm/members', session, {
            signal: controller.signal,
          })
        : Promise.resolve({ members: [] as Member<string>[] }),
    ])
      .then(([clients, firm]) => {
        if (!controller.signal.aborted)
          setEligible([
            ...new Map(
              [
                ...clients.members.filter((member) =>
                  ['owner', 'admin', 'finance'].includes(member.role),
                ),
                ...firm.members,
              ].map((member) => [member.user_id, member]),
            ).values(),
          ]);
      })
      .catch((failure) => {
        if (!controller.signal.aborted) setError(errorMessage(failure));
      });
    return () => controller.abort();
  }, [kind, disabled, company, session]);
  async function perform(action: () => Promise<unknown>) {
    if (lock.current) return;
    lock.current = true;
    setBusy(true);
    setError('');
    setNotice('');
    try {
      await action();
      setChange(undefined);
      setGrant(false);
      await reload();
      await onChanged?.();
      setNotice('授权已保存，后续请求按最新权限执行。');
    } catch (failure) {
      setError(errorMessage(failure));
    } finally {
      lock.current = false;
      setBusy(false);
    }
  }
  return (
    <>
      <div className="admin-section-heading">
        <div>
          <h2>{kind === 'firm' ? '会计团队' : kind === 'book' ? '账套分工' : '企业人员'}</h2>
          <p>
            {kind === 'firm'
              ? '负责人和管理员自动覆盖本事务所全部企业。'
              : kind === 'book'
                ? '经办准备草稿，复核审核并导出；企业财务身份与账套授权共同决定权限。'
                : '企业权限只作用于当前公司；部门审批人请在“部门与审批”中指定。'}
          </p>
        </div>
        {!disabled && (
          <div className="admin-inline-actions">
            {kind !== 'book' && (
              <button
                className="button primary small"
                disabled={busy}
                onClick={() => setCreate(true)}
              >
                <UserPlus size={15} />
                开通账号
              </button>
            )}
            <button
              className="button secondary small"
              disabled={busy || (kind === 'book' && !bookId)}
              onClick={() => setGrant(true)}
            >
              <Plus size={15} />
              {kind === 'book' ? '分配成员' : '添加已有成员'}
            </button>
          </div>
        )}
      </div>
      {kind === 'book' && (
        <label className="admin-book-select">
          选择账期
          <select
            aria-label="分工账套"
            value={bookId}
            disabled={busy || disabled}
            onChange={(event) => setBookId(event.target.value)}
          >
            <option value="">选择账套</option>
            {books.map((book) => (
              <option key={book.id} value={book.id}>
                {book.name} · {book.period}
              </option>
            ))}
          </select>
        </label>
      )}
      {kind === 'book' && bookId && (
        <ReviewPolicyPanel
          session={session}
          bookId={bookId}
          disabled={disabled}
          onBusyChange={setBusy}
          onChanged={onChanged}
        />
      )}
      {error && (
        <p role="alert" className="form-error">
          {error}
        </p>
      )}
      {notice && (
        <p role="status" className="form-success">
          {notice}
        </p>
      )}
      {loading ? (
        <div className="loading-block">
          <Spinner />
          正在读取人员权限…
        </div>
      ) : members.length ? (
        <div className="admin-members">
          {members.map((member) => {
            const protectedMember = member.role === 'owner' || member.user_id === session.user_id;
            return (
              <div className="admin-member" key={member.user_id}>
                <span className="member-avatar">
                  {(member.name || member.email || '员').slice(0, 1)}
                </span>
                <div className="member-description">
                  <strong>{member.name || member.email}</strong>
                  <span>{member.email}</span>
                </div>
                <select
                  aria-label={`修改 ${member.email || member.name} 的权限`}
                  value={member.role}
                  disabled={busy || disabled || protectedMember}
                  onChange={(event) => setChange({ member, role: event.target.value })}
                >
                  {member.role === 'owner' && <option value="owner">负责人</option>}
                  {roleOptions.map((role) => (
                    <option key={role} value={role}>
                      {adminRoleLabels[role]}
                    </option>
                  ))}
                </select>
                {kind === 'company' && (
                  <select
                    aria-label={`修改 ${member.email || member.name} 的部门`}
                    value={member.department_id ?? ''}
                    disabled={busy || disabled || protectedMember}
                    onChange={(event) => setChange({ member, department: event.target.value })}
                  >
                    <option value="">未分配部门</option>
                    {departments.map((department) => (
                      <option key={department.id} value={department.id}>
                        {department.name}
                      </option>
                    ))}
                  </select>
                )}
                {kind === 'firm' && (
                  <button
                    className="icon-button"
                    aria-label={`工作交接 ${member.email || member.name}`}
                    disabled={
                      busy ||
                      disabled ||
                      member.role === 'owner' ||
                      member.user_id === session.user_id
                    }
                    onClick={() => {
                      setError('');
                      setNotice('');
                      setHandoverFrom(member);
                    }}
                  >
                    <ArrowLeftRight size={16} />
                  </button>
                )}
                <button
                  className="icon-button"
                  aria-label={`撤销 ${member.email || member.name} 的授权`}
                  disabled={busy || disabled || protectedMember}
                  onClick={() => setChange({ member, remove: true })}
                >
                  <Trash2 size={16} />
                </button>
              </div>
            );
          })}
        </div>
      ) : (
        <EmptyState title={kind === 'book' ? '此账套尚未分配成员' : '还没有成员'}>
          {disabled
            ? '恢复企业后可以继续管理授权。'
            : '开通新账号，或将已经注册的成员加入当前范围。'}
        </EmptyState>
      )}
      {kind !== 'book' && !disabled && (
        <AccountRequests
          key={requestRevision}
          session={session}
          companyId={company?.id}
          onBusy={setBusy}
          onCompleted={async () => {
            await reload();
            await onChanged?.();
          }}
        />
      )}
      {kind === 'company' && company && !disabled && (
        <BulkEmployeeInvites
          key={session.scope_key + ':' + company.id}
          session={session}
          companyId={company.id}
          departments={departments}
          onBusy={setBusy}
          onCompleted={() => setRequestRevision((value) => value + 1)}
        />
      )}
      {handoverFrom && (
        <MemberHandoverDialog
          session={session}
          from={handoverFrom}
          candidates={members.filter(
            (member) =>
              member.user_id !== handoverFrom.user_id &&
              ['owner', 'admin', 'operator', 'reviewer'].includes(member.role),
          )}
          onClose={() => setHandoverFrom(undefined)}
          onDone={() => {
            setNotice('工作交接已完成，账套分工与待处理任务按结果更新。');
            void reload();
          }}
        />
      )}
      {change && (
        <Modal
          title={change.remove ? '撤销成员授权' : '修改成员权限'}
          subtitle={change.member.email}
          busy={busy}
          error={error}
          onClose={() => setChange(undefined)}
        >
          <div className="admin-form">
            <p className="admin-info">
              {change.remove
                ? `仅移除此人员在${company?.name ?? session.firm_name}的授权；原始资料和操作历史继续保留。请先安排待办交接。`
                : `将更新此成员在${company?.name ?? session.firm_name}的${change.role ? `角色为「${adminRoleLabels[change.role]}」` : '部门归属'}。`}
            </p>
            <div className="admin-form-actions">
              <button
                className="button secondary"
                disabled={busy}
                onClick={() => setChange(undefined)}
              >
                取消
              </button>
              <button
                className="button primary"
                disabled={busy}
                onClick={() =>
                  void perform(() =>
                    api(
                      change.remove || kind === 'book'
                        ? `${route}/${encodeURIComponent(change.member.user_id)}`
                        : route,
                      session,
                      {
                        method: change.remove ? 'DELETE' : 'PUT',
                        ...(change.remove
                          ? {}
                          : {
                              body:
                                kind === 'book'
                                  ? { role: change.role ?? change.member.role }
                                  : {
                                      email: change.member.email,
                                      role: change.role ?? change.member.role,
                                      ...(kind === 'company'
                                        ? {
                                            department_id:
                                              change.department !== undefined
                                                ? change.department || null
                                                : (change.member.department_id ?? null),
                                          }
                                        : {}),
                                    },
                            }),
                      },
                    ),
                  )
                }
              >
                {busy && <Spinner />}确认{change.remove ? '撤销' : '修改'}
              </button>
            </div>
          </div>
        </Modal>
      )}
      {grant && (
        <GrantForm
          kind={kind}
          roles={roleOptions}
          departments={departments}
          eligible={eligible}
          busy={busy}
          error={error}
          onClose={() => setGrant(false)}
          onSave={(body) =>
            perform(() =>
              api(
                kind === 'book' ? `${route}/${encodeURIComponent(String(body.user_id))}` : route,
                session,
                { method: 'PUT', body: kind === 'book' ? { role: body.role } : body },
              ),
            )
          }
        />
      )}
      {create && (
        <AccountForm
          session={session}
          company={company}
          departments={departments}
          roles={roleOptions}
          onBusy={setBusy}
          onCompleted={async () => {
            await reload();
            await onChanged?.();
          }}
          onClose={() => {
            setCreate(false);
            setRequestRevision((value) => value + 1);
          }}
        />
      )}
    </>
  );
}

function GrantForm({
  kind,
  roles,
  departments,
  eligible,
  busy,
  error,
  onClose,
  onSave,
}: {
  kind: 'firm' | 'company' | 'book';
  roles: string[];
  departments: Department[];
  eligible: Member<string>[];
  busy: boolean;
  error: string;
  onClose: () => void;
  onSave: (body: Record<string, unknown>) => Promise<void>;
}) {
  const [email, setEmail] = useState('');
  const [userId, setUserId] = useState('');
  const [role, setRole] = useState(roles[0]);
  const [departmentId, setDepartmentId] = useState('');
  return (
    <Modal
      title={kind === 'book' ? '分配账套成员' : '添加已有成员'}
      busy={busy}
      error={error}
      onClose={onClose}
    >
      <form
        className="admin-form"
        onSubmit={(event) => {
          event.preventDefault();
          void onSave(
            kind === 'book'
              ? { user_id: userId, role }
              : {
                  email: email.trim(),
                  role,
                  ...(kind === 'company' ? { department_id: departmentId || null } : {}),
                },
          );
        }}
      >
        {kind === 'book' ? (
          <label>
            成员
            <select
              required
              value={userId}
              disabled={busy}
              onChange={(event) => setUserId(event.target.value)}
            >
              <option value="">选择已授权的财务或事务所成员</option>
              {eligible.map((member) => (
                <option key={member.user_id} value={member.user_id}>
                  {member.name} · {member.email}
                </option>
              ))}
            </select>
          </label>
        ) : (
          <>
            <label>
              成员登录邮箱
              <input
                type="email"
                required
                autoComplete="off"
                value={email}
                disabled={busy}
                onChange={(event) => setEmail(event.target.value)}
              />
            </label>
            <p className="field-help">
              适用于已经登录过本系统的账号；新成员请使用“开通账号”。已有账号继续使用原密码。
            </p>
          </>
        )}
        <label>
          权限角色
          <select
            aria-label="权限角色"
            value={role}
            disabled={busy}
            onChange={(event) => setRole(event.target.value)}
          >
            {roles.map((value) => (
              <option key={value} value={value}>
                {adminRoleLabels[value]}
              </option>
            ))}
          </select>
        </label>
        {kind === 'company' && (
          <label>
            所属部门
            <select
              aria-label="所属部门"
              value={departmentId}
              disabled={busy}
              onChange={(event) => setDepartmentId(event.target.value)}
            >
              <option value="">未分配部门</option>
              {departments.map((department) => (
                <option key={department.id} value={department.id}>
                  {department.name}
                </option>
              ))}
            </select>
          </label>
        )}
        <div className="admin-form-actions">
          <button type="button" className="button secondary" disabled={busy} onClick={onClose}>
            取消
          </button>
          <button
            className="button primary"
            disabled={busy || (kind === 'book' ? !userId : !email.trim())}
          >
            {busy && <Spinner />}保存授权
          </button>
        </div>
      </form>
    </Modal>
  );
}

function AccountRolePreview({
  role,
  company,
  submissionMode,
  hasDepartment,
}: {
  role: string;
  company: boolean;
  submissionMode?: SubmissionPolicy['mode'];
  hasDepartment?: boolean;
}) {
  const details: Record<string, { title: string; text: string }> = company
    ? {
        employee: {
          title:
            submissionMode === 'direct'
              ? '登录后：工作空间'
              : hasDepartment
                ? '登录后：我的工作台'
                : '分配部门后：我的工作台',
          text:
            submissionMode === 'direct'
              ? '此企业采用集中提交，普通员工不能提交业务事项。需要员工逐人填报时，请先在“部门与审批”配置部门审批流程。'
              : '录入本人报销、采购费用、日常收支，上传票据，跟进审批和补件。默认只能查看本人事项；部门审批权需在“部门与审批”另行指定。',
        },
        finance: {
          title: '登录后：财务工作台',
          text: '核验本企业业务与票据，处理财务待办并移交会计。凭证经办、复核及导出权限需在“账套分工”另行分配。',
        },
        admin: {
          title: '登录后：企业业务与管理工作空间',
          text: '管理本企业人员、部门和资料协作，首页按本人兼任的业务职责显示。企业管理员身份不自动获得其他企业权限，也不替代账套复核授权。',
        },
      }
    : {
        admin: { title: '登录后：会计工作台', text: '管理本事务所全部企业、人员和账套。' },
        operator: {
          title: '登录后：会计工作台',
          text: '在已分配账套中整理资料、准备分录草稿，交由复核会计审核。',
        },
        reviewer: {
          title: '登录后：会计工作台',
          text: '在已分配账套中处理凭证复核，审核通过后才可导出。',
        },
        viewer: { title: '登录后：工作空间', text: '查看已授权范围内的资料和办理进度。' },
      };
  const detail = details[role];
  return detail ? (
    <section className="admin-role-preview" aria-label="登录后的权限预览">
      <ShieldCheck size={17} />
      <div>
        <strong>{detail.title}</strong>
        <p>{detail.text}</p>
      </div>
    </section>
  ) : null;
}

function AccountDelivery({
  name,
  email,
  companyName,
  role,
  passwordMode,
  passwordAlreadyCreated = false,
}: {
  name: string;
  email: string;
  companyName?: string;
  role: string;
  passwordMode: boolean;
  passwordAlreadyCreated?: boolean;
}) {
  const [copied, setCopied] = useState(false);
  const loginUrl = `${window.location.origin}/`;
  const instruction = `${name}，你的衡账账号已开通。\n企业：${companyName ?? '已授权企业'}\n登录地址：${loginUrl}\n登录邮箱：${email}\n权限：${adminRoleLabels[role] ?? role}${passwordMode ? '\n初始密码请通过管理员单独交付的信息获取。' : '\n请继续使用原账号密码。'}`;
  return (
    <section className="admin-account-delivery" aria-label="账号开通结果">
      <h3>账号授权已完成</h3>
      <p role="status">
        {passwordMode
          ? passwordAlreadyCreated
            ? '账号已在此前创建，仍使用首次设置的初始密码；本次输入未覆盖密码。'
            : '员工现在可以使用登录邮箱和初始密码登录。密码已从本表单清空，不会再次显示。'
          : '已有账号继续使用原密码，当前范围的授权已生效。'}
      </p>
      <dl>
        <div>
          <dt>登录地址</dt>
          <dd>
            <a href={loginUrl} target="_blank" rel="noreferrer">
              {loginUrl}
            </a>
          </dd>
        </div>
        <div>
          <dt>登录邮箱</dt>
          <dd>{email}</dd>
        </div>
        <div>
          <dt>权限角色</dt>
          <dd>{adminRoleLabels[role] ?? role}</dd>
        </div>
      </dl>
      <button
        type="button"
        className="button secondary"
        onClick={() => {
          void navigator.clipboard
            .writeText(instruction)
            .then(() => setCopied(true))
            .catch(() => setCopied(false));
        }}
      >
        {copied ? '已复制登录说明' : '复制登录说明（不含密码）'}
      </button>
    </section>
  );
}

function AccountForm({
  session,
  company,
  departments,
  roles,
  onBusy,
  onCompleted,
  onClose,
}: {
  session: Session;
  company?: AdminCompany;
  departments: Department[];
  roles: string[];
  onBusy: (busy: boolean) => void;
  onCompleted: () => Promise<void>;
  onClose: () => void;
}) {
  const [name, setName] = useState('');
  const [email, setEmail] = useState('');
  const [role, setRole] = useState(company ? 'employee' : 'operator');
  const [departmentId, setDepartmentId] = useState('');
  const [mode, setMode] = useState<'activation' | 'password'>(company ? 'password' : 'activation');
  const [password, setPassword] = useState('');
  const [confirmation, setConfirmation] = useState('');
  const [key] = useState(() => crypto.randomUUID());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [result, setResult] = useState<{
    request: AccountRequest;
    activation_url?: string;
    password_already_created?: boolean;
  }>();
  const [policy, setPolicy] = useState<SubmissionPolicy>();
  const [policyError, setPolicyError] = useState('');
  const [policyRevision, setPolicyRevision] = useState(0);
  const departmentRequired =
    mode === 'password' && role === 'employee' && policy?.mode === 'department';
  const submitting = useRef(false);
  useEffect(() => () => onBusy(false), [onBusy]);
  useEffect(() => {
    if (!company?.id) return;
    const controller = new AbortController();
    setPolicy(undefined);
    setPolicyError('');
    void api<SubmissionPolicy>(
      `/companies/${encodeURIComponent(company.id)}/submission-policy`,
      session,
      { signal: controller.signal },
    )
      .then((value) => {
        if (!controller.signal.aborted) setPolicy(value);
      })
      .catch((failure) => {
        if (!controller.signal.aborted) setPolicyError(errorMessage(failure));
      });
    return () => controller.abort();
  }, [company?.id, session, policyRevision]);
  async function submit() {
    if (submitting.current) return;
    if (company && mode === 'password' && !policy) return;
    if (departmentRequired && !departmentId) {
      setError('此企业使用部门审批，请先选择员工所属部门。');
      return;
    }
    if (mode === 'password' && (password.length < 12 || password.length > 128)) {
      setError('初始密码需为 12–128 个字符。');
      return;
    }
    if (mode === 'password' && password !== confirmation) {
      setError('两次输入的初始密码不一致。');
      return;
    }
    submitting.current = true;
    setBusy(true);
    onBusy(true);
    setError('');
    try {
      const value = await api<NonNullable<typeof result>>('/admin/account-requests', session, {
        method: 'POST',
        body: {
          idempotency_key: key,
          name: name.trim(),
          email: email.trim(),
          provisioning_mode: mode,
          ...(mode === 'password' ? { initial_password: password } : {}),
          ...(company
            ? { company_id: company.id, company_role: role, department_id: departmentId || null }
            : { firm_role: role }),
        },
      });
      setResult(value);
      if (value.request.status === 'failed')
        setError(
          value.request.error || value.request.last_error || '开通未完成，请在开通记录中重试。',
        );
      if (value.request.status === 'completed')
        await onCompleted().catch(() =>
          setError('账号授权已完成，人员列表暂未刷新。关闭弹窗后请刷新企业详情。'),
        );
    } catch (failure) {
      setError(errorMessage(failure));
    } finally {
      setPassword('');
      setConfirmation('');
      submitting.current = false;
      setBusy(false);
      onBusy(false);
    }
  }
  return (
    <Modal
      title="开通账号"
      subtitle={company?.name ?? session.firm_name}
      busy={busy}
      error={error}
      onClose={onClose}
    >
      {result ? (
        <div className="admin-form">
          {result.request.status === 'completed' ? (
            <AccountDelivery
              name={name.trim()}
              email={email.trim()}
              companyName={company?.name ?? session.firm_name}
              passwordMode={mode === 'password'}
              passwordAlreadyCreated={result.password_already_created}
              role={role}
            />
          ) : result.activation_url ? (
            <InvitationDelivery value={result.activation_url} session={session} />
          ) : (
            <p role="status">
              {result.request.status === 'failed'
                ? '开通记录已保留，可以关闭后重试。'
                : '开通请求已保存，请在下方记录中查看进度。'}
            </p>
          )}
          <button className="button primary" onClick={onClose}>
            完成
          </button>
        </div>
      ) : (
        <form
          className="admin-form"
          onSubmit={(event) => {
            event.preventDefault();
            void submit();
          }}
        >
          {company && (
            <fieldset className="admin-account-modes" disabled={busy}>
              <legend>开通方式</legend>
              {[
                ['password', '设置初始密码', '开通后即可用邮箱和密码登录'],
                ['activation', '本人激活', '生成链接，由本人设置密码'],
              ].map(([value, title, description]) => (
                <label key={value}>
                  <input
                    type="radio"
                    name="account-provisioning-mode"
                    value={value}
                    checked={mode === value}
                    onChange={() => {
                      setMode(value as 'activation' | 'password');
                      setPassword('');
                      setConfirmation('');
                      setError('');
                    }}
                  />
                  <span>
                    <strong>{title}</strong>
                    <small>{description}</small>
                  </span>
                </label>
              ))}
            </fieldset>
          )}
          {mode === 'activation' && (
            <p className="admin-info">
              <ShieldCheck size={16} />
              由成员本人激活并设置密码。已有账号继续使用原身份，仅新增当前范围的授权。
            </p>
          )}
          <label>
            姓名
            <input
              required
              maxLength={80}
              value={name}
              disabled={busy}
              onChange={(event) => setName(event.target.value)}
            />
          </label>
          {mode === 'password' && (
            <>
              <label>
                初始密码
                <input
                  type="password"
                  autoComplete="new-password"
                  required
                  minLength={12}
                  maxLength={128}
                  value={password}
                  disabled={busy}
                  aria-describedby="initial-password-help"
                  onChange={(event) => setPassword(event.target.value)}
                />
              </label>
              <label>
                再次输入初始密码
                <input
                  type="password"
                  autoComplete="new-password"
                  required
                  minLength={12}
                  maxLength={128}
                  value={confirmation}
                  disabled={busy}
                  onChange={(event) => setConfirmation(event.target.value)}
                />
              </label>
              <p className="field-help" id="initial-password-help">
                使用 12–128
                个字符。提交后密码立即清空，请提前记录并单独交给本人；已有账号请用“添加已有成员”，不会覆盖原密码。
              </p>
            </>
          )}
          <label>
            登录邮箱
            <input
              type="email"
              autoComplete="off"
              required
              value={email}
              disabled={busy}
              onChange={(event) => setEmail(event.target.value)}
            />
          </label>
          <label>
            权限角色
            <select
              aria-label="权限角色"
              value={role}
              disabled={busy}
              onChange={(event) => setRole(event.target.value)}
            >
              {roles.map((value) => (
                <option key={value} value={value}>
                  {adminRoleLabels[value]}
                </option>
              ))}
            </select>
          </label>
          <AccountRolePreview
            role={role}
            company={!!company}
            submissionMode={policy?.mode}
            hasDepartment={!!departmentId}
          />
          {company && (
            <label>
              所属部门
              <select
                aria-label="所属部门"
                value={departmentId}
                disabled={busy}
                required={departmentRequired}
                onChange={(event) => setDepartmentId(event.target.value)}
              >
                <option value="">未分配部门</option>
                {departments.map((department) => (
                  <option key={department.id} value={department.id}>
                    {department.name}
                  </option>
                ))}
              </select>
            </label>
          )}
          {company && mode === 'password' && !policy && (
            <p className="field-help" role={policyError ? 'alert' : 'status'}>
              {policyError ? (
                <>
                  无法确认企业提交流程：{policyError}{' '}
                  <button
                    type="button"
                    className="text-button"
                    onClick={() => setPolicyRevision((value) => value + 1)}
                  >
                    重新读取
                  </button>
                </>
              ) : (
                '正在确认企业的资料提交流程…'
              )}
            </p>
          )}
          {departmentRequired && (
            <p className="field-help">
              {departments.length
                ? '请选择所属部门。部门审批人需在“部门与审批”中单独指定，员工不能审批自己的事项。'
                : '此企业尚无部门。请先关闭弹窗，到“部门与审批”新建部门并指定审批人，再开通员工账号。'}
            </p>
          )}
          <div className="admin-form-actions">
            <button className="button secondary" type="button" disabled={busy} onClick={onClose}>
              取消
            </button>
            <button
              className="button primary"
              disabled={
                busy ||
                !name.trim() ||
                !email.trim() ||
                (mode === 'password' &&
                  (!password ||
                    !confirmation ||
                    (!!company && !policy) ||
                    (departmentRequired && !departmentId)))
              }
            >
              {busy && <Spinner />}
              {mode === 'password' ? '创建账号并授权' : '生成激活链接'}
            </button>
          </div>
        </form>
      )}
    </Modal>
  );
}

function AccountRequests({
  session,
  companyId,
  onBusy,
  onCompleted,
}: {
  session: Session;
  companyId?: string;
  onBusy: (busy: boolean) => void;
  onCompleted: () => Promise<void>;
}) {
  const [requests, setRequests] = useState<AccountRequest[]>([]);
  const [enabled, setEnabled] = useState(true);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [link, setLink] = useState('');
  const [cancelTarget, setCancelTarget] = useState<AccountRequest>();
  const [retryTarget, setRetryTarget] = useState<AccountRequest>();
  const [retryPassword, setRetryPassword] = useState('');
  const [retryConfirmation, setRetryConfirmation] = useState('');
  const [completed, setCompleted] = useState<{
    request: AccountRequest;
    passwordAlreadyCreated: boolean;
  }>();
  const retryLock = useRef(false);
  const reload = useCallback(
    async (signal?: AbortSignal) => {
      const data = await api<{ requests: AccountRequest[]; provisioning_enabled: boolean }>(
        `/admin/account-requests${companyId ? `?company_id=${encodeURIComponent(companyId)}` : ''}`,
        session,
        { signal },
      );
      if (!signal?.aborted) {
        setRequests(data.requests);
        setEnabled(data.provisioning_enabled);
      }
    },
    [session, companyId],
  );
  useEffect(() => {
    const controller = new AbortController();
    void reload(controller.signal).catch((failure) => {
      if (!controller.signal.aborted) setError(errorMessage(failure));
    });
    return () => controller.abort();
  }, [reload]);
  useEffect(() => () => onBusy(false), [onBusy]);
  async function retry(id: string, initialPassword?: string) {
    if (retryLock.current) return;
    if (
      initialPassword !== undefined &&
      (initialPassword.length < 12 || initialPassword.length > 128)
    ) {
      setError('初始密码需为 12–128 个字符。');
      return;
    }
    if (initialPassword !== undefined && initialPassword !== retryConfirmation) {
      setError('两次输入的初始密码不一致。');
      return;
    }
    retryLock.current = true;
    setBusy(true);
    onBusy(true);
    setError('');
    try {
      const data = await api<{
        request: AccountRequest;
        activation_url?: string;
        password_already_created?: boolean;
      }>(`/admin/account-requests/${encodeURIComponent(id)}/retry`, session, {
        method: 'POST',
        body: initialPassword === undefined ? {} : { initial_password: initialPassword },
      });
      setLink(data.request.status === 'completed' ? '' : (data.activation_url ?? ''));
      if (data.request.status === 'failed')
        setError(data.request.error || data.request.last_error || '开通未完成，请稍后重试。');
      else setRetryTarget(undefined);
      if (data.request.status === 'completed') {
        setCompleted({
          request: data.request,
          passwordAlreadyCreated: data.password_already_created === true,
        });
        await onCompleted().catch(() =>
          setError('账号授权已完成，人员列表暂未刷新，请刷新企业详情。'),
        );
      }
      await reload();
    } catch (failure) {
      setError(errorMessage(failure));
    } finally {
      setRetryPassword('');
      setRetryConfirmation('');
      retryLock.current = false;
      setBusy(false);
      onBusy(false);
    }
  }
  const labels: Record<string, string> = {
    pending: '处理中',
    awaiting_activation: '待本人激活',
    completed: '已完成',
    failed: '开通失败',
    cancelled: '已撤销',
  };
  return (
    <section className="admin-provisions">
      <h3>账号开通记录</h3>
      {!enabled && (
        <p className="admin-info">
          当前环境尚未配置账号开通服务。管理员可先添加已有成员；完成服务端配置后即可创建新账号或生成激活链接。
        </p>
      )}
      {error && (
        <p className="form-error" role="alert">
          {error}
        </p>
      )}
      {requests.length ? (
        requests.map((request) => (
          <div className="admin-request" key={request.id}>
            <div>
              <strong>{request.name}</strong>
              <span>{request.email}</span>
              <small>
                {request.provisioning_mode === 'password' ? '管理员设置初始密码' : '本人激活'}
              </small>
            </div>
            <span>
              {request.activation_expired
                ? '激活链接已过期，请重新生成'
                : (labels[request.status] ?? request.status)}
              {request.status === 'awaiting_activation' && !!request.expires_at && (
                <small>有效至 {new Date(request.expires_at * 1000).toLocaleString('zh-CN')}</small>
              )}
              {request.invitation_delivery && (
                <small>
                  {request.invitation_delivery.current_link ? '本版链接：' : '旧版链接：'}
                  {request.invitation_delivery.state === 'accepted'
                    ? '邮件通道已接受，不代表本人激活'
                    : request.invitation_delivery.state === 'not_sent'
                      ? '确认未发送'
                      : '发送结果未核定，不自动重发'}
                </small>
              )}
            </span>
            {!['completed', 'cancelled'].includes(request.status) && (
              <div className="admin-inline-actions">
                <button
                  className="button secondary small"
                  disabled={busy || !enabled}
                  onClick={() => {
                    if (request.provisioning_mode === 'password') {
                      setError('');
                      setRetryPassword('');
                      setRetryConfirmation('');
                      setRetryTarget(request);
                    } else void retry(request.id);
                  }}
                >
                  {busy
                    ? '处理中…'
                    : request.provisioning_mode === 'password' || request.status === 'failed'
                      ? '重试开通'
                      : '重新生成链接'}
                </button>
                <button
                  className="text-button"
                  disabled={busy}
                  onClick={() => setCancelTarget(request)}
                >
                  {request.provisioning_mode === 'password' ? '撤销开通' : '撤销邀请'}
                </button>
              </div>
            )}
          </div>
        ))
      ) : (
        <p className="field-help">暂无开通记录。</p>
      )}
      {retryTarget && (
        <Modal
          title="重试密码开户"
          subtitle={retryTarget.email}
          busy={busy}
          error={error}
          onClose={() => {
            setRetryTarget(undefined);
            setRetryPassword('');
            setRetryConfirmation('');
            setError('');
          }}
        >
          <form
            className="admin-form"
            onSubmit={(event) => {
              event.preventDefault();
              void retry(retryTarget.id, retryPassword);
            }}
          >
            <p className="admin-info">
              请重新输入初始密码。若此前已成功创建账号，仍沿用首次创建时的密码，本次输入不会覆盖；本次只继续完成尚未完成的授权。
            </p>
            <label>
              初始密码
              <input
                type="password"
                autoComplete="new-password"
                required
                minLength={12}
                maxLength={128}
                value={retryPassword}
                disabled={busy}
                onChange={(event) => setRetryPassword(event.target.value)}
              />
            </label>
            <label>
              再次输入初始密码
              <input
                type="password"
                autoComplete="new-password"
                required
                minLength={12}
                maxLength={128}
                value={retryConfirmation}
                disabled={busy}
                onChange={(event) => setRetryConfirmation(event.target.value)}
              />
            </label>
            <div className="admin-form-actions">
              <button
                className="button primary"
                disabled={busy || !retryPassword || !retryConfirmation}
              >
                {busy && <Spinner />}重试开通
              </button>
            </div>
          </form>
        </Modal>
      )}
      {completed && (
        <Modal title="账号授权已完成" onClose={() => setCompleted(undefined)}>
          <div className="admin-form">
            <AccountDelivery
              name={completed.request.name}
              email={completed.request.email}
              role={completed.request.company_role ?? completed.request.firm_role ?? ''}
              passwordMode={completed.request.provisioning_mode === 'password'}
              passwordAlreadyCreated={completed.passwordAlreadyCreated}
            />
            <button className="button primary" onClick={() => setCompleted(undefined)}>
              完成
            </button>
          </div>
        </Modal>
      )}
      {link && (
        <Modal title="新的账号激活链接" onClose={() => setLink('')}>
          <div className="admin-form">
            <InvitationDelivery value={link} session={session} />
            <button className="button primary" onClick={() => setLink('')}>
              完成
            </button>
          </div>
        </Modal>
      )}
      {cancelTarget && (
        <Modal
          title={cancelTarget.provisioning_mode === 'password' ? '撤销账号开通' : '撤销账号邀请'}
          subtitle={cancelTarget.email}
          busy={busy}
          error={error}
          onClose={() => setCancelTarget(undefined)}
        >
          <div className="admin-form">
            <p className="admin-info">
              撤销后不再继续此开通请求。已经创建的账号、已有授权和其他企业权限继续保留。
            </p>
            <div className="admin-form-actions">
              <button
                className="button secondary"
                disabled={busy}
                onClick={() => setCancelTarget(undefined)}
              >
                取消
              </button>
              <button
                className="button primary"
                disabled={busy}
                onClick={() => {
                  setBusy(true);
                  onBusy(true);
                  setError('');
                  void api(
                    `/admin/account-requests/${encodeURIComponent(cancelTarget.id)}/cancel`,
                    session,
                    { method: 'POST', body: {} },
                  )
                    .then(async () => {
                      setCancelTarget(undefined);
                      setLink('');
                      await reload();
                    })
                    .catch((failure) => setError(errorMessage(failure)))
                    .finally(() => {
                      setBusy(false);
                      onBusy(false);
                    });
                }}
              >
                {cancelTarget.provisioning_mode === 'password' ? '确认撤销开通' : '确认撤销邀请'}
              </button>
            </div>
          </div>
        </Modal>
      )}
    </section>
  );
}
