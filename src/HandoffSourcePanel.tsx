import { useEffect, useState } from 'react';
import { api } from './api';
import { acceptHandoffs, type Handoff } from './batch-operations';
import { readHandoffTarget } from './handoff-target';
import { useOperationRequests } from './useOperationRequests';
import { BatchOperationsDialog } from './BatchOperationsDialog';
import { DeliveryNotifications } from './DeliveryNotifications';
import { MonthlyPackagePanel } from './MonthlyPackagePanel';
import { TaxWorkpaperPanel } from './TaxWorkpaperPanel';
import type { Session } from './types';

/** The URL selects an immutable source. Current authority is always re-read. */
export function HandoffSourcePanel({
  session,
  search,
  onClose,
}: {
  session: Session;
  search: string;
  onClose: () => void;
}) {
  const [item, setItem] = useState<Handoff | null>(null);
  const { run, busy, error, denied } = useOperationRequests(session);
  const load = () => {
    setItem(null);
    void run(async (signal) => {
      const target = readHandoffTarget(search);
      if (!target) throw new Error('请从接手队列打开明确的原记录。');
      const query = new URLSearchParams({
        expected_version: String(target.version),
        expected_fingerprint: target.fingerprint,
      });
      const raw = await api<unknown>(
        `/handoff/sources/${target.kind}/${encodeURIComponent(target.id)}?${query}`,
        session,
        { signal },
      );
      const value = acceptHandoffs({ items: [raw] })[0];
      const scope = new URLSearchParams(search);
      if (
        value.source.kind !== target.kind ||
        value.source.id !== target.id ||
        value.source.version !== target.version ||
        value.source.fingerprint !== target.fingerprint ||
        value.scope.period !== scope.get('period') ||
        (scope.has('company') && value.scope.company_id !== scope.get('company')) ||
        (value.scope.book_id ?? '') !== (scope.get('book') ?? '')
      )
        throw new Error('原记录范围或版本不一致，已停止显示；请返回队列核对。');
      return value;
    }, setItem);
  };
  useEffect(() => {
    load();
  }, []);
  const visible = denied ? null : item;
  return (
    <section className="panel" aria-label="接手原记录">
      <button className="button secondary" onClick={onClose}>
        返回接手队列
      </button>
      <button className="button secondary" disabled={busy || denied} onClick={load}>
        重新核对来源
      </button>
      {error && <p role="alert">{error}</p>}
      {busy && <p role="status">正在核对原记录与当前权限…</p>}
      {visible && (
        <>
          <h2>
            {visible.scope.company_name} · 来源版本 {visible.source.version}
          </h2>
          <p>{visible.next_action}</p>
          {visible.assignment_blocker && <p>{visible.assignment_blocker}</p>}
          {visible.source.kind === 'batch_item' ? (
            <BatchOperationsDialog
              session={session}
              period={visible.scope.period}
              rows={[]}
              initialBatchId={visible.scope.batch_id}
              focusItem={{
                id: visible.source.id,
                version: visible.source.version,
                companyId: visible.scope.company_id!,
                fingerprint: visible.source.fingerprint,
              }}
              onClose={onClose}
            />
          ) : (
            visible.scope.book_id &&
            (visible.source.kind === 'notification_investigation' ? (
              <DeliveryNotifications
                session={session}
                bookId={visible.scope.book_id}
                period={visible.scope.period}
                focusNotification={{
                  id: visible.source.id,
                  attempts: visible.source.version - 1,
                  fingerprint: visible.source.fingerprint,
                }}
              />
            ) : visible.source.kind === 'delivery_package' ? (
              <MonthlyPackagePanel
                session={session}
                bookId={visible.scope.book_id}
                focusPackageId={visible.source.id}
                expectedSource={visible.source}
              />
            ) : (
              visible.scope.company_id && (
                <TaxWorkpaperPanel
                  session={session}
                  bookId={visible.scope.book_id}
                  companyId={visible.scope.company_id}
                  period={visible.scope.period}
                  focusSource={visible.source}
                />
              )
            ))
          )}
        </>
      )}
    </section>
  );
}
