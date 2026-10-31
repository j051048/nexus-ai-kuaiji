import { useState } from 'react';
import { api, apiBlob, errorMessage } from './api';
import { useScopedQuery } from './useScopedQuery';
import { acceptPackage } from './monthly-package-read';
import type { MonthlyPackage } from './monthly-package-types';
import type { BusinessContext } from './business-types';
import type { Session } from './types';
import { expenseSummary } from './access-policy';
import './access-policy.css';

type PackageRef = {
  id: string;
  book_id: string;
  company_id: string;
  company_name: string;
  period: string;
  version: number;
  manifest_sha256: string;
};
export function ClientAccessTools({
  session,
  canApprove,
}: {
  session: Session;
  canApprove: boolean;
}) {
  const [open, setOpen] = useState(false);
  return (
    <section className="client-access-tools" aria-label="获授权资料与部门费用">
      <details onToggle={(event) => setOpen(event.currentTarget.open)}>
        <summary>我需要签收的资料包</summary>
        {open && <RecipientPackages key={session.scope_key} session={session} />}
      </details>
      {canApprove && <DepartmentExpenses key={session.scope_key} session={session} />}
    </section>
  );
}

function RecipientPackages({ session }: { session: Session }) {
  const [selected, setSelected] = useState<MonthlyPackage | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [reason, setReason] = useState('');
  const [confirmed, setConfirmed] = useState(false);
  const query = useScopedQuery(async (signal) => {
    const result = await api<{ packages: PackageRef[] }>('/my-delivery-packages', session, {
      signal,
    });
    if (
      !Array.isArray(result.packages) ||
      result.packages.length > 100 ||
      result.packages.some(
        (row) =>
          !row ||
          !row.id ||
          !row.book_id ||
          !row.company_id ||
          !/^20\d\d-(0[1-9]|1[0-2])$/.test(row.period) ||
          !Number.isSafeInteger(row.version) ||
          row.version < 1 ||
          !/^[a-f0-9]{64}$/.test(row.manifest_sha256),
      )
    )
      throw new Error('资料包清单范围不完整，请刷新后联系会计');
    return result;
  }, session.scope_key);
  async function act(row: PackageRef, action: 'open' | 'download' | 'acknowledge') {
    if (busy) return;
    setBusy(true);
    setError('');
    const base = `/books/${encodeURIComponent(row.book_id)}/delivery/monthly-package/${encodeURIComponent(row.id)}`;
    try {
      if (action === 'download') {
        const blob = await apiBlob(`${base}/download`, session);
        const link = document.createElement('a');
        const url = URL.createObjectURL(blob);
        link.href = url;
        link.download = `${row.period}-V${row.version}.zip`;
        link.click();
        setTimeout(() => URL.revokeObjectURL(url), 1000);
      } else {
        const result = await api<unknown>(`${base}/${action}`, session, {
          method: 'POST',
          ...(action === 'acknowledge'
            ? { body: { reason: reason.trim(), manifest_sha256: row.manifest_sha256 } }
            : {}),
        });
        const accepted = acceptPackage(result, {
          id: row.id,
          version: row.version,
          bookId: row.book_id,
          companyId: row.company_id,
          period: row.period,
          manifestSha256: row.manifest_sha256,
        });
        if (
          !accepted ||
          accepted.contact_user_id !== session.user_id ||
          (action === 'acknowledge' && accepted.customer_receipt_status !== 'acknowledged')
        )
          throw new Error('资料包版本校验失败，请重新选择；本次未确认签收状态');
        setSelected(accepted);
        setConfirmed(false);
        setReason('');
      }
    } catch (failure) {
      setSelected(null);
      setConfirmed(false);
      query.reload();
      setError(errorMessage(failure));
    } finally {
      setBusy(false);
    }
  }
  return (
    <div>
      {(error || query.error) && (
        <p className="form-error" role="alert">
          {error || query.error}
        </p>
      )}
      {query.loading ? (
        <p role="status">正在读取待签收资料…</p>
      ) : !query.data?.packages.length ? (
        <p>当前没有获授权的资料包。请联系本企业负责人或会计确认签收联系人。</p>
      ) : (
        <ul>
          {query.data.packages.map((row) => (
            <li key={row.id}>
              <strong>
                {row.company_name} · {row.period} · 第 {row.version} 版
              </strong>
              <button
                className="button secondary"
                disabled={busy}
                onClick={() => void act(row, 'open')}
              >
                查看这版资料
              </button>
              {selected?.id === row.id && (
                <div>
                  <p>
                    本版包含 {selected.file_count ?? selected.files?.length ?? 0}{' '}
                    个文件；签收只表示收到资料，不代表财务软件已导入。
                  </p>
                  <ul>
                    {selected.files?.map((file) => (
                      <li key={file.path}>{file.path}</li>
                    ))}
                  </ul>
                  <button
                    className="button secondary"
                    disabled={busy}
                    onClick={() => void act(row, 'download')}
                  >
                    下载这版资料
                  </button>
                  {selected.can_acknowledge ? (
                    <form
                      onSubmit={(event) => {
                        event.preventDefault();
                        if (confirmed && reason.trim().length >= 3) void act(row, 'acknowledge');
                      }}
                    >
                      <label>
                        <input
                          type="checkbox"
                          checked={confirmed}
                          disabled={busy}
                          onChange={(event) => setConfirmed(event.target.checked)}
                        />
                        我已核对当前企业、月份和版本
                      </label>
                      <label>
                        签收说明
                        <input
                          required
                          minLength={3}
                          value={reason}
                          disabled={busy}
                          onChange={(event) => setReason(event.target.value)}
                        />
                      </label>
                      <button
                        className="button primary"
                        disabled={busy || !confirmed || reason.trim().length < 3}
                      >
                        确认收到这版资料
                      </button>
                    </form>
                  ) : (
                    <p role="status">
                      {selected.customer_receipt_status === 'acknowledged'
                        ? '这版资料已签收'
                        : '当前账号不能签收，请联系会计核对权限'}
                    </p>
                  )}
                </div>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function DepartmentExpenses({ session }: { session: Session }) {
  const [open, setOpen] = useState(false);
  return (
    <details onToggle={(event) => setOpen(event.currentTarget.open)}>
      <summary>本部门费用</summary>
      {open && <DepartmentExpenseForm session={session} />}
    </details>
  );
}
function DepartmentExpenseForm({ session }: { session: Session }) {
  const context = useScopedQuery(
    (signal) => api<BusinessContext>('/business/context', session, { signal }),
    session.scope_key,
  );
  const [selection, setSelection] = useState('');
  const [period, setPeriod] = useState(() => new Date().toISOString().slice(0, 7));
  const choices = (context.data?.companies ?? []).flatMap((company) =>
    company.departments
      .filter((department) => company.approver_department_ids.includes(department.id))
      .map((department) => ({
        company_id: company.id,
        department_id: department.id,
        label: `${company.name} · ${department.name}`,
      })),
  );
  const selected = choices.find(
    (choice) => `${choice.company_id}:${choice.department_id}` === selection,
  );
  const query = useScopedQuery(async (signal) => {
    if (!selected || !/^20\d\d-(0[1-9]|1[0-2])$/.test(period)) return null;
    const scope = { ...selected, period };
    const data = await api<unknown>(
      `/companies/${encodeURIComponent(selected.company_id)}/departments/${encodeURIComponent(selected.department_id)}/expenses?period=${period}`,
      session,
      { signal },
    );
    const cents = expenseSummary(data, scope);
    if (cents === null) throw new Error('费用汇总范围或金额不完整，请刷新后联系会计');
    return {
      cents,
      label: selected.label,
      period,
      company_id: selected.company_id,
      department_id: selected.department_id,
    };
  }, `${session.scope_key}:${selection}:${period}`);
  return (
    <div>
      <label>
        获授权部门
        <select
          aria-label="费用查询部门"
          value={selection}
          onChange={(event) => setSelection(event.target.value)}
        >
          <option value="">选择获授权部门</option>
          {choices.map((choice) => (
            <option
              key={choice.department_id}
              value={`${choice.company_id}:${choice.department_id}`}
            >
              {choice.label}
            </option>
          ))}
        </select>
      </label>
      <label>
        月份
        <input
          aria-label="部门费用月份"
          type="month"
          value={period}
          onChange={(event) => setPeriod(event.target.value)}
        />
      </label>
      {(context.error || query.error) && <p role="alert">{context.error || query.error}</p>}
      {selected && query.loading && <p role="status">正在核对部门范围…</p>}
      {query.data &&
        query.data.period === period &&
        query.data.company_id === selected?.company_id &&
        query.data.department_id === selected?.department_id && (
          <p role="status">
            {query.data.label} · {query.data.period} · 已入账费用净额 ¥
            {(query.data.cents / 100).toFixed(2)}
          </p>
        )}
      <p className="field-help">
        仅统计正式凭证中明确归属本部门的费用；不含未分配费用，不代表企业全部费用。
      </p>
    </div>
  );
}
