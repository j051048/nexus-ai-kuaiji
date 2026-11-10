import { useEffect, useState } from 'react';
import { api } from './api';
import { useScopedQuery } from './useScopedQuery';
import type { Session } from './types';
import './business-preparation.css';

type Preparation = {
  company_id: string;
  period: string;
  status: 'ready' | 'period_missing' | 'period_locked' | 'assignment_required';
  owner_role: string;
  next_action: string;
  advisory: true;
};
const labels = {
  ready: '可继续提交资料',
  period_missing: '等待会计开账',
  period_locked: '请会计确认补录方案',
  assignment_required: '请管理员安排交接权限',
};

/** A new scope unmounts the old advice, including all late requests. */
export function BusinessPreparation(props: {
  session: Session;
  companyId: string;
  occurredOn: string;
  version?: number;
  onRecheck?: () => void;
  compact?: boolean;
}) {
  if (!props.companyId || !/^20\d{2}-\d{2}-\d{2}$/.test(props.occurredOn)) return null;
  return (
    <PreparationRead
      key={`${props.session.scope_key}:${props.companyId}:${props.occurredOn}:${props.version ?? 0}`}
      {...props}
    />
  );
}

function PreparationRead({
  session,
  companyId,
  occurredOn,
  onRecheck,
  compact = false,
}: {
  session: Session;
  companyId: string;
  occurredOn: string;
  onRecheck?: () => void;
  compact?: boolean;
}) {
  const [invalidated, setInvalidated] = useState(false);
  const query = useScopedQuery(async (signal) => {
    const value = await api<Preparation>(
      `/business/preparation?${new URLSearchParams({ company_id: companyId, occurred_on: occurredOn })}`,
      session,
      { signal },
    );
    if (
      !value ||
      value.company_id !== companyId ||
      value.period !== occurredOn.slice(0, 7) ||
      !Object.prototype.hasOwnProperty.call(labels, value.status) ||
      value.advisory !== true ||
      !['finance', 'accountant', 'firm_admin'].includes(value.owner_role) ||
      typeof value.next_action !== 'string' ||
      !value.next_action.trim()
    ) {
      throw new Error('交接准备信息与当前企业或月份不符，请重新检查。');
    }
    return value;
  }, `${session.scope_key}:${companyId}:${occurredOn}`);
  useEffect(() => {
    const revoke = (event: Event) => {
      if ((event as CustomEvent).detail === session.scope_key) setInvalidated(true);
    };
    window.addEventListener('accounting:forbidden', revoke);
    window.addEventListener('accounting:unauthorized', revoke);
    return () => {
      window.removeEventListener('accounting:forbidden', revoke);
      window.removeEventListener('accounting:unauthorized', revoke);
    };
  }, [session.scope_key]);
  const data = invalidated ? null : query.data;
  const readySummary = compact && data?.status === 'ready';
  const Container = readySummary ? 'details' : 'section';
  return (
    <Container className="business-preparation" aria-label="交接准备" aria-live="polite">
      {readySummary ? (
        <summary>本月接收账套已就绪</summary>
      ) : (
        <strong>{data ? labels[data.status] : '交接准备'}</strong>
      )}
      <p className="field-help">
        {invalidated
          ? '权限已变化，请重新检查；无法读取时联系企业管理员。'
          : query.error || (query.loading ? '正在检查这个月份的接收准备…' : data?.next_action)}
      </p>
      <button
        type="button"
        className="text-button"
        disabled={query.loading}
        onClick={() => {
          setInvalidated(false);
          query.reload();
          onRecheck?.();
        }}
      >
        重新检查
      </button>
    </Container>
  );
}
