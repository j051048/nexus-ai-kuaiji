import { useEffect, useRef, useState } from 'react';
import { api, errorMessage } from './api';
import { useScopedQuery } from './useScopedQuery';
import type { Session } from './types';

type Item = {
  kind: 'draft' | 'manual' | 'periodic';
  subject_id: string;
  book_id: string;
  source_version: number;
  policy_version: number;
  content_fingerprint: string;
  mode: 'independent' | 'single_operator';
  can_prepare: boolean;
  can_confirm: boolean;
  blocked_reason: string | null;
  preparation: { id: string; maker_user_id: string; reason: string } | null;
  content: unknown;
};
type Props = { session: Session; bookId: string };
const labels = { draft: '来源凭证', manual: '手工凭证', periodic: '期末凭证' };

export function LegacyReviewPanel(props: Props) {
  return (
    <LegacyReviewContent
      key={`${props.session.scope_key}:${props.session.user_id}:${props.bookId}`}
      {...props}
    />
  );
}

function LegacyReviewContent({ session, bookId }: Props) {
  const query = useScopedQuery(async (signal) => {
    const value = await api<{ book_id: string; items: Item[] }>(
      `/books/${bookId}/legacy-reviews`,
      session,
      { signal },
    );
    if (
      value.book_id !== bookId ||
      !Array.isArray(value.items) ||
      value.items.some((r) => r.book_id !== bookId)
    )
      throw new Error('返回范围不一致，请重新读取');
    return value;
  }, `${session.scope_key}:${bookId}`);
  const [reason, setReason] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const controller = useRef<AbortController | null>(null);
  useEffect(() => () => controller.current?.abort(), []);
  async function act(item: Item, confirm: boolean) {
    if (busy || reason.trim().length < 3) return;
    const current = new AbortController();
    controller.current = current;
    setBusy(true);
    setError('');
    try {
      const path = confirm
        ? `/legacy-reviews/${item.preparation!.id}/confirm`
        : `/books/${bookId}/legacy-reviews/${item.kind}/${item.subject_id}/prepare`;
      await api(path, session, {
        method: 'POST',
        signal: current.signal,
        body: confirm
          ? { reason }
          : {
              expected_version: item.source_version,
              content_fingerprint: item.content_fingerprint,
              expected_policy_version: item.policy_version,
              reason,
            },
      });
      if (!current.signal.aborted) {
        setReason('');
        query.reload();
      }
    } catch (failure) {
      if (!current.signal.aborted) {
        setError(errorMessage(failure));
        query.reload();
      }
    } finally {
      if (!current.signal.aborted) setBusy(false);
    }
  }
  return (
    <section aria-label="旧稿当前版本补核">
      <p>
        旧稿历史审核依据未核定时，先认领当前内容，再按现行政策重新人工复核。原审核记录保留；这次复核记为今天，不能补签历史日期。已记账记录请走冲销或调整。
      </p>
      {query.loading && <p role="status">正在读取本账套旧稿…</p>}
      {query.error && (
        <p role="alert">
          {query.error}{' '}
          <button type="button" className="button secondary" onClick={query.reload}>
            重新读取
          </button>
        </p>
      )}
      {error && <p role="alert">{error}。请刷新版本或联系本企业负责人，不能自动重试确认。</p>}
      {query.data && query.data.items.length === 0 && <p>当前账套没有需要补核的旧稿。</p>}
      {!!query.data?.items.length && (
        <label>
          本次核对依据与理由（至少 3 字）
          <textarea
            maxLength={1000}
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            disabled={busy}
          />
        </label>
      )}
      {query.data?.items.map((item) => (
        <article className="review-section" key={`${item.kind}:${item.subject_id}`}>
          <h3>
            {labels[item.kind]} · 当前版本 {item.source_version}
          </h3>
          <p>
            {item.mode === 'independent' ? '须另一位事务所会计独立复核' : '单人办理，非独立复核'} ·{' '}
            {item.preparation ? '已认领当前版本，等待人工复核' : '待认领当前制单责任'}
          </p>
          <details>
            <summary>核对原内容与历史记录</summary>
            <pre className="legacy-review-evidence">{JSON.stringify(item.content, null, 2)}</pre>
          </details>
          {item.blocked_reason ? (
            <p>{item.blocked_reason}</p>
          ) : item.preparation ? (
            <>
              <p>
                当前制单责任人：{item.preparation.maker_user_id} · {item.preparation.reason}
              </p>
              <button
                type="button"
                className="button secondary"
                disabled={busy || !item.can_confirm || reason.trim().length < 3}
                onClick={() => void act(item, true)}
              >
                确认本次人工复核
              </button>
            </>
          ) : (
            <button
              type="button"
              className="button secondary"
              disabled={busy || !item.can_prepare || reason.trim().length < 3}
              onClick={() => void act(item, false)}
            >
              核对内容并认领当前版本
            </button>
          )}
        </article>
      ))}
    </section>
  );
}
