import { useState } from 'react';
import { api, errorMessage } from './api';
import { useScopedQuery } from './useScopedQuery';
import { staffTemplateAllowed, staffTemplates, type StaffTemplate } from './access-policy';
import type { Company, FirmRole, Member, Session } from './types';

type Assignment = {
  id: string;
  name: string;
  company_id: string | null;
  template: StaffTemplate;
  valid_from: string | null;
  valid_to: string | null;
  state: string;
  reason: string;
};
export function AccessPolicyPanel({
  session,
  firms,
  companies,
  onChanged,
}: {
  session: Session;
  firms: Member<FirmRole>[];
  companies: Company[];
  onChanged: () => Promise<void>;
}) {
  const [user, setUser] = useState('');
  const [template, setTemplate] = useState<StaffTemplate>('auditor');
  const [company, setCompany] = useState('unselected');
  const [from, setFrom] = useState('');
  const [until, setUntil] = useState('');
  const [reason, setReason] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const query = useScopedQuery(
    (signal) => api<{ assignments: Assignment[] }>('/firm/access-assignments', session, { signal }),
    session.scope_key,
  );
  const selected = firms.find((person) => person.user_id === user);
  async function change(action: () => Promise<unknown>) {
    if (busy) return;
    setBusy(true);
    setError('');
    try {
      await action();
      query.reload();
      await onChanged();
    } catch (failure) {
      setError(errorMessage(failure));
    } finally {
      setBusy(false);
    }
  }
  return (
    <section aria-label="岗位范围与期限" className="panel">
      <h3>岗位范围与期限</h3>
      <p className="field-help">
        明确选择客户范围。全所包含以后新建的客户与月份；已有只读账号不会自动扩大权限。模板授权与原有账套授权叠加，撤销仅移除此条授权。
      </p>
      {(error || query.error) && (
        <p role="alert" className="form-error">
          {error || query.error}
        </p>
      )}
      <form
        className="access-policy-form"
        onSubmit={(event) => {
          event.preventDefault();
          void change(() =>
            api('/firm/access-assignments', session, {
              method: 'POST',
              body: {
                user_id: user,
                template,
                company_id: company === '*' ? null : company,
                scope: company === '*' ? 'firm' : 'company',
                valid_from: from ? new Date(from).toISOString() : null,
                valid_to: until ? new Date(until).toISOString() : null,
                reason,
              },
            }),
          );
        }}
      >
        <label>
          事务所成员
          <select
            aria-label="岗位成员"
            value={user}
            disabled={busy}
            required
            onChange={(event) => setUser(event.target.value)}
          >
            <option value="">选择成员</option>
            {firms
              .filter((person) => !['owner', 'admin'].includes(person.role))
              .map((person) => (
                <option key={person.user_id} value={person.user_id}>
                  {person.name}
                </option>
              ))}
          </select>
        </label>
        <label>
          岗位模板
          <select
            aria-label="岗位模板"
            value={template}
            disabled={busy}
            onChange={(event) => setTemplate(event.target.value as StaffTemplate)}
          >
            {Object.entries(staffTemplates).map(([key, label]) => (
              <option key={key} value={key}>
                {label}
              </option>
            ))}
          </select>
        </label>
        <label>
          客户范围
          <select
            aria-label="岗位客户范围"
            value={company}
            disabled={busy}
            onChange={(event) => setCompany(event.target.value)}
          >
            <option value="unselected">选择客户范围</option>
            <option value="*">全事务所（包含未来客户）</option>
            {companies.map((item) => (
              <option key={item.id} value={item.id}>
                {item.name}
              </option>
            ))}
          </select>
        </label>
        <details>
          <summary>生效时间与到期时间</summary>
          <label>
            开始时间
            <input
              aria-label="岗位开始时间"
              type="datetime-local"
              value={from}
              disabled={busy}
              onChange={(event) => setFrom(event.target.value)}
            />
          </label>
          <label>
            到期时间
            <input
              aria-label="岗位到期时间"
              type="datetime-local"
              value={until}
              disabled={busy}
              onChange={(event) => setUntil(event.target.value)}
            />
          </label>
        </details>
        <label>
          授权理由
          <input
            aria-label="岗位授权理由"
            required
            minLength={3}
            maxLength={1000}
            value={reason}
            disabled={busy}
            onChange={(event) => setReason(event.target.value)}
          />
        </label>
        {selected && !staffTemplateAllowed(selected.role, template) && (
          <p role="status">此成员的事务所身份不支持该岗位，请先核对成员职责。</p>
        )}
        <button
          className="button secondary"
          disabled={
            busy ||
            company === 'unselected' ||
            !selected ||
            !staffTemplateAllowed(selected.role, template)
          }
          type="submit"
        >
          保存岗位授权
        </button>
      </form>
      {query.loading ? (
        <p role="status">正在读取授权…</p>
      ) : (
        <ul className="access-policy-list">
          {query.data?.assignments.map((row) => (
            <li key={row.id}>
              <strong>
                {row.name} · {staffTemplates[row.template]}
              </strong>
              <p>
                {companies.find((c) => c.id === row.company_id)?.name ?? '全事务所'} ·{' '}
                {row.state === 'revoked'
                  ? '已撤销'
                  : `生效 ${row.valid_from ? new Date(row.valid_from).toLocaleString() : '立即'}；到期 ${row.valid_to ? new Date(row.valid_to).toLocaleString() : '长期'}`}
              </p>
              <p>{row.reason}</p>
              {row.state === 'active' && (
                <button
                  className="button secondary"
                  disabled={busy}
                  onClick={() => {
                    if (reason.trim().length < 3) {
                      setError('请先在授权理由中填写撤销原因');
                      return;
                    }
                    void change(() =>
                      api(
                        `/firm/access-assignments/${encodeURIComponent(row.id)}/revoke`,
                        session,
                        { method: 'POST', body: { reason } },
                      ),
                    );
                  }}
                >
                  撤销此授权
                </button>
              )}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

export function ContactAccessPanel({
  session,
  bookId,
  members,
  onChanged,
}: {
  session: Session;
  bookId: string;
  members: Member<string>[];
  onChanged?: () => Promise<void>;
}) {
  const [user, setUser] = useState('');
  const [reason, setReason] = useState('');
  const [validTo, setValidTo] = useState('');
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  async function save(revoke = false) {
    if (!user || busy || reason.trim().length < 3) return;
    setBusy(true);
    setMessage('');
    try {
      await api(
        `/books/${encodeURIComponent(bookId)}/contact-access${revoke ? '/revoke' : ''}`,
        session,
        {
          method: 'POST',
          body: {
            user_id: user,
            reason,
            valid_to: validTo ? new Date(validTo).toISOString() : null,
          },
        },
      );
      setMessage(
        revoke ? '已撤销仅签收授权' : '已保存。仍须在月度服务单指定同一联系人；不授予账簿权限。',
      );
      await onChanged?.();
    } catch (failure) {
      setMessage(errorMessage(failure));
    } finally {
      setBusy(false);
    }
  }
  return (
    <details className="panel">
      <summary>仅签收本月资料包</summary>
      <p>只允许指定联系人查看、下载、签收自己名下的冻结包；不授予账簿或制单权限。</p>
      <form
        className="access-policy-form"
        onSubmit={(event) => {
          event.preventDefault();
          void save();
        }}
      >
        <label>
          企业签收人
          <select
            aria-label="仅签收成员"
            required
            disabled={busy}
            value={user}
            onChange={(event) => setUser(event.target.value)}
          >
            <option value="">选择企业成员</option>
            {members.map((member) => (
              <option key={member.user_id} value={member.user_id}>
                {member.name}
              </option>
            ))}
          </select>
        </label>
        <label>
          到期时间
          <input
            type="datetime-local"
            aria-label="签收到期时间"
            value={validTo}
            disabled={busy}
            onChange={(event) => setValidTo(event.target.value)}
          />
        </label>
        <label>
          授权或撤销理由
          <input
            required
            minLength={3}
            value={reason}
            disabled={busy}
            onChange={(event) => setReason(event.target.value)}
          />
        </label>
        <button className="button secondary" disabled={busy} type="submit">
          保存仅签收授权
        </button>
        <button
          className="button secondary"
          disabled={busy || !user || reason.trim().length < 3}
          type="button"
          onClick={() => void save(true)}
        >
          撤销仅签收授权
        </button>
      </form>
      {message && <p role="status">{message}</p>}
    </details>
  );
}
