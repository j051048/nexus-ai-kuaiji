import { lazy, Suspense, useCallback, useEffect, useState } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import {
  ArrowLeft,
  ArrowUpRight,
  Building2,
  ChevronRight,
  History,
  LogOut,
  Plus,
  RefreshCw,
  Search,
  ShieldCheck,
  Users,
} from 'lucide-react';
import { api, errorMessage } from './api';
import { EmptyState, Modal, PageControls, Spinner } from './AppPrimitives';
import { BusinessOrganization } from './BusinessOrganization';
import { AdminMembers } from './AdminMembers';
import { AdminAudit } from './AdminAudit';
import type { Me, Session } from './types';
import {
  canOpenAdmin,
  companyStatusLabels,
  type AdminCompany,
  type AdminCompanyDetail,
  type CompanyStatus,
} from './admin-types';
import './admin.css';

const AdminAIControls = lazy(() => import('./AdminAIControls'));
const AdminOnboarding = lazy(() => import('./AdminOnboarding'));

export default function AdminWorkspace({
  session,
  me,
  loading,
  onLogout,
  onSwitchFirm,
  onBusyChange,
}: {
  session: Session;
  me: Me;
  loading: boolean;
  onLogout: () => Promise<void>;
  onSwitchFirm: (id: string) => Promise<void>;
  onBusyChange: (busy: boolean) => void;
}) {
  const navigate = useNavigate();
  const location = useLocation();
  const companyId = location.pathname.match(/^\/admin\/organizations\/([^/]+)\/?$/)?.[1] ?? '';
  const section =
    location.pathname === '/admin/members'
      ? 'members'
      : location.pathname === '/admin/audit'
        ? 'audit'
        : 'companies';
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    onBusyChange(busy);
    return () => onBusyChange(false);
  }, [busy, onBusyChange]);
  const allowed = canOpenAdmin(me.permissions) && !!session.firm_id;
  const go = (path: string) => {
    if (!busy) void navigate(path);
  };
  return (
    <div className="admin-shell">
      <header className="admin-header">
        <button
          className="admin-brand"
          disabled={busy}
          onClick={() => go('/admin')}
          aria-label="管理后台首页"
        >
          <span className="admin-brand-mark">衡</span>
          <span>
            衡账 <small>组织管理</small>
          </span>
        </button>
        <div className="admin-header-actions">
          {me.firms.length > 1 ? (
            <select
              aria-label="切换管理事务所"
              value={session.firm_id ?? ''}
              disabled={busy || loading}
              onChange={(event) => {
                void navigate('/admin', { replace: true });
                void onSwitchFirm(event.target.value);
              }}
            >
              {me.firms.map((firm) => (
                <option key={firm.id} value={firm.id}>
                  {firm.name}
                </option>
              ))}
            </select>
          ) : (
            <span className="admin-firm-name">{session.firm_name}</span>
          )}
          <button
            className="button secondary small"
            disabled={busy}
            onClick={() => go('/workspace/home')}
          >
            <ArrowUpRight size={15} />
            会计工作台
          </button>
          <button
            className="icon-button"
            aria-label="退出登录"
            disabled={busy || loading}
            onClick={() => void onLogout()}
          >
            <LogOut size={17} />
          </button>
        </div>
      </header>
      {!allowed ? (
        <main className="admin-main">
          <EmptyState title="当前账号没有管理后台权限" icon={<ShieldCheck size={28} />}>
            请联系本事务所负责人授权。你仍可返回本人获授权的工作台。
          </EmptyState>
          <button className="button secondary" onClick={() => go('/workspace/home')}>
            返回工作台
          </button>
        </main>
      ) : (
        <>
          <nav className="admin-nav" aria-label="管理后台导航">
            <button
              className={section === 'companies' ? 'active' : ''}
              disabled={busy}
              onClick={() => go('/admin')}
            >
              <Building2 size={16} />
              企业管理
            </button>
            {me.permissions.manage_firm && (
              <button
                className={section === 'members' ? 'active' : ''}
                disabled={busy}
                onClick={() => go('/admin/members')}
              >
                <Users size={16} />
                事务所人员
              </button>
            )}
            <button
              className={section === 'audit' ? 'active' : ''}
              disabled={busy}
              onClick={() => go('/admin/audit')}
            >
              <History size={16} />
              操作记录
            </button>
            <span>
              <ShieldCheck size={14} />
              {session.user_name} ·{' '}
              {me.permissions.manage_firm ? '管理本事务所全部企业' : '管理获授权企业'}
            </span>
          </nav>
          <main className="admin-main">
            {section === 'members' && me.permissions.manage_firm ? (
              <>
                <AdminHeading
                  title="事务所人员"
                  description="负责人和管理员管理全部企业；经办、复核和只读成员需要分配具体账套。"
                />
                <AdminMembers
                  key={`firm:${session.scope_key}`}
                  session={session}
                  kind="firm"
                  onBusy={setBusy}
                />
              </>
            ) : section === 'audit' ? (
              <>
                <AdminHeading
                  title="操作记录"
                  description="记录企业、人员和授权的变更，所有操作保留实际执行人。"
                />
                <AdminAudit session={session} />
              </>
            ) : companyId ? (
              <CompanyDetail
                key={companyId}
                session={session}
                companyId={companyId}
                firmManager={me.permissions.manage_firm === true}
                onBusy={setBusy}
                onBack={() => go('/admin')}
                onWork={() => go(`/workspace/home?company=${encodeURIComponent(companyId)}`)}
              />
            ) : (
              <CompanyList
                session={session}
                canCreate={me.permissions.create_company}
                onBusy={setBusy}
                onOpen={(id) => go(`/admin/organizations/${encodeURIComponent(id)}`)}
              />
            )}
          </main>
        </>
      )}
      <footer className="admin-footer">
        企业权限独立校验 · 原始资料与操作历史保留 · 会计审核后导出
      </footer>
    </div>
  );
}

function AdminHeading({
  title,
  description,
  action,
}: {
  title: string;
  description: string;
  action?: React.ReactNode;
}) {
  return (
    <div className="admin-heading">
      <div>
        <p className="eyebrow">ADMINISTRATION</p>
        <h1>{title}</h1>
        <p>{description}</p>
      </div>
      {action}
    </div>
  );
}

function CompanyList({
  session,
  canCreate,
  onBusy,
  onOpen,
}: {
  session: Session;
  canCreate: boolean;
  onBusy: (busy: boolean) => void;
  onOpen: (id: string) => void;
}) {
  const [search, setSearch] = useState('');
  const [query, setQuery] = useState('');
  const [status, setStatus] = useState('');
  const [offset, setOffset] = useState(0);
  const [revision, setRevision] = useState(0);
  const [data, setData] = useState<{
    companies: AdminCompany[];
    total: number;
    limit: number;
    offset: number;
  }>();
  const [error, setError] = useState('');
  const [create, setCreate] = useState(false);
  const [createdName, setCreatedName] = useState('');
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError('');
    setData(undefined);
    const params = new URLSearchParams({
      search: query,
      ...(status ? { status } : {}),
      limit: '25',
      offset: String(offset),
    });
    void api<NonNullable<typeof data>>(`/admin/companies?${params}`, session, {
      signal: controller.signal,
    })
      .then((value) => {
        if (!controller.signal.aborted) setData(value);
      })
      .catch((failure) => {
        if (!controller.signal.aborted) setError(errorMessage(failure));
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [session, query, status, offset, revision]);
  return (
    <>
      <AdminHeading
        title="企业管理"
        description="从一家企业开始，清晰管理每个人的职责与访问范围。"
        action={
          canCreate && (
            <button className="button primary" onClick={() => setCreate(true)}>
              <Plus size={16} />
              新建企业
            </button>
          )
        }
      />
      <form
        className="admin-filters"
        onSubmit={(event) => {
          event.preventDefault();
          setQuery(search.trim());
          setOffset(0);
        }}
      >
        <label className="admin-search">
          <Search size={17} />
          <input
            aria-label="搜索企业"
            placeholder="搜索企业名称或税号"
            value={search}
            maxLength={120}
            onChange={(event) => setSearch(event.target.value)}
          />
        </label>
        <select
          aria-label="企业状态"
          value={status}
          onChange={(event) => {
            setStatus(event.target.value);
            setOffset(0);
          }}
        >
          <option value="">全部状态</option>
          {Object.entries(companyStatusLabels).map(([value, label]) => (
            <option key={value} value={value}>
              {label}
            </option>
          ))}
        </select>
        <button className="button secondary" type="submit">
          查询
        </button>
        <button
          className="icon-button"
          type="button"
          aria-label="刷新企业列表"
          onClick={() => setRevision((value) => value + 1)}
        >
          <RefreshCw size={16} />
        </button>
      </form>
      {createdName && (
        <p className="form-success" role="status">
          「{createdName}」已创建，点击企业进入人员与权限配置。
        </p>
      )}
      {error && (
        <p className="form-error" role="alert">
          {error}
        </p>
      )}
      {loading ? (
        <div className="loading-block">
          <Spinner />
          正在读取企业…
        </div>
      ) : data?.companies.length ? (
        <div className="admin-company-list">
          {data.companies.map((company) => (
            <button
              className="admin-company-card"
              key={company.id}
              onClick={() => onOpen(company.id)}
            >
              <span className="admin-company-icon">
                <Building2 size={23} />
              </span>
              <span className="admin-company-name">
                <strong>{company.name}</strong>
                <span>{company.tax_id || '尚未填写税号'}</span>
              </span>
              <span className={`admin-status ${company.status}`}>
                {companyStatusLabels[company.status]}
              </span>
              <span className="admin-company-count">
                {company.member_count} 位成员 · {company.book_count} 个账期
              </span>
              <ChevronRight size={18} />
            </button>
          ))}
        </div>
      ) : (
        !error && (
          <EmptyState title="没有符合条件的企业">
            {canCreate
              ? '新建企业后，可以开通员工账号并分配账套权限。'
              : '当前没有可管理企业，请联系事务所负责人。'}
          </EmptyState>
        )
      )}
      {data && (
        <PageControls
          label="企业"
          busy={loading}
          page={{ ...data, has_more: data.offset + data.limit < data.total }}
          onPage={setOffset}
        />
      )}
      {create && (
        <CompanyForm
          session={session}
          onBusy={onBusy}
          onClose={() => setCreate(false)}
          onSaved={(company) => {
            setCreate(false);
            setCreatedName(company.name);
            setSearch(company.name);
            setQuery(company.name);
            setStatus('');
            setOffset(0);
            setRevision((value) => value + 1);
          }}
        />
      )}
    </>
  );
}

function CompanyDetail({
  session,
  companyId,
  firmManager,
  onBusy,
  onBack,
  onWork,
}: {
  session: Session;
  companyId: string;
  firmManager: boolean;
  onBusy: (busy: boolean) => void;
  onBack: () => void;
  onWork: () => void;
}) {
  const location = useLocation();
  const navigate = useNavigate();
  const requestedTab = new URLSearchParams(location.search).get('tab') ?? 'members';
  const initialTab = ['setup', 'members', 'departments', 'books', 'ai', 'audit'].includes(
    requestedTab,
  )
    ? requestedTab
    : 'members';
  const [detail, setDetail] = useState<AdminCompanyDetail>();
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [tab, setTab] = useState(initialTab);
  useEffect(() => setTab(initialTab), [initialTab, companyId]);
  function changeTab(value: string) {
    setTab(value);
    void navigate({ pathname: location.pathname, search: `?tab=${value}` }, { replace: true });
  }
  const [edit, setEdit] = useState(false);
  const [targetStatus, setTargetStatus] = useState<CompanyStatus>();
  const [busy, setBusy] = useState(false);
  const changedBusy = useCallback(
    (value: boolean) => {
      setBusy(value);
      onBusy(value);
    },
    [onBusy],
  );
  useEffect(() => () => onBusy(false), [onBusy]);
  const reload = useCallback(
    async (signal?: AbortSignal) => {
      const value = await api<AdminCompanyDetail>(
        `/admin/companies/${encodeURIComponent(companyId)}`,
        session,
        { signal },
      );
      if (!signal?.aborted) {
        setDetail(value);
        setError('');
        setLoading(false);
      }
    },
    [companyId, session],
  );
  useEffect(() => {
    const controller = new AbortController();
    void reload(controller.signal).catch((failure) => {
      if (!controller.signal.aborted) {
        setError(errorMessage(failure));
        setLoading(false);
      }
    });
    return () => controller.abort();
  }, [reload]);
  return (
    <>
      <button className="text-button admin-back" disabled={busy} onClick={onBack}>
        <ArrowLeft size={15} />
        企业列表
      </button>
      {error && (
        <p className="form-error" role="alert">
          {error}
          <button
            className="text-button"
            onClick={() => void reload().catch((failure) => setError(errorMessage(failure)))}
          >
            重新加载
          </button>
        </p>
      )}
      {loading ? (
        <div className="loading-block">
          <Spinner />
          正在读取企业管理资料…
        </div>
      ) : (
        detail && (
          <>
            <AdminHeading
              title={detail.company.name}
              description={`税号：${detail.company.tax_id || '未填写'} · ${companyStatusLabels[detail.company.status]}`}
              action={
                <div className="admin-inline-actions">
                  <button
                    className="icon-button"
                    aria-label="刷新企业详情"
                    disabled={busy}
                    onClick={() =>
                      void reload().catch((failure) => setError(errorMessage(failure)))
                    }
                  >
                    <RefreshCw size={16} />
                  </button>
                  <button
                    className="button primary"
                    disabled={busy || detail.company.status !== 'active'}
                    onClick={onWork}
                  >
                    <ArrowUpRight size={16} />
                    进入工作台
                  </button>
                </div>
              }
            />
            <section className="admin-summary">
              <div>
                <strong>{detail.members.length}</strong>
                <span>企业成员</span>
              </div>
              <div>
                <strong>{detail.departments.length}</strong>
                <span>部门</span>
              </div>
              <div>
                <strong>{detail.books.length}</strong>
                <span>账期记录</span>
              </div>
              <div className="admin-lifecycle-actions">
                <button
                  className="button secondary small"
                  disabled={busy}
                  onClick={() => setEdit(true)}
                >
                  修改企业资料
                </button>
                {detail.company.can_change_status &&
                  (detail.company.status === 'active' ? (
                    <button
                      className="button secondary small"
                      disabled={busy}
                      onClick={() => setTargetStatus('suspended')}
                    >
                      暂停服务
                    </button>
                  ) : (
                    <button
                      className="button secondary small"
                      disabled={busy}
                      onClick={() => setTargetStatus('active')}
                    >
                      恢复服务
                    </button>
                  ))}
                {detail.company.can_change_status && detail.company.status !== 'archived' && (
                  <button
                    className="button secondary small"
                    disabled={busy}
                    onClick={() => setTargetStatus('archived')}
                  >
                    归档企业
                  </button>
                )}
              </div>
            </section>
            {detail.company.status !== 'active' && (
              <p className="admin-info" role="status">
                此企业已{detail.company.status === 'archived' ? '归档' : '暂停'}
                。业务处理与授权变更已停止，原始资料和操作历史继续保留。
              </p>
            )}
            {detail.company.status === 'active' && tab !== 'setup' && (
              <div className="admin-setup-prompt">
                <span>首次接入或人员变动后，按清单核对提交、审批与会计分工。</span>
                <button
                  className="button secondary small"
                  disabled={busy}
                  onClick={() => changeTab('setup')}
                >
                  查看开通清单
                </button>
              </div>
            )}
            <div className="admin-tabs" role="tablist" aria-label="企业管理内容">
              {[
                ['setup', '开通清单'],
                ['members', '人员与权限'],
                ['departments', '部门与审批'],
                ['books', '账套分工'],
                ['ai', firmManager ? 'AI用量与控制' : '智能识别与隐私'],
                ['audit', '操作记录'],
              ].map(([key, label]) => (
                <button
                  key={key}
                  role="tab"
                  aria-selected={tab === key}
                  disabled={busy}
                  onClick={() => changeTab(key)}
                >
                  {label}
                </button>
              ))}
            </div>
            <section className="admin-panel" role="tabpanel">
              {tab === 'setup' ? (
                <Suspense
                  fallback={
                    <div className="loading-block">
                      <Spinner />
                      正在打开开通清单…
                    </div>
                  }
                >
                  <AdminOnboarding
                    key={`${session.scope_key}:${companyId}`}
                    session={session}
                    companyId={companyId}
                    onOpen={(target) => {
                      if (target === 'company') setEdit(true);
                      else if (target === 'work') onWork();
                      else changeTab(target);
                    }}
                  />
                </Suspense>
              ) : tab === 'audit' ? (
                <AdminAudit session={session} companyId={companyId} />
              ) : tab === 'ai' ? (
                <Suspense
                  fallback={
                    <div className="loading-block">
                      <Spinner />
                      正在打开企业AI控制…
                    </div>
                  }
                >
                  <AdminAIControls
                    key={`${session.scope_key}:${companyId}`}
                    session={session}
                    companyId={companyId}
                    companyName={detail.company.name}
                    firmManager={firmManager}
                    onBusy={changedBusy}
                    onAudit={() => changeTab('audit')}
                  />
                </Suspense>
              ) : tab === 'departments' ? (
                detail.company.status === 'active' ? (
                  <BusinessOrganization
                    key={companyId}
                    session={session}
                    companies={[detail.company]}
                    onChanged={reload}
                    onBusyChange={changedBusy}
                  />
                ) : (
                  <div>
                    {detail.departments.map((department) => (
                      <p key={department.id}>{department.name}</p>
                    ))}
                    {!detail.departments.length && <p className="field-help">尚未建立部门。</p>}
                  </div>
                )
              ) : (
                <AdminMembers
                  key={`${companyId}:${tab}:${detail.company.status}`}
                  session={session}
                  kind={tab === 'books' ? 'book' : 'company'}
                  company={detail.company}
                  departments={detail.departments}
                  books={detail.books}
                  onBusy={changedBusy}
                  onChanged={reload}
                  disabled={detail.company.status !== 'active'}
                  initialMembers={detail.members}
                />
              )}
            </section>
            {edit && (
              <CompanyForm
                session={session}
                company={detail.company}
                onBusy={changedBusy}
                onClose={() => setEdit(false)}
                onSaved={(company) => {
                  setDetail({ ...detail, company });
                  setEdit(false);
                }}
              />
            )}
            {targetStatus && (
              <CompanyStatusForm
                session={session}
                company={detail.company}
                target={targetStatus}
                pending={detail.pending_counts}
                onBusy={changedBusy}
                onClose={() => setTargetStatus(undefined)}
                onSaved={(company) => {
                  setDetail({ ...detail, company });
                  setTargetStatus(undefined);
                }}
              />
            )}
          </>
        )
      )}
    </>
  );
}

function CompanyForm({
  session,
  company,
  onBusy,
  onClose,
  onSaved,
}: {
  session: Session;
  company?: AdminCompany;
  onBusy: (value: boolean) => void;
  onClose: () => void;
  onSaved: (company: AdminCompany) => void;
}) {
  const [name, setName] = useState(company?.name ?? '');
  const [taxId, setTaxId] = useState(company?.tax_id ?? '');
  const [reason, setReason] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  useEffect(() => () => onBusy(false), [onBusy]);
  async function submit() {
    if (busy) return;
    setBusy(true);
    onBusy(true);
    setError('');
    try {
      const result = await api<AdminCompany>(
        company ? `/admin/companies/${encodeURIComponent(company.id)}` : '/companies',
        session,
        {
          method: company ? 'PATCH' : 'POST',
          body: {
            name: name.trim(),
            tax_id: taxId.trim(),
            ...(company ? { reason: reason.trim(), expected_version: company.version } : {}),
          },
        },
      );
      onSaved(result);
    } catch (failure) {
      setError(errorMessage(failure));
    } finally {
      setBusy(false);
      onBusy(false);
    }
  }
  return (
    <Modal
      title={company ? '修改企业资料' : '新建企业'}
      subtitle="每家企业独立管理人员、资料与账套。"
      busy={busy}
      error={error}
      onClose={onClose}
    >
      <form
        className="admin-form"
        onSubmit={(event) => {
          event.preventDefault();
          void submit();
        }}
      >
        <label>
          企业名称
          <input
            value={name}
            maxLength={120}
            required
            disabled={busy}
            onChange={(event) => setName(event.target.value)}
          />
        </label>
        <label>
          统一社会信用代码／税号
          <input
            value={taxId}
            maxLength={30}
            disabled={busy}
            onChange={(event) => setTaxId(event.target.value)}
          />
        </label>
        {company && (
          <label>
            修改原因
            <textarea
              value={reason}
              required
              minLength={2}
              maxLength={500}
              disabled={busy}
              onChange={(event) => setReason(event.target.value)}
            />
          </label>
        )}
        {error && company && (
          <p className="field-help">如提示版本已变化，请关闭弹窗并重新加载企业详情，再提交修改。</p>
        )}
        <div className="admin-form-actions">
          <button className="button secondary" type="button" disabled={busy} onClick={onClose}>
            取消
          </button>
          <button
            className="button primary"
            disabled={busy || !name.trim() || (!!company && reason.trim().length < 2)}
          >
            {busy && <Spinner />}
            {company ? '保存修改' : '创建企业'}
          </button>
        </div>
      </form>
    </Modal>
  );
}

function CompanyStatusForm({
  session,
  company,
  target,
  pending,
  onBusy,
  onClose,
  onSaved,
}: {
  session: Session;
  company: AdminCompany;
  target: CompanyStatus;
  pending: Record<string, number>;
  onBusy: (value: boolean) => void;
  onClose: () => void;
  onSaved: (company: AdminCompany) => void;
}) {
  const [reason, setReason] = useState('');
  const [confirmed, setConfirmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const title =
    target === 'active' ? '恢复企业服务' : target === 'archived' ? '归档企业' : '暂停企业服务';
  useEffect(() => () => onBusy(false), [onBusy]);
  async function submit() {
    if (busy) return;
    setBusy(true);
    onBusy(true);
    setError('');
    try {
      const result = await api<AdminCompany>(
        `/admin/companies/${encodeURIComponent(company.id)}/status`,
        session,
        {
          method: 'POST',
          body: { status: target, reason: reason.trim(), expected_version: company.version },
        },
      );
      onSaved(result);
    } catch (failure) {
      setError(errorMessage(failure));
    } finally {
      setBusy(false);
      onBusy(false);
    }
  }
  const count = Object.values(pending).reduce((sum, value) => sum + value, 0);
  return (
    <Modal title={title} subtitle={company.name} busy={busy} error={error} onClose={onClose}>
      <form
        className="admin-form"
        onSubmit={(event) => {
          event.preventDefault();
          void submit();
        }}
      >
        <p className="admin-info">
          {target === 'active'
            ? '恢复后按现有授权开放业务访问。请确认成员和账套授权仍然适用。'
            : '停止该企业业务访问及后台处理，保留历史账务、原始附件和审计记录。'}
          {count > 0 && ` 当前还有 ${count} 项待处理工作，请安排交接。`}
        </p>
        <label>
          操作原因
          <textarea
            value={reason}
            required
            minLength={2}
            maxLength={500}
            disabled={busy}
            onChange={(event) => setReason(event.target.value)}
          />
        </label>
        <label className="admin-checkbox">
          <input
            type="checkbox"
            checked={confirmed}
            disabled={busy}
            onChange={(event) => setConfirmed(event.target.checked)}
          />
          我确认对「{company.name}」执行此操作。
        </label>
        <div className="admin-form-actions">
          <button className="button secondary" type="button" disabled={busy} onClick={onClose}>
            取消
          </button>
          <button
            className="button primary"
            disabled={busy || !confirmed || reason.trim().length < 2}
          >
            {busy && <Spinner />}
            {title}
          </button>
        </div>
      </form>
    </Modal>
  );
}
