import { useEffect, useState } from 'react';
import { api } from './api';
import type { Session } from './types';
import './workspace-context-bar.css';

interface CloseRow {
  id: string;
  period: string;
  status: string;
  version: number;
}

const roleLabels: Record<string, string> = {
  owner: '事务所负责人',
  admin: '事务所管理员',
  reviewer: '复核会计',
  operator: '记账会计',
  finance: '企业财务',
  viewer: '只读',
  employee: '企业员工',
  client_member: '企业联系人',
};

/**
 * Persistent "unit / identity / period" strip. It reports the facts the shell
 * already owns (firm, working company, role, period) and the lock state the
 * close ledger reports; switching stays on the existing book-entry points so
 * this strip cannot invent an authorization.
 */
export function WorkspaceContextBar({
  session,
  companyName,
  companies,
  companyId,
  period,
  bookId,
  onPeriodChange,
  onCompanyChange,
  onOpenBooks,
}: {
  session: Session;
  companyName?: string;
  companies?: { id: string; name: string }[];
  companyId?: string;
  period: string;
  bookId?: string;
  onPeriodChange: (period: string) => void;
  onCompanyChange?: (companyId: string) => void;
  onOpenBooks: () => void;
}) {
  const [close, setClose] = useState<CloseRow | null>(null);
  const [error, setError] = useState('');

  useEffect(() => {
    if (!bookId || !/^\d{4}-(0[1-9]|1[0-2])$/.test(period)) {
      setClose(null);
      return;
    }
    let live = true;
    const controller = new AbortController();
    const query = new URLSearchParams({ book_id: bookId, period });
    void api<{ closes: CloseRow[] }>(`/b04-close?${query}`, session, {
      signal: controller.signal,
    })
      .then((data) => {
        if (!live) return;
        setError('');
        setClose(data.closes.find((row) => row.period === period) ?? null);
      })
      .catch(() => {
        if (!live) return;
        // The strip never blocks the page: an unreadable lock state is shown as
        // unknown instead of being guessed as open.
        setClose(null);
        setError('锁期状态待核实');
      });
    return () => {
      live = false;
      controller.abort();
    };
  }, [bookId, period, session]);

  const lockLabel = !bookId
    ? '未选择账套'
    : error
      ? '锁期状态待核实'
      : close?.status === 'locked' || close?.status === 'published'
        ? '已锁期'
        : close
          ? '未锁期'
          : '未锁期';

  return (
    <section className="workspace-context-bar" aria-label="当前单位、身份与期间">
      <div className="workspace-context-item">
        <span className="workspace-context-label">我所属单位</span>
        <strong>{session.firm_name || '未载入'}</strong>
      </div>
      <div className="workspace-context-item">
        <span className="workspace-context-label">当前办理单位</span>
        {companies && companies.length > 0 && onCompanyChange ? (
          <select
            aria-label="当前办理单位"
            value={companyId ?? ''}
            onChange={(event) => onCompanyChange(event.target.value)}
          >
            <option value="">未选择</option>
            {companies.map((company) => (
              <option key={company.id} value={company.id}>
                {company.name}
              </option>
            ))}
          </select>
        ) : (
          <strong>{companyName ?? '未选择'}</strong>
        )}
        <button type="button" className="text-button" onClick={onOpenBooks}>
          切换
        </button>
      </div>
      <div className="workspace-context-item">
        <span className="workspace-context-label">身份</span>
        <strong>{roleLabels[session.role ?? ''] ?? session.role ?? '未载入'}</strong>
      </div>
      <label className="workspace-context-item workspace-context-period">
        <span className="workspace-context-label">期间</span>
        <input
          type="month"
          aria-label="当前会计期间"
          min="2000-01"
          max="2099-12"
          value={period}
          onChange={(event) => {
            if (/^\d{4}-(0[1-9]|1[0-2])$/.test(event.target.value))
              onPeriodChange(event.target.value);
          }}
        />
      </label>
      <span
        className={
          'workspace-context-lock' +
          (lockLabel === '已锁期' ? ' locked' : '') +
          (lockLabel === '锁期状态待核实' ? ' unknown' : '')
        }
      >
        {lockLabel}
      </span>
    </section>
  );
}
