import { useEffect, useState } from 'react';
import { RefreshCw } from 'lucide-react';
import { api, errorMessage } from './api';
import { EmptyState, PageControls, Spinner } from './AppPrimitives';
import type { Session } from './types';
import type { AdminAuditEvent } from './admin-types';

const auditLabels: Record<string, string> = {
  'company.create': '新建企业',
  'company.update': '修改企业资料',
  'company.status': '企业状态变更',
  'company.member.grant': '配置企业成员',
  'company.member.revoke': '移除企业成员',
  'company.department.create': '新建部门',
  'company.department.approver.grant': '配置部门审批人',
  'company.department.approver.revoke': '撤销部门审批人',
  'firm.member.grant': '配置事务所成员',
  'firm.member.revoke': '移除事务所成员',
  'book.member.grant': '配置账套授权',
  'book.member.revoke': '撤销账套授权',
  'admin.account.request': '发起账号开通',
  'admin.account.link_created': '生成账号激活链接',
  'admin.account.failed': '账号开通失败',
  'admin.account.cancel': '撤销账号邀请',
  'admin.account.accept': '成员接受授权',
};

export function AdminAudit({ session, companyId }: { session: Session; companyId?: string }) {
  const [data, setData] = useState<{
    events: AdminAuditEvent[];
    total: number;
    limit: number;
    offset: number;
  }>();
  const [offset, setOffset] = useState(0);
  const [action, setAction] = useState('');
  const [query, setQuery] = useState('');
  const [revision, setRevision] = useState(0);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError('');
    setData(undefined);
    const params = new URLSearchParams({
      limit: '25',
      offset: String(offset),
      ...(query ? { action: query } : {}),
      ...(companyId ? { company_id: companyId } : {}),
    });
    void api<NonNullable<typeof data>>(`/admin/audit?${params}`, session, {
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
  }, [session, companyId, offset, query, revision]);
  return (
    <>
      <form
        className="admin-filters"
        onSubmit={(event) => {
          event.preventDefault();
          setQuery(action.trim());
          setOffset(0);
        }}
      >
        <label className="admin-audit-filter">
          操作类型
          <select
            aria-label="筛选操作类型"
            value={action}
            onChange={(event) => setAction(event.target.value)}
          >
            <option value="">全部操作</option>
            {Object.entries(auditLabels).map(([key, label]) => (
              <option key={key} value={key}>
                {label}
              </option>
            ))}
          </select>
        </label>
        <button className="button secondary">查询</button>
        <button
          type="button"
          className="icon-button"
          aria-label="刷新操作记录"
          onClick={() => setRevision((value) => value + 1)}
        >
          <RefreshCw size={16} />
        </button>
      </form>
      {error && (
        <p className="form-error" role="alert">
          {error}
        </p>
      )}
      {loading ? (
        <div className="loading-block">
          <Spinner />
          正在读取操作记录…
        </div>
      ) : data?.events.length ? (
        <div className="admin-audit-list">
          {data.events.map((event) => (
            <article className="admin-audit-event" key={event.id}>
              <div>
                <strong>{event.user_name || event.user_id}</strong>
                <span className="admin-audit-action">
                  {auditLabels[event.action] ?? event.action}
                </span>
                <time dateTime={event.created_at}>
                  {new Date(event.created_at).toLocaleString('zh-CN')}
                </time>
              </div>
              <details>
                <summary>查看变更详情</summary>
                <pre>{JSON.stringify(event.details, null, 2)}</pre>
                <p className="field-help">对象：{event.object_id}</p>
              </details>
            </article>
          ))}
        </div>
      ) : (
        !error && <EmptyState title="暂无操作记录">企业和权限变更会在这里留下记录。</EmptyState>
      )}
      {data && (
        <PageControls
          label="操作记录"
          busy={loading}
          page={{ ...data, has_more: data.offset + data.limit < data.total }}
          onPage={setOffset}
        />
      )}
    </>
  );
}
