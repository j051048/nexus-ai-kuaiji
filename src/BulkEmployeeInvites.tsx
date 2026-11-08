import { useEffect, useRef, useState } from 'react';
import { api, errorMessage } from './api';
import type { Session } from './types';
import type { Department } from './business-types';
import type { AccountRequest } from './admin-types';
import { validInvitePreview } from './invite-preview';
import { InvitationDelivery } from './InvitationDelivery';

type Item = {
  name: string;
  email: string;
  company_id: string;
  company_role: string;
  department_id: string | null;
  provisioning_mode: string;
  idempotency_key: string;
};
type Preview = {
  ready: boolean;
  provisioning_enabled: boolean;
  items: { index: number; ready: boolean; message: string; email?: string }[];
};

/** Same single-account command and stable request keys; never auto-retry uncertain sends. */
export function BulkEmployeeInvites({
  session,
  companyId,
  departments,
  onBusy,
  onCompleted,
}: {
  session: Session;
  companyId: string;
  departments: Department[];
  onBusy: (busy: boolean) => void;
  onCompleted: () => void;
}) {
  const [text, setText] = useState('');
  const [role, setRole] = useState('employee');
  const [department, setDepartment] = useState('');
  const [preview, setPreview] = useState<Preview | null>(null);
  const [items, setItems] = useState<Item[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [started, setStarted] = useState(false);
  const [results, setResults] = useState<{ email: string; status: string; link?: string }[]>([]);
  const [deliveryEmail, setDeliveryEmail] = useState('');
  const live = useRef(true),
    lock = useRef(false);
  useEffect(() => {
    live.current = true;
    return () => {
      live.current = false;
      onBusy(false);
    };
  }, [onBusy]);
  function invalidate() {
    setPreview(null);
    setItems([]);
    setError('');
  }
  async function check() {
    if (lock.current) return;
    lock.current = true;
    setBusy(true);
    onBusy(true);
    setError('');
    setPreview(null);
    try {
      const lines = text.trim().split(/\r?\n/).filter(Boolean);
      if (!lines.length || lines.length > 50) throw new Error('每批请输入 1–50 人。');
      const next = lines.map((line) => {
        const parts = line.split(/[\t,，]/).map((part) => part.trim());
        if (parts.length !== 2) throw new Error('每行填写“姓名,邮箱”，也可从表格粘贴两列。');
        return {
          name: parts[0],
          email: parts[1],
          company_id: companyId,
          company_role: role,
          department_id: department || null,
          provisioning_mode: 'activation',
          idempotency_key: crypto.randomUUID(),
        };
      });
      const value = await api<Preview>('/admin/account-requests/preview', session, {
        method: 'POST',
        body: { items: next },
      });
      if (!live.current) return;
      if (!validInvitePreview(value, next)) throw new Error('预检结果无法核验，请重新检查名单。');
      setItems(next);
      setPreview(value);
    } catch (failure) {
      if (live.current) setError(errorMessage(failure));
    } finally {
      lock.current = false;
      if (live.current) {
        setBusy(false);
        onBusy(false);
      }
    }
  }
  async function create() {
    if (lock.current || started || !preview?.ready || !preview.provisioning_enabled) return;
    lock.current = true;
    setBusy(true);
    onBusy(true);
    setStarted(true);
    setError('');
    try {
      for (const item of items) {
        if (!live.current) break;
        try {
          const value = await api<{ request: AccountRequest; activation_url?: string }>(
            '/admin/account-requests',
            session,
            { method: 'POST', body: item },
          );
          if (!live.current) break;
          if (
            value.request?.email !== item.email.toLowerCase() ||
            !value.request.id ||
            value.request.company_id !== item.company_id ||
            value.request.company_role !== item.company_role ||
            value.request.department_id !== item.department_id ||
            value.request.provisioning_mode !== 'activation' ||
            value.request.firm_role
          )
            throw new Error('无法核验开户结果，请在开通记录中查询，勿重复生成。');
          setResults((rows) => [
            ...rows,
            {
              email: item.email,
              status:
                value.request.status === 'awaiting_activation'
                  ? '等待本人激活'
                  : value.request.status === 'failed'
                    ? '开通失败，请查看开通记录'
                    : '请查看开通记录核对状态',
              link: value.activation_url,
            },
          ]);
          if (value.request.status !== 'awaiting_activation') break;
        } catch (failure) {
          if (live.current) {
            setResults((rows) => [
              ...rows,
              { email: item.email, status: '未确认结果，后续人员尚未办理' },
            ]);
            setError(errorMessage(failure));
          }
          break;
        }
      }
      if (live.current) onCompleted();
    } finally {
      lock.current = false;
      if (live.current) {
        setBusy(false);
        onBusy(false);
      }
    }
  }
  return (
    <details className="admin-provisions bulk-employee-invites">
      <summary>批量员工入职（先检查名单）</summary>
      <p>
        仅为当前企业生成本人激活邀请。员工可交资料；企业财务可核验业务。均不自动获得账套、复核或导出权限。
      </p>
      <label>
        入职岗位
        <select
          value={role}
          disabled={busy || started}
          onChange={(e) => {
            setRole(e.target.value);
            invalidate();
          }}
        >
          <option value="employee">员工 · 提交资料</option>
          <option value="finance">企业财务 · 核验业务</option>
        </select>
      </label>
      <label>
        入职部门
        <select
          value={department}
          disabled={busy || started}
          onChange={(e) => {
            setDepartment(e.target.value);
            invalidate();
          }}
        >
          <option value="">未指定</option>
          {departments.map((item) => (
            <option key={item.id} value={item.id}>
              {item.name}
            </option>
          ))}
        </select>
      </label>
      <label>
        入职名单
        <textarea
          rows={5}
          maxLength={20000}
          value={text}
          disabled={busy || started}
          placeholder="张三,zhang@example.com"
          onChange={(e) => {
            setText(e.target.value);
            invalidate();
          }}
        />
      </label>
      {error && (
        <p role="alert" className="form-error">
          {error}
        </p>
      )}
      {!started && (
        <button className="button secondary" disabled={busy} onClick={() => void check()}>
          检查名单
        </button>
      )}
      {preview && (
        <ul>
          {preview.items.map((item) => (
            <li key={item.index}>
              第 {item.index + 1} 人：{item.message}
            </li>
          ))}
        </ul>
      )}
      {preview?.ready && !started && (
        <button
          className="button"
          disabled={busy || !preview.provisioning_enabled}
          onClick={() => void create()}
        >
          确认生成 {items.length} 人的邀请
        </button>
      )}
      {preview && !preview.provisioning_enabled && (
        <p>当前未启用账号开通服务，名单已检查，尚未生成邀请。</p>
      )}
      {!!results.length && (
        <>
          <p>
            链接仅在本次显示。生成邀请不会发送邮件；可逐人打开受控发送，或通过可信方式交给本人。未确认项请先查开通记录，禁止重复生成。
          </p>
          <ul>
            {results.map((row) => (
              <li key={row.email}>
                {row.email}：{row.status}
                {row.link && (
                  <input
                    readOnly
                    aria-label={`${row.email} 激活链接`}
                    value={row.link}
                    onFocus={(e) => e.target.select()}
                  />
                )}
                {row.link && (
                  <button
                    type="button"
                    className="text-button"
                    onClick={() => setDeliveryEmail(deliveryEmail === row.email ? '' : row.email)}
                  >
                    {deliveryEmail === row.email ? '收起邮件邀请' : '办理邮件邀请'}
                  </button>
                )}
                {row.link && deliveryEmail === row.email && (
                  <InvitationDelivery value={row.link} session={session} />
                )}
              </li>
            ))}
          </ul>
        </>
      )}
    </details>
  );
}
