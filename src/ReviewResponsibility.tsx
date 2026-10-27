import { useEffect, useRef, useState } from 'react';
import { api, errorMessage } from './api';
import type { Draft, Session } from './types';
import './review-policy.css';
import { parseReviewResponsibility, reviewModeLabel } from './review-policy';

export function ReviewResponsibility({
  value,
  version,
  userId,
}: {
  value: unknown;
  version: number;
  userId: string;
}) {
  const review = parseReviewResponsibility(value, version);
  if (!review)
    return (
      <aside className="permission-note" role="status">
        当前版本的复核政策尚未核验，请重新读取；暂不能确认审核。
      </aside>
    );
  const self = review.maker_user_id === userId && review.mode === 'independent';
  return (
    <aside className="review-section review-responsibility" aria-label="制单与复核责任">
      <strong>
        {review.state === 'legacy_unverified'
          ? '历史审核（独立性待核实）'
          : reviewModeLabel(review.mode)}{' '}
        · 政策版本 {review.policy_version}
      </strong>
      <p>
        版本 {review.draft_version} · 制单责任：
        {review.maker_name || review.maker_user_id || '制单责任待确认'}
      </p>
      {review.state === 'unattributed' ? (
        <p>请由实际制单会计核对内容并认领本版本责任，再按政策交给复核员。</p>
      ) : review.state === 'legacy_unverified' ? (
        <p>历史记录的复核证据未核实，不标记为独立复核；请联系负责会计核对历史记录。</p>
      ) : review.state === 'reviewed' ? (
        <p>
          {review.independent === true
            ? '已独立复核'
            : review.independent === false && review.mode === 'single_operator'
              ? '已按单人办理政策审核（非独立复核）'
              : '审核结果的独立性证据待核实'}{' '}
          · {review.reviewer_name || review.reviewer_user_id || '审核责任待核实'} · 待审版本{' '}
          {review.reviewed_version ?? '待核实'}
        </p>
      ) : (
        <p>
          {self
            ? '本版本由你制单，请交由另一名有复核权限的会计审核。'
            : review.blocked_reason ||
              (review.mode === 'single_operator'
                ? '确认审核时须说明单人办理依据，系统保留真实制单与审核责任。'
                : '请由制单人之外、有复核权限的会计核对后确认。')}
        </p>
      )}
    </aside>
  );
}

/** Explicit legacy attribution, never inferred from the current viewer. */
export function ClaimPreparation({
  session,
  bookId,
  draft,
  disabled,
  onChanged,
}: {
  session: Session;
  bookId: string;
  draft: Draft;
  disabled: boolean;
  onChanged: (value: Draft) => void;
}) {
  const [reason, setReason] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const live = useRef(true),
    locked = useRef(false);
  useEffect(() => {
    live.current = true;
    return () => {
      live.current = false;
    };
  }, []);
  const review = parseReviewResponsibility(draft.review_responsibility, draft.version);
  if (draft.status !== 'draft' || review?.state !== 'unattributed') return null;
  return (
    <section className="review-section review-responsibility-claim" aria-label="确认制单责任">
      <label>
        认领说明
        <textarea
          value={reason}
          maxLength={1000}
          disabled={disabled || busy}
          onChange={(event) => setReason(event.target.value)}
          placeholder="说明已核对内容及由你承担本版本制单责任的依据"
        />
      </label>
      {error && (
        <p className="form-error" role="alert">
          {error}
        </p>
      )}
      <button
        className="button secondary"
        disabled={disabled || busy || reason.trim().length < 3}
        onClick={async () => {
          if (locked.current || disabled || reason.trim().length < 3) return;
          locked.current = true;
          setBusy(true);
          setError('');
          try {
            const value = await api<Draft>(
              `/books/${encodeURIComponent(bookId)}/drafts/${encodeURIComponent(draft.id)}/claim-preparation`,
              session,
              { method: 'POST', body: { expected_version: draft.version, reason: reason.trim() } },
            );
            if (value.id !== draft.id) throw new Error('责任记录范围不一致，请重新读取。');
            if (live.current) onChanged(value);
          } catch (failure) {
            if (live.current) setError(errorMessage(failure));
          } finally {
            if (live.current) {
              setBusy(false);
              locked.current = false;
            }
          }
        }}
      >
        {busy ? '正在确认…' : '确认由我制单并认领本版本'}
      </button>
    </section>
  );
}
