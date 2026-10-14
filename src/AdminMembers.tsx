import { useCallback, useEffect, useRef, useState } from 'react';
import { Plus, ShieldCheck, Trash2, UserPlus } from 'lucide-react';
import { api, errorMessage } from './api';
import { EmptyState, Modal, Spinner } from './AppPrimitives';
import type { Book, CompanyRole, Member, Session } from './types';
import type { Department } from './business-types';
import { adminRoleLabels, type AccountRequest, type AdminCompany } from './admin-types';

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
              适用于已经登录过本系统的账号；新成员请使用“开通账号”，由本人激活。
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

function ActivationLink({ value }: { value: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <section className="admin-activation-link">
      <h3>将激活链接交给本人</h3>
      <p>链接包含一次性凭据，仅在本次操作后显示。不会发送邮件；请通过你信任的方式交给账号本人。</p>
      <textarea aria-label="账号激活链接" readOnly value={value} spellCheck={false} />
      <button
        className="button secondary"
        onClick={() => {
          void navigator.clipboard
            .writeText(value)
            .then(() => setCopied(true))
            .catch(() => setCopied(false));
        }}
      >
        {copied ? '已复制' : '复制激活链接'}
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
  onClose,
}: {
  session: Session;
  company?: AdminCompany;
  departments: Department[];
  roles: string[];
  onBusy: (busy: boolean) => void;
  onClose: () => void;
}) {
  const [name, setName] = useState('');
  const [email, setEmail] = useState('');
  const [role, setRole] = useState(company ? 'employee' : 'operator');
  const [departmentId, setDepartmentId] = useState('');
  const [key] = useState(() => crypto.randomUUID());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [result, setResult] = useState<{ request: AccountRequest; activation_url?: string }>();
  useEffect(() => () => onBusy(false), [onBusy]);
  async function submit() {
    if (busy) return;
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
    } catch (failure) {
      setError(errorMessage(failure));
    } finally {
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
          {result.activation_url && result.request.status !== 'completed' ? (
            <ActivationLink value={result.activation_url} />
          ) : (
            <p role="status">
              {result.request.status === 'completed'
                ? '账号授权已完成。'
                : result.request.status === 'failed'
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
          <p className="admin-info">
            <ShieldCheck size={16} />
            由成员本人激活并设置密码。已有账号继续使用原身份，仅新增当前范围的授权。
          </p>
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
          {company && (
            <label>
              所属部门
              <select
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
            <button className="button secondary" type="button" disabled={busy} onClick={onClose}>
              取消
            </button>
            <button className="button primary" disabled={busy || !name.trim() || !email.trim()}>
              {busy && <Spinner />}生成激活链接
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
}: {
  session: Session;
  companyId?: string;
  onBusy: (busy: boolean) => void;
}) {
  const [requests, setRequests] = useState<AccountRequest[]>([]);
  const [enabled, setEnabled] = useState(true);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [link, setLink] = useState('');
  const [cancelTarget, setCancelTarget] = useState<AccountRequest>();
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
  async function retry(id: string) {
    if (busy) return;
    setBusy(true);
    onBusy(true);
    setError('');
    try {
      const data = await api<{ request: AccountRequest; activation_url?: string }>(
        `/admin/account-requests/${encodeURIComponent(id)}/retry`,
        session,
        { method: 'POST', body: {} },
      );
      setLink(data.request.status === 'completed' ? '' : (data.activation_url ?? ''));
      if (data.request.status === 'failed')
        setError(data.request.error || data.request.last_error || '开通未完成，请稍后重试。');
      await reload();
    } catch (failure) {
      setError(errorMessage(failure));
    } finally {
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
          当前环境尚未配置账号开通服务。管理员可先添加已有成员；完成服务端配置后即可生成激活链接。
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
            </div>
            <span>{labels[request.status] ?? request.status}</span>
            {!['completed', 'cancelled'].includes(request.status) && (
              <div className="admin-inline-actions">
                <button
                  className="button secondary small"
                  disabled={busy || !enabled}
                  onClick={() => void retry(request.id)}
                >
                  {busy ? '处理中…' : request.status === 'failed' ? '重试开通' : '重新生成链接'}
                </button>
                <button
                  className="text-button"
                  disabled={busy}
                  onClick={() => setCancelTarget(request)}
                >
                  撤销邀请
                </button>
              </div>
            )}
          </div>
        ))
      ) : (
        <p className="field-help">暂无开通记录。</p>
      )}
      {link && (
        <Modal title="新的账号激活链接" onClose={() => setLink('')}>
          <div className="admin-form">
            <ActivationLink value={link} />
            <button className="button primary" onClick={() => setLink('')}>
              完成
            </button>
          </div>
        </Modal>
      )}
      {cancelTarget && (
        <Modal
          title="撤销账号邀请"
          subtitle={cancelTarget.email}
          busy={busy}
          error={error}
          onClose={() => setCancelTarget(undefined)}
        >
          <div className="admin-form">
            <p className="admin-info">
              撤销后此邀请不能再用于获得授权。已有账号及其他企业权限继续保留。
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
                确认撤销邀请
              </button>
            </div>
          </div>
        </Modal>
      )}
    </section>
  );
}
