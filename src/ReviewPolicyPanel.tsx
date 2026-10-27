import { useCallback, useEffect, useRef, useState } from 'react';
import { api, errorMessage } from './api';
import type { Session } from './types';
import './review-policy.css';
import {
  parseReviewPolicy,
  reviewModeLabel,
  type ReviewPolicyData,
  type ReviewPolicyMode,
} from './review-policy';

interface Props {
  session: Session;
  bookId: string;
  disabled?: boolean;
  onBusyChange?: (busy: boolean) => void;
  onChanged?: () => Promise<void>;
}
export function ReviewPolicyPanel(props: Props) {
  return <ReviewPolicyContent key={`${props.session.scope_key}:${props.bookId}`} {...props} />;
}
function ReviewPolicyContent({
  session,
  bookId,
  disabled = false,
  onBusyChange,
  onChanged,
}: Props) {
  const [data, setData] = useState<ReviewPolicyData | null>(null);
  const [mode, setMode] = useState<ReviewPolicyMode>('independent');
  const [reason, setReason] = useState('');
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const live = useRef(true),
    locked = useRef(false);
  const path = `/books/${encodeURIComponent(bookId)}/review-policy`;
  useEffect(() => {
    live.current = true;
    return () => {
      live.current = false;
    };
  }, []);
  useEffect(() => {
    onBusyChange?.(busy);
    return () => onBusyChange?.(false);
  }, [busy, onBusyChange]);
  const load = useCallback(
    async (signal?: AbortSignal) => {
      setLoading(true);
      setError('');
      setData(null);
      try {
        const value = parseReviewPolicy(await api<unknown>(path, session, { signal }), bookId);
        if (live.current && !signal?.aborted) {
          setData(value);
          setMode(value.policy.mode);
        }
      } catch (failure) {
        if (live.current && !signal?.aborted) setError(errorMessage(failure));
      } finally {
        if (live.current && !signal?.aborted) setLoading(false);
      }
    },
    [path, session, bookId],
  );
  useEffect(() => {
    const controller = new AbortController();
    void load(controller.signal);
    return () => controller.abort();
  }, [load]);
  return (
    <section className="review-section review-policy-panel" aria-label="账套复核政策">
      <h3>制单与复核分工</h3>
      <p>
        默认由不同会计制单与复核。单人办理须明确留痕，不标为独立复核；政策修改不改写历史审核结果。
      </p>
      {loading && <p role="status">正在读取当前账套政策…</p>}
      {error && (
        <p className="form-error" role="alert">
          {error} 请重新读取当前账套，确认权限和政策版本。
        </p>
      )}
      {notice && <p role="status">{notice}</p>}
      {data && (
        <>
          <p>
            <strong>{reviewModeLabel(data.policy.mode)}</strong> · 政策版本 {data.policy.version}
          </p>
          <p>设置说明：{data.policy.reason || '默认独立复核政策'}</p>
          {data.can_manage && !disabled ? (
            <form
              onSubmit={async (event) => {
                event.preventDefault();
                if (locked.current || reason.trim().length < 3 || mode === data.policy.mode) return;
                locked.current = true;
                setBusy(true);
                setError('');
                setNotice('');
                try {
                  const value = parseReviewPolicy(
                    await api<unknown>(path, session, {
                      method: 'POST',
                      body: { expected_version: data.policy.version, mode, reason: reason.trim() },
                    }),
                    bookId,
                  );
                  if (!live.current) return;
                  setData(value);
                  setMode(value.policy.mode);
                  setReason('');
                  setNotice('政策已保存。请重新读取待审凭证与批量预览后按新政策办理。');
                  await onChanged?.();
                } catch (failure) {
                  if (live.current) setError(errorMessage(failure));
                } finally {
                  if (live.current) {
                    locked.current = false;
                    setBusy(false);
                  }
                }
              }}
            >
              <label>
                办理方式
                <select
                  value={mode}
                  disabled={busy}
                  onChange={(event) => setMode(event.target.value as ReviewPolicyMode)}
                >
                  <option value="independent">独立复核：制单与复核由不同人员负责</option>
                  <option value="single_operator">单人办理（非独立复核）</option>
                </select>
              </label>
              <label>
                变更原因
                <textarea
                  value={reason}
                  maxLength={1000}
                  disabled={busy}
                  onChange={(event) => setReason(event.target.value)}
                  placeholder="说明人员安排及风险承担，至少 3 个字"
                />
              </label>
              <button
                className="button secondary"
                disabled={busy || mode === data.policy.mode || reason.trim().length < 3}
              >
                {busy ? '正在保存…' : '保存复核政策'}
              </button>
            </form>
          ) : (
            <p>由事务所负责人或管理员管理复核政策。</p>
          )}
        </>
      )}
      <button className="text-button" disabled={busy || loading} onClick={() => void load()}>
        重新读取政策
      </button>
    </section>
  );
}
