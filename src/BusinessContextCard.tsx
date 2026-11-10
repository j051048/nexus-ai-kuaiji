import { useEffect, useRef, useState } from 'react';
import { api, ApiError } from './api';
import { useScopedQuery } from './useScopedQuery';
import { assertBusinessContext } from './business-context';
import { evidenceChange, type EvidenceRelationships } from './business-evidence';
import type { BusinessEvent } from './business-types';
import type { Session } from './types';
import './business-context-card.css';

export type BusinessFocusTarget = 'originals' | 'differences' | 'actions';

/** This mounted card belongs to one version. Its buttons only navigate. */
export function BusinessContextCard({
  session,
  event,
  busy,
  revision = 0,
  onFocus,
  onRefresh,
}: {
  session: Session;
  event: BusinessEvent;
  busy: boolean;
  revision?: number;
  onFocus: (target: BusinessFocusTarget, issueCode?: string) => void;
  onRefresh: () => void;
}) {
  const [revoked, setRevoked] = useState(false);
  const [stale, setStale] = useState(false);
  const previous = useRef<EvidenceRelationships | null>(null);
  const [change, setChange] = useState<ReturnType<typeof evidenceChange> | null>(null);
  const period = event.payload.occurred_on.slice(0, 7);
  const query = useScopedQuery(async (signal) => {
    try {
      const value = await api<unknown>(
        `/agent-workspace/context/events/${encodeURIComponent(event.id)}?${new URLSearchParams({ company_id: event.company_id, period, expected_version: String(event.version) })}`,
        session,
        { signal },
      );
      const accepted = assertBusinessContext(value, {
        firmId: event.firm_id,
        companyId: event.company_id,
        eventId: event.id,
        period,
        version: event.version,
        originals: event.attachments,
      });
      return { ...accepted, readRevision: revision };
    } catch (failure) {
      if (!signal.aborted && failure instanceof ApiError && failure.status === 409) setStale(true);
      throw failure;
    }
  }, `${session.scope_key}:${event.id}:${event.version}:${revision}`);
  useEffect(() => {
    if (!query.data) return;
    setStale(false);
    setChange(previous.current ? evidenceChange(previous.current, query.data.relationships) : null);
    previous.current = query.data.relationships;
  }, [query.data]);
  useEffect(() => {
    if (query.error || revoked) {
      previous.current = null;
      setChange(null);
    }
  }, [query.error, revoked]);
  useEffect(() => {
    const invalidate = (signal: Event) => {
      if ((signal as CustomEvent).detail === session.scope_key) setRevoked(true);
    };
    window.addEventListener('accounting:forbidden', invalidate);
    window.addEventListener('accounting:unauthorized', invalidate);
    return () => {
      window.removeEventListener('accounting:forbidden', invalidate);
      window.removeEventListener('accounting:unauthorized', invalidate);
    };
  }, [session.scope_key]);
  const refreshButton = (
    <button
      type="button"
      className="text-button business-context-refresh"
      disabled={busy || query.loading}
      onClick={() => {
        if (stale) onRefresh();
        else {
          setRevoked(false);
          query.reload();
        }
      }}
    >
      {stale ? '刷新事项及依据' : '重新读取办理依据'}
    </button>
  );
  // The old query state exists for one render before the scope effect clears it.
  const card =
    revoked ||
    query.data?.scope.event_version !== event.version ||
    query.data?.readRevision !== revision
      ? null
      : query.data;
  return (
    <section className="business-context-card" aria-label="本事项办理助手">
      <header>
        <strong>本事项办理助手</strong>
        <span>
          {event.company_name} · {period} · v{event.version}
        </span>
      </header>
      {card ? (
        <>
          <p className="business-context-next">
            <strong>下一步 · {card.next_step.owner}</strong>
            {card.next_step.detail}
          </p>
          {change && (
            <p className="field-help" role="status">
              已重新核对：{change.handled} 项已记录人工核对，{change.absent} 项已不再检出，
              {change.remaining} 项仍需人工办理。此摘要不代表复核或交付完成。
            </p>
          )}
          {!!card.differences.length && (
            <p className="business-context-difference">
              {card.differences[0].message}
              {card.differences.length > 1 ? `（共 ${card.differences.length} 项）` : ''}
            </p>
          )}
          {!!card.relationships.issues.filter((issue) => !issue.resolved).length && (
            <details>
              <summary>展开待核对事项 · {card.differences.length} 项</summary>
              {card.relationships.issues
                .filter((issue) => !issue.resolved)
                .map((issue) => (
                  <article className="business-context-issue" key={issue.id}>
                    <strong>{issue.message}</strong>
                    {issue.comparison.state === 'conflict' && (
                      <p>
                        事项金额 ¥{issue.comparison.left} · 票据合计 ¥{issue.comparison.right} ·
                        差额 ¥{issue.comparison.delta}
                      </p>
                    )}
                    <p>
                      {issue.owner} · {issue.next_action}
                    </p>
                    <button
                      type="button"
                      className="button secondary small"
                      disabled={busy}
                      onClick={() => onFocus(issue.target, issue.code)}
                    >
                      去核对此项
                    </button>
                    {issue.can_handle && (
                      <button
                        type="button"
                        className="text-button"
                        disabled={busy}
                        onClick={() => onFocus('actions')}
                      >
                        去办理
                      </button>
                    )}
                  </article>
                ))}
            </details>
          )}
          <div className="business-context-controls">
            <button
              className="button secondary small"
              type="button"
              disabled={busy || !card.evidence.length}
              onClick={() => onFocus('originals')}
            >
              核对原件
            </button>
            <button
              className="button secondary small"
              type="button"
              disabled={busy}
              onClick={() => onFocus('differences')}
            >
              差异与依据{card.differences.length ? ` · ${card.differences.length} 项待核对` : ''}
            </button>
            {card.next_step.target && (
              <button
                className="button secondary small"
                type="button"
                disabled={busy}
                onClick={() => onFocus('actions')}
              >
                {card.next_step.label}
              </button>
            )}
          </div>
          <details>
            <summary>查看依据范围</summary>
            <p>{card.coverage}</p>
            <p>
              当前有效原件来源 {card.relationships.original_roots.length}{' '}
              份；识别转录不增加独立来源。历史资料不作为本次办理授权。
            </p>
            <ul>
              {card.evidence.map((original) => (
                <li key={original.attachment_id}>{original.filename}</li>
              ))}
            </ul>
            <p>办理后可重新核对；退回、指派或跳转不会自动消除差异。</p>
            {refreshButton}
          </details>
        </>
      ) : query.loading && !revoked ? (
        <p role="status">正在读取当前事项依据…</p>
      ) : (
        <p role="alert">{revoked ? '权限已变化，办理依据已清空。请重新确认授权。' : query.error}</p>
      )}
      {!card && refreshButton}
    </section>
  );
}
