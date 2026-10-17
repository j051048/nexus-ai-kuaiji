import { useCallback, useEffect, useRef, useState } from 'react';
import {
  CalendarCheck2,
  CheckCircle2,
  History,
  LockKeyhole,
  RefreshCw,
  ShieldCheck,
  TriangleAlert,
} from 'lucide-react';
import { api } from './api';
import type { Session } from './types';
import { canConfirmClose, closingCategories, closingTarget } from './closing-types';
import type { ClosingOverview } from './closing-types';
import './closing.css';

export function ClosingWorkbench({
  session,
  bookId,
  onBack,
  onBusyChange,
  onChanged,
  onOpenIssue,
  embedded = false,
}: {
  session: Session;
  bookId: string;
  onBack: () => void;
  onBusyChange?: (busy: boolean) => void;
  onChanged?: () => void;
  onOpenIssue?: (target: { type: string; id: string; bookId: string }) => void;
  embedded?: boolean;
}) {
  const [data, setData] = useState<ClosingOverview | null>(null),
    [selected, setSelected] = useState(''),
    [busy, setBusy] = useState(false);
  const [reason, setReason] = useState(''),
    [confirmed, setConfirmed] = useState(false),
    [error, setError] = useState(''),
    [notice, setNotice] = useState('');
  const scope = `${session.scope_key}:${bookId}`,
    scopeRef = useRef(scope);
  scopeRef.current = scope;
  const lock = useRef(false),
    mounted = useRef(true),
    epoch = useRef(0),
    resetScope = useRef('');
  const path = `/books/${encodeURIComponent(bookId)}/closing`;
  const load = useCallback(async () => {
    const current = ++epoch.current,
      saved = `${session.scope_key}:${bookId}`;
    const result = await api<ClosingOverview>(
      `/books/${encodeURIComponent(bookId)}/closing`,
      session,
    );
    if (mounted.current && current === epoch.current && saved === scopeRef.current) {
      setData(result);
      setError('');
    }
  }, [bookId, session]);
  useEffect(() => {
    const changed = resetScope.current !== scope;
    resetScope.current = scope;
    mounted.current = true;
    if (changed) {
      lock.current = false;
      setBusy(false);
      setSelected('');
      setReason('');
      setConfirmed(false);
      setError('');
      setNotice('');
      setData(null);
    }
    void load().catch((failure) => {
      if (mounted.current && scopeRef.current === scope)
        setError(failure instanceof Error ? failure.message : '读取失败');
    });
    return () => {
      mounted.current = false;
      epoch.current++;
    };
  }, [load, scope]);
  useEffect(() => {
    onBusyChange?.(busy);
    return () => onBusyChange?.(false);
  }, [busy, onBusyChange]);
  async function run(action: () => Promise<string>) {
    if (lock.current) return;
    const saved = scope;
    lock.current = true;
    setBusy(true);
    setError('');
    setNotice('');
    try {
      const text = await action();
      if (mounted.current && saved === scopeRef.current) {
        setNotice(text);
        await load();
        onChanged?.();
      }
    } catch (failure) {
      if (mounted.current && saved === scopeRef.current)
        setError(failure instanceof Error ? failure.message : '操作未完成，请刷新后重新预检');
    } finally {
      if (saved === scopeRef.current) {
        lock.current = false;
        if (mounted.current) setBusy(false);
      }
    }
  }
  const snapshot = data?.snapshots.find((item) => item.id === selected) ?? null;
  const report = snapshot?.report ?? data?.current_report;
  const Wrapper = embedded ? 'div' : 'main';
  return (
    <Wrapper className={'closing-workbench' + (embedded ? ' embedded' : '')}>
      {embedded ? (
        <section className="book-embedded-heading">
          <div>
            <h2>月结预检与锁期</h2>
            <p>核对资料、任务、匹配和交付结果，确认当前快照后才锁定期间。</p>
          </div>
          <button
            className="button secondary small"
            disabled={busy}
            onClick={() => void run(async () => '已读取最新账务与月结状态。')}
          >
            <RefreshCw size={15} />
            刷新
          </button>
        </section>
      ) : (
        <section className="page-heading">
          <div>
            <p className="eyebrow">MONTH-END REVIEW</p>
            <h1>先核清，再合上这个月。</h1>
            <p className="page-description">
              月结预检核对资料、任务、匹配和交付结果。会计确认当前快照后，才锁定期间。
            </p>
          </div>
          <div className="closing-actions">
            <button className="button secondary" disabled={busy} onClick={onBack}>
              返回账套
            </button>
            <button
              className="button secondary"
              disabled={busy}
              onClick={() => void run(async () => '已读取最新账务与月结状态。')}
            >
              <RefreshCw size={15} />
              刷新
            </button>
          </div>
        </section>
      )}
      {error && (
        <p className="inline-error" role="alert">
          <TriangleAlert size={16} />
          {error}
        </p>
      )}
      {notice && (
        <p className="form-success" role="status">
          {notice}
        </p>
      )}
      {data && (
        <>
          <section className="closing-period">
            <CalendarCheck2 size={25} />
            <div>
              <strong>{data.book_name}</strong>
              <p>
                {data.period} · {data.period_status === 'open' ? '开放期间' : '已锁定期间'}
              </p>
            </div>
            <span className={`badge ${data.current_report.passed ? 'approved' : 'exception'}`}>
              {data.period_status === 'closed'
                ? '历史月结'
                : data.current_report.passed
                  ? '当前检查通过'
                  : `${data.current_report.issue_count} 项待处理`}
            </span>
          </section>
          <div className="closing-columns">
            <section className="panel">
              <div className="panel-heading">
                <div>
                  <h2>预检与复核快照</h2>
                  <p>每次快照独立留存。事实或回执变化后，旧快照不能继续关期。</p>
                </div>
                <History size={19} />
              </div>
              <div className="closing-snapshots">
                <button
                  className={`closing-snapshot ${!selected ? 'selected' : ''}`}
                  disabled={busy}
                  onClick={() => {
                    setSelected('');
                    setConfirmed(false);
                  }}
                >
                  当前检查结果<small>实时重新计算，尚未冻结为快照</small>
                </button>
                {data.snapshots.map((item) => (
                  <button
                    className={`closing-snapshot ${selected === item.id ? 'selected' : ''}`}
                    key={item.id}
                    disabled={busy}
                    onClick={() => {
                      setSelected(item.id);
                      setConfirmed(false);
                      setReason('');
                    }}
                  >
                    <span>{item.created_at.slice(0, 19).replace('T', ' ')}</span>
                    <span
                      className={`badge ${item.report.passed && !item.stale ? 'approved' : 'exception'}`}
                    >
                      {item.historical
                        ? '历史留存'
                        : item.stale
                          ? '事实已变化'
                          : item.report.passed
                            ? '检查通过'
                            : '存在阻断'}
                    </span>
                    <small>
                      {item.report.issue_count} 项阻断 ·{' '}
                      {item.reviewed_by_me ? '本人已明确复核' : '尚待本人复核'}
                    </small>
                  </button>
                ))}
              </div>
              {data.period_status === 'open' && (
                <div className="closing-preflight">
                  <button
                    className="button primary"
                    disabled={busy}
                    onClick={() =>
                      void run(async () => {
                        const result = await api<{ id: string }>(`${path}/preflights`, session, {
                          method: 'POST',
                          body: { idempotency_key: crypto.randomUUID() },
                        });
                        if (scopeRef.current === scope) {
                          setSelected(result.id);
                          setConfirmed(false);
                          setReason('');
                        }
                        return '预检快照已保存，请先处置阻断，再明确复核。';
                      })
                    }
                  >
                    <CalendarCheck2 size={16} />
                    生成月结预检快照
                  </button>
                </div>
              )}
            </section>
            <section className="panel">
              <div className="panel-heading">
                <div>
                  <h2>{snapshot ? '快照中的核对结果' : '当前核对结果'}</h2>
                  <p>
                    {snapshot?.stale
                      ? '这份结果已是历史事实，请重新生成预检。'
                      : '每项阻断都需要业务、财务或会计明确处理。'}
                  </p>
                </div>
                <ShieldCheck size={19} />
              </div>
              {report && (
                <div className="closing-checks">
                  {Object.entries(closingCategories).map(([key, label]) => (
                    <div key={key}>
                      <span>{label}</span>
                      {report.counts[key] ? (
                        <strong className="closing-blocked">{report.counts[key]} 项待处理</strong>
                      ) : (
                        <span className="closing-clear">
                          <CheckCircle2 size={14} />
                          已通过
                        </span>
                      )}
                    </div>
                  ))}
                </div>
              )}
              {report && !!report.issues.length && (
                <div className="closing-issues">
                  {report.issues.map((issue, index) => (
                    <article key={`${issue.category}:${issue.object_id}:${index}`}>
                      <span>{closingCategories[issue.category] || issue.category}</span>
                      <p>{issue.detail}</p>
                      {closingTarget(issue.object_type, issue.object_id) && onOpenIssue && (
                        <button
                          className="text-button"
                          disabled={busy}
                          onClick={() =>
                            onOpenIssue({ type: issue.object_type, id: issue.object_id, bookId })
                          }
                        >
                          打开对应事项 →
                        </button>
                      )}
                    </article>
                  ))}
                  {report.truncated && <p>当前显示前 500 项，请先处理所列事项，再重新预检。</p>}
                </div>
              )}
            </section>
          </div>
          {snapshot && data.period_status === 'open' && (
            <section className="panel closing-confirm">
              <div>
                <h2>明确复核，随后锁期</h2>
                <p>{data.coverage_note}</p>
                <small>事实指纹 {snapshot.input_fingerprint}</small>
              </div>
              <form onSubmit={(event) => event.preventDefault()}>
                <label className="closing-coverage">
                  <input
                    type="checkbox"
                    checked={confirmed}
                    disabled={
                      busy || snapshot.stale || !snapshot.report.passed || snapshot.reviewed_by_me
                    }
                    onChange={(event) => setConfirmed(event.target.checked)}
                  />
                  我已核对本期资料与银行流水完整性，确认当前预检没有遗漏
                </label>
                <label>
                  月结复核依据
                  <textarea
                    aria-label="月结复核依据"
                    rows={3}
                    minLength={3}
                    maxLength={1000}
                    value={reason}
                    disabled={busy}
                    onChange={(event) => setReason(event.target.value)}
                  />
                </label>
                <div className="closing-actions">
                  <button
                    className="button secondary"
                    disabled={
                      busy ||
                      snapshot.stale ||
                      !snapshot.report.passed ||
                      !confirmed ||
                      reason.trim().length < 3 ||
                      snapshot.reviewed_by_me
                    }
                    onClick={() =>
                      void run(async () => {
                        await api(
                          `${path}/snapshots/${encodeURIComponent(snapshot.id)}/review`,
                          session,
                          { method: 'POST', body: { reason, materials_confirmed: true } },
                        );
                        return '当前快照已明确复核。锁期时会再次核对事实和权限。';
                      })
                    }
                  >
                    <CheckCircle2 size={16} />
                    复核当前快照
                  </button>
                  <button
                    className="button primary"
                    disabled={
                      busy ||
                      !canConfirmClose(snapshot, data.period_status) ||
                      reason.trim().length < 3
                    }
                    onClick={() =>
                      void run(async () => {
                        await api(`${path}/close`, session, {
                          method: 'POST',
                          body: { snapshot_id: snapshot.id, reason },
                        });
                        return '本期已依据当前复核快照锁定，月结历史已留存。';
                      })
                    }
                  >
                    <LockKeyhole size={16} />
                    依据复核快照锁期
                  </button>
                </div>
              </form>
            </section>
          )}
          {data.receipt && (
            <section className="closing-receipt">
              <LockKeyhole size={22} />
              <div>
                <strong>本期已锁定</strong>
                <p>{data.receipt.reason}</p>
                <small>
                  关闭时间 {data.receipt.created_at} · 快照 {data.receipt.snapshot_id}
                </small>
              </div>
            </section>
          )}
        </>
      )}
    </Wrapper>
  );
}
