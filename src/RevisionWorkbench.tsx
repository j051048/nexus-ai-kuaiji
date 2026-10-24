import { useCallback, useEffect, useRef, useState } from 'react';
import {
  ArrowLeft,
  ArrowRight,
  FileClock,
  History,
  LoaderCircle,
  RefreshCw,
  ShieldCheck,
  TriangleAlert,
} from 'lucide-react';
import { api, errorMessage } from './api';
import { formatMoney } from './csv';
import type { Session } from './types';
import type { BusinessPayload } from './business-types';
import { revisionIsDirty } from './revision-progress';
import {
  revisionChanges,
  revisionStateLabel,
  revisionSubmitCommand,
  type FactRevision,
  type RevisionContext,
  type RevisionPage,
} from './revision-types';
import './revisions.css';

export function RevisionWorkbench({
  session,
  initialEventId = '',
  onBusyChange,
  onChanged,
  onBack,
  backLabel = '返回原业务事项',
  onDirtyChange,
}: {
  session: Session;
  initialEventId?: string;
  onBusyChange?: (busy: boolean) => void;
  onChanged?: () => void;
  onBack?: () => void;
  backLabel?: string;
  onDirtyChange?: (dirty: boolean) => void;
}) {
  const [page, setPage] = useState<RevisionPage | null>(null),
    [offset, setOffset] = useState(0);
  const [eventId, setEventId] = useState(initialEventId),
    [context, setContext] = useState<RevisionContext | null>(null);
  const [payload, setPayload] = useState<BusinessPayload | null>(null),
    [targetBook, setTargetBook] = useState('');
  const [reason, setReason] = useState(''),
    [reviewReason, setReviewReason] = useState('');
  const [selected, setSelected] = useState<FactRevision | null>(null),
    [error, setError] = useState(''),
    [notice, setNotice] = useState('');
  const [busy, setBusy] = useState(false),
    [loading, setLoading] = useState(false);
  const epoch = useRef(0),
    active = useRef(true),
    lock = useRef(false),
    scope = useRef(session.scope_key);
  const commandKey = useRef<{ identity: string; key: string } | null>(null);
  scope.current = session.scope_key;
  useEffect(() => {
    active.current = true;
    return () => {
      active.current = false;
      epoch.current += 1;
    };
  }, []);
  useEffect(() => {
    onBusyChange?.(busy);
    return () => onBusyChange?.(false);
  }, [busy, onBusyChange]);
  const load = useCallback(
    async (signal?: AbortSignal) => {
      const generation = ++epoch.current;
      setLoading(true);
      try {
        const data = await api<RevisionPage>(`/fact-revisions?limit=20&offset=${offset}`, session, {
          signal,
        });
        if (active.current && !signal?.aborted && generation === epoch.current) {
          setPage(data);
          setError('');
        }
      } catch (failure) {
        if (active.current && !signal?.aborted && generation === epoch.current)
          setError(errorMessage(failure));
      } finally {
        if (active.current && generation === epoch.current) setLoading(false);
      }
    },
    [offset, session],
  );
  useEffect(() => {
    setContext(null);
    setPayload(null);
    setSelected(null);
    setReason('');
    setReviewReason('');
    setNotice('');
    setPage(null);
    setOffset(0);
    commandKey.current = null;
  }, [session.scope_key]);
  useEffect(() => {
    const controller = new AbortController();
    void load(controller.signal);
    return () => controller.abort();
  }, [load]);
  const openEvent = useCallback(
    async (id: string, signal?: AbortSignal) => {
      if (!id.trim()) return;
      const started = session.scope_key;
      try {
        const data = await api<RevisionContext>(
          `/fact-revisions/context/${encodeURIComponent(id.trim())}`,
          session,
          { signal },
        );
        if (!active.current || signal?.aborted || scope.current !== started) return;
        setContext(data);
        setPayload(data.payload);
        setTargetBook(data.mapping.book_id);
        setReason('');
        setError('');
        commandKey.current = null;
      } catch (failure) {
        if (active.current && !signal?.aborted && scope.current === started) {
          setContext(null);
          setPayload(null);
          setError(errorMessage(failure));
        }
      }
    },
    [session],
  );
  useEffect(() => {
    setEventId(initialEventId);
    if (!initialEventId) return;
    const controller = new AbortController();
    void openEvent(initialEventId, controller.signal);
    return () => controller.abort();
  }, [initialEventId, openEvent]);
  async function run(identity: string, action: (key: string) => Promise<FactRevision>) {
    if (lock.current) return;
    const started = session.scope_key;
    lock.current = true;
    setBusy(true);
    setError('');
    setNotice('');
    if (commandKey.current?.identity !== identity)
      commandKey.current = { identity, key: crypto.randomUUID() };
    try {
      const result = await action(commandKey.current.key);
      if (!active.current || scope.current !== started) return;
      setSelected(result);
      setReviewReason('');
      setReason('');
      setTargetBook('');
      setNotice(revisionStateLabel[result.state]);
      // The acknowledged request is now shown in selected and in the history.
      // Only an unsuccessful/unknown submission keeps an editable local form.
      setContext(null);
      setPayload(null);
      onChanged?.();
      await load();
    } catch (failure) {
      if (active.current && scope.current === started) setError(errorMessage(failure));
    } finally {
      lock.current = false;
      if (active.current && scope.current === started) setBusy(false);
    }
  }
  function submit() {
    if (!context || !payload || !context.permissions.submit || context.pending) return;
    const identity = JSON.stringify([
      session.scope_key,
      context,
      payload,
      targetBook,
      reason.trim(),
    ]);
    void run(identity, (key) =>
      api<FactRevision>(`/fact-revisions/events/${encodeURIComponent(context.event_id)}`, session, {
        method: 'POST',
        body: revisionSubmitCommand(context, payload, targetBook, reason, key),
      }),
    );
  }
  function review(decision: 'accept' | 'reject') {
    if (!selected?.permissions.review) return;
    const revision = selected;
    const identity = JSON.stringify([
      session.scope_key,
      revision.id,
      revision.version,
      decision,
      reviewReason.trim(),
    ]);
    void run(identity, (key) =>
      api<FactRevision>(`/fact-revisions/${encodeURIComponent(revision.id)}/review`, session, {
        method: 'POST',
        body: {
          expected_version: revision.version,
          decision,
          reason: reviewReason.trim(),
          idempotency_key: key,
        },
      }),
    );
  }
  const changes = context && payload ? revisionChanges(context.payload, payload) : [];
  const unsaved = revisionIsDirty({ context, payload, targetBook, reason, reviewReason });
  useEffect(() => {
    onDirtyChange?.(unsaved);
    return () => onDirtyChange?.(false);
  }, [unsaved, onDirtyChange]);
  return (
    <div className="revision-workbench">
      {onBack && (
        <button
          type="button"
          className="text-button revision-back"
          disabled={busy}
          onClick={() => {
            if (
              !unsaved ||
              window.confirm('修订内容或原因尚未提交，返回会放弃本次编辑。是否继续？')
            )
              onBack();
          }}
        >
          <ArrowLeft size={15} /> {backLabel}
        </button>
      )}
      <header className="revision-header">
        <div>
          <span className="eyebrow">事实版本 · 独立审批</span>
          <h2>
            <FileClock size={23} />
            业务事实纠错
          </h2>
          <p>金额和日期有误时保留审批原件，提出修订，再由另一位会计核验。</p>
        </div>
        <button className="button secondary" disabled={busy || loading} onClick={() => void load()}>
          <RefreshCw size={16} />
          刷新清单
        </button>
      </header>
      <div className="revision-policy">
        <ShieldCheck size={18} />
        <p>
          未审核凭证会创建新的来源与草稿。已审核或已导出的凭证只生成调整建议，原账务保持冻结；调整建议处理前不能通过月结预检。
        </p>
      </div>
      {error && (
        <p role="alert" className="inline-error">
          {error}
        </p>
      )}
      {notice && (
        <p role="status" className="revision-notice">
          {notice}
        </p>
      )}
      <div className="revision-layout">
        <section className="panel revision-list">
          <div className="panel-heading">
            <h3>修订与调整清单</h3>
            <span>{page?.total ?? 0} 项</span>
          </div>
          {loading && (
            <p>
              <LoaderCircle className="spinner" size={16} />
              读取修订清单
            </p>
          )}
          {!loading && !page?.items.length && (
            <p className="empty-copy">暂无修订。发现原始业务事实有误时，可从业务事项打开纠错。</p>
          )}
          {page?.items.map((item) => (
            <button
              key={item.id}
              className={`revision-item ${selected?.id === item.id ? 'selected' : ''}`}
              disabled={busy}
              onClick={() => {
                setSelected(item);
                setReviewReason('');
              }}
            >
              <div>
                <strong>{item.proposed_payload.description}</strong>
                <span className={`revision-state ${item.state}`}>
                  {revisionStateLabel[item.state]}
                </span>
              </div>
              <span>
                {formatMoney(item.old_snapshot.payload.amount)} <ArrowRight size={12} />{' '}
                {formatMoney(item.proposed_payload.amount)} 元
              </span>
              <small>{item.reason}</small>
            </button>
          ))}
          <div className="pagination">
            <button
              className="button secondary small"
              disabled={busy || loading || offset === 0}
              onClick={() => setOffset(Math.max(0, offset - 20))}
            >
              上一页
            </button>
            <span>{Math.floor(offset / 20) + 1}</span>
            <button
              className="button secondary small"
              disabled={busy || loading || !page?.has_more}
              onClick={() => setOffset(offset + 20)}
            >
              下一页
            </button>
          </div>
        </section>
        <div className="revision-detail">
          {selected && (
            <section className="panel">
              <div className="panel-heading">
                <h3>
                  <History size={17} />
                  原事实与修订建议
                </h3>
                <span className={`revision-state ${selected.state}`}>
                  {revisionStateLabel[selected.state]}
                </span>
              </div>
              <p className="field-help">
                事项 {selected.event_id} · 事实版本 {selected.expected_fact_version} · 草稿版本{' '}
                {selected.expected_draft_version}
              </p>
              <table className="revision-diff">
                <thead>
                  <tr>
                    <th>字段</th>
                    <th>原审批事实</th>
                    <th>提议修订</th>
                  </tr>
                </thead>
                <tbody>
                  {revisionChanges(selected.old_snapshot.payload, selected.proposed_payload).map(
                    (item) => (
                      <tr key={item.key}>
                        <th>{item.label}</th>
                        <td>{item.before || '—'}</td>
                        <td>{item.after || '—'}</td>
                      </tr>
                    ),
                  )}
                </tbody>
              </table>
              {selected.target_book_id !== selected.old_book_id && (
                <p className="revision-warning">
                  <TriangleAlert size={15} />
                  日期跨会计期间，复核人须同时具备原账套和目标账套复核权限。
                </p>
              )}
              <p>
                <strong>提议依据：</strong>
                {selected.reason}
              </p>
              <p className="field-help">
                留存原附件 {selected.old_snapshot.attachments.length} 份，审批提交快照不变。
              </p>
              {selected.decisions.map((item) => (
                <p className="field-help" key={item.id}>
                  复核记录：{item.reason} · {item.created_at}
                </p>
              ))}
              {selected.permissions.review && (
                <>
                  <label>
                    <span className="field-label">独立复核依据</span>
                    <textarea
                      aria-label="独立复核依据"
                      value={reviewReason}
                      maxLength={1000}
                      rows={3}
                      disabled={busy}
                      onChange={(e) => setReviewReason(e.target.value)}
                    />
                  </label>
                  <div className="revision-actions">
                    <button
                      className="button primary"
                      disabled={busy || reviewReason.trim().length < 3}
                      onClick={() => review('accept')}
                    >
                      <ShieldCheck size={16} />
                      核验并接受修订
                    </button>
                    <button
                      className="button secondary"
                      disabled={busy || reviewReason.trim().length < 3}
                      onClick={() => review('reject')}
                    >
                      拒绝修订
                    </button>
                  </div>
                </>
              )}
            </section>
          )}
          <section className="panel">
            <h3>提出事实修订</h3>
            <div className="revision-event-picker">
              <label>
                <span className="field-label">已移交事项编号</span>
                <input
                  aria-label="待修订事项编号"
                  value={eventId}
                  disabled={busy}
                  onChange={(e) => setEventId(e.target.value)}
                />
              </label>
              <button
                className="button secondary"
                disabled={busy || !eventId.trim()}
                onClick={() => void openEvent(eventId)}
              >
                读取原事实
              </button>
            </div>
            {context?.pending && (
              <p className="revision-warning">该事项已有待复核修订，请先处理清单中的申请。</p>
            )}
            {context && payload && (
              <>
                <p className="field-help">
                  当前草稿：{context.draft_status} · 事实版本 {context.fact_version}
                  。原始事实保留在修订记录内。
                </p>
                <div className="revision-form">
                  <label>
                    <span className="field-label">目标会计期间</span>
                    <select
                      aria-label="修订目标账套"
                      value={targetBook}
                      disabled={busy || !context.permissions.submit}
                      onChange={(e) => setTargetBook(e.target.value)}
                    >
                      {context.books.map((book) => (
                        <option value={book.id} key={book.id}>
                          {book.period} · {book.name}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label>
                    <span className="field-label">发生日期</span>
                    <input
                      aria-label="修订发生日期"
                      type="date"
                      value={payload.occurred_on}
                      disabled={busy || !context.permissions.submit}
                      onChange={(e) => setPayload({ ...payload, occurred_on: e.target.value })}
                    />
                  </label>
                  <label>
                    <span className="field-label">金额（人民币）</span>
                    <input
                      aria-label="修订金额"
                      inputMode="decimal"
                      value={payload.amount}
                      disabled={busy || !context.permissions.submit}
                      onChange={(e) => setPayload({ ...payload, amount: e.target.value })}
                    />
                  </label>
                  <label>
                    <span className="field-label">交易对方</span>
                    <input
                      aria-label="修订交易对方"
                      value={payload.counterparty}
                      maxLength={200}
                      disabled={busy || !context.permissions.submit}
                      onChange={(e) => setPayload({ ...payload, counterparty: e.target.value })}
                    />
                  </label>
                  <label className="revision-wide">
                    <span className="field-label">业务说明</span>
                    <input
                      aria-label="修订业务说明"
                      value={payload.description}
                      maxLength={500}
                      disabled={busy || !context.permissions.submit}
                      onChange={(e) => setPayload({ ...payload, description: e.target.value })}
                    />
                  </label>
                  <label>
                    <span className="field-label">费用类别</span>
                    <select
                      aria-label="修订费用类别"
                      value={payload.category}
                      disabled={busy || !context.permissions.submit}
                      onChange={(e) =>
                        setPayload({
                          ...payload,
                          category: e.target.value as BusinessPayload['category'],
                        })
                      }
                    >
                      <option value="office">办公</option>
                      <option value="travel">差旅</option>
                      <option value="rent">租金</option>
                      <option value="communication">通信</option>
                      <option value="other">其他</option>
                    </select>
                  </label>
                  <label>
                    <span className="field-label">项目</span>
                    <input
                      aria-label="修订项目"
                      value={payload.project ?? ''}
                      maxLength={200}
                      disabled={busy || !context.permissions.submit}
                      onChange={(e) => setPayload({ ...payload, project: e.target.value })}
                    />
                  </label>
                  <label className="revision-wide">
                    <span className="field-label">修订依据</span>
                    <textarea
                      aria-label="事实修订依据"
                      value={reason}
                      maxLength={1000}
                      rows={3}
                      disabled={busy || !context.permissions.submit}
                      onChange={(e) => setReason(e.target.value)}
                      placeholder="说明原事实为什么有误，以及核对了哪些原始凭据"
                    />
                  </label>
                </div>
                <div className="revision-actions">
                  <span className="field-help">本次修改 {changes.length} 个字段</span>
                  <button
                    className="button primary"
                    disabled={
                      busy ||
                      !context.permissions.submit ||
                      context.pending ||
                      !changes.length ||
                      reason.trim().length < 3
                    }
                    onClick={submit}
                  >
                    {busy ? (
                      <LoaderCircle className="spinner" size={16} />
                    ) : (
                      <FileClock size={16} />
                    )}
                    提交独立复核
                  </button>
                </div>
              </>
            )}
          </section>
        </div>
      </div>
    </div>
  );
}
