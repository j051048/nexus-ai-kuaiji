import { useCallback, useEffect, useRef, useState } from 'react';
import {
  ArrowRight,
  CheckCircle2,
  ClipboardCheck,
  LoaderCircle,
  RefreshCw,
  ShieldCheck,
  TriangleAlert,
  X,
} from 'lucide-react';
import { useReviewFilters } from './useReviewFilters';
import { api, ApiError, errorMessage } from './api';
import { reviewGroups, reviewSelection, reviewTemplateLabels } from './review-helpers';
import type { ReviewList, ReviewPreview, ReviewReceipt, ReviewSelection } from './review-types';
import { ReviewPackageWorkbench } from './ReviewPackageWorkbench';
import { InlineReviewDrawer, type InlineReviewTarget } from './InlineReviewDrawer';
import { clearReviewSession, readReviewWorkspace, saveReviewWorkspace } from './review-session';
import type { Draft, Session } from './types';
import './review.css';

export function ReviewWorkbench({
  session,
  onBusyChange,
  onOpenDraft,
  onChanged,
  onOpenInline,
}: {
  onOpenInline?: (
    target: InlineReviewTarget,
    queue: InlineReviewTarget[],
    onChanged: (bookId: string, draft: Draft) => void,
    onClose: () => void,
  ) => void;
  session: Session;
  onBusyChange?: (value: boolean) => void;
  onOpenDraft: (bookId: string, draftId: string) => void;
  onChanged?: () => void;
}) {
  const remembered = useRef(readReviewWorkspace(session.scope_key));
  const [list, setList] = useState<ReviewList | null>(null);
  const {
    company,
    setCompany,
    period,
    setPeriod,
    template,
    setTemplate,
    risk,
    setRisk,
    offset,
    setOffset,
  } = useReviewFilters(remembered.current);
  const [selected, setSelected] = useState(new Set<string>(remembered.current?.selected ?? []));
  const [preview, setPreview] = useState<ReviewPreview | null>(null);
  const [packageItems, setPackageItems] = useState<ReviewSelection[] | null>(
    remembered.current?.packageItems ?? null,
  );
  const [receipt, setReceipt] = useState<ReviewReceipt | null>(null);
  const [acknowledged, setAcknowledged] = useState(false);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const epoch = useRef(0);
  const commandKey = useRef(remembered.current?.commandKey ?? '');
  const locked = useRef(false);
  const active = useRef(true);
  const [inlineTarget, setInlineTarget] = useState<InlineReviewTarget | null>(null);
  const [inlineBusy, setInlineBusy] = useState(false),
    [restoring, setRestoring] = useState(!!remembered.current?.previewId);
  const lastScope = useRef('');
  const authorized = useRef(false),
    inlineQueue = useRef<InlineReviewTarget[]>([]);
  const scope = `${session.scope_key}:${company}:${period}:${template}:${risk}:${offset}`;
  const scopeRef = useRef(scope);
  scopeRef.current = scope;
  useEffect(() => {
    active.current = true;
    return () => {
      active.current = false;
      epoch.current += 1;
    };
  }, [session.scope_key]);
  useEffect(() => {
    onBusyChange?.(!!busy || inlineBusy);
    return () => onBusyChange?.(false);
  }, [busy, inlineBusy, onBusyChange]);
  const clear = useCallback(() => {
    setSelected(new Set());
    setPreview(null);
    setPackageItems(null);
    setReceipt(null);
    setAcknowledged(false);
    setError('');
    commandKey.current = '';
  }, []);
  const load = useCallback(
    async (signal?: AbortSignal) => {
      const started = ++epoch.current;
      setLoading(true);
      const query = new URLSearchParams({ limit: '20', offset: String(offset), template, risk });
      if (company) query.set('company_id', company);
      if (period) query.set('period', period);
      try {
        const result = await api<ReviewList>(`/review/drafts?${query}`, session, { signal });
        if (!active.current || started !== epoch.current || signal?.aborted) return;
        authorized.current = true;
        setList(result);
        setSelected(
          (old) =>
            new Set(result.items.filter((row) => old.has(row.draft_id)).map((row) => row.draft_id)),
        );
        setError('');
      } catch (failure) {
        if (active.current && started === epoch.current && !signal?.aborted) {
          if (failure instanceof ApiError && [401, 403, 404].includes(failure.status)) {
            authorized.current = false;
            clearReviewSession();
            setList(null);
            clear();
            setInlineTarget(null);
          }
          setError(errorMessage(failure));
        }
      } finally {
        if (active.current && started === epoch.current && !signal?.aborted) setLoading(false);
      }
    },
    [session, company, period, template, risk, offset, clear],
  );
  useEffect(() => {
    const controller = new AbortController();
    if (lastScope.current && lastScope.current !== scope) {
      clear();
      setInlineTarget(null);
    }
    lastScope.current = scope;
    setList(null);
    locked.current = false;
    setBusy('');
    void load(controller.signal);
    return () => {
      controller.abort();
      epoch.current += 1;
    };
  }, [scope, load, clear]);
  useEffect(() => {
    const id = remembered.current?.previewId;
    if (!id) return;
    const controller = new AbortController();
    void api<ReviewPreview>(`/review/previews/${encodeURIComponent(id)}`, session, {
      signal: controller.signal,
    })
      .then((value) => {
        if (!controller.signal.aborted) {
          setPreview(value);
          setAcknowledged(false);
        }
      })
      .catch((failure) => {
        if (!controller.signal.aborted) {
          setError(`原复核预览已失效，请重新准备：${errorMessage(failure)}`);
          commandKey.current = '';
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) {
          remembered.current = null;
          setRestoring(false);
        }
      });
    return () => controller.abort();
  }, [session]);
  useEffect(() => {
    if (loading || restoring || !authorized.current) return;
    saveReviewWorkspace(session.scope_key, {
      company,
      period,
      template,
      risk,
      offset,
      selected: [...selected],
      previewId: preview?.id ?? null,
      commandKey: commandKey.current,
      packageItems,
    });
  }, [
    session.scope_key,
    company,
    period,
    template,
    risk,
    offset,
    selected,
    preview,
    packageItems,
    loading,
    restoring,
  ]);
  useEffect(() => {
    const revoke = () => {
      authorized.current = false;
      clearReviewSession();
      epoch.current++;
      locked.current = false;
      setBusy('');
      setInlineBusy(false);
      setList(null);
      clear();
      setInlineTarget(null);
      setLoading(false);
      setError('授权已更新，请重新读取当前待办。');
    };
    window.addEventListener('accounting:forbidden', revoke);
    window.addEventListener('accounting:unauthorized', revoke);
    return () => {
      window.removeEventListener('accounting:forbidden', revoke);
      window.removeEventListener('accounting:unauthorized', revoke);
    };
  }, [clear]);
  const groups = reviewGroups(list?.items ?? []);
  function select(id: string, checked: boolean) {
    if (busy || preview) return;
    setSelected((value) => {
      const next = new Set(value);
      if (checked) next.add(id);
      else next.delete(id);
      return next;
    });
  }
  async function makePreview() {
    if (!list || !selected.size || locked.current) return;
    locked.current = true;
    setBusy('preview');
    setError('');
    const started = scope,
      generation = epoch.current;
    try {
      const value = await api<ReviewPreview>('/review/previews', session, {
        method: 'POST',
        body: { items: reviewSelection(list.items, selected) },
      });
      if (!active.current || scopeRef.current !== started || generation !== epoch.current) return;
      setPreview(value);
      setReceipt(null);
      setAcknowledged(false);
      commandKey.current = crypto.randomUUID();
    } catch (failure) {
      if (active.current && scopeRef.current === started && generation === epoch.current)
        setError(errorMessage(failure));
    } finally {
      if (active.current && scopeRef.current === started && generation === epoch.current) {
        locked.current = false;
        setBusy('');
      }
    }
  }
  async function confirm() {
    if (!preview || !acknowledged || receipt || locked.current || !commandKey.current) return;
    locked.current = true;
    setBusy('confirm');
    setError('');
    const started = scope,
      generation = epoch.current;
    try {
      const value = await api<ReviewReceipt>(
        `/review/previews/${encodeURIComponent(preview.id)}/confirm`,
        session,
        { method: 'POST', body: { idempotency_key: commandKey.current } },
      );
      if (!active.current || scopeRef.current !== started || generation !== epoch.current) return;
      setReceipt(value);
      onChanged?.();
    } catch (failure) {
      if (active.current && scopeRef.current === started && generation === epoch.current)
        setError(`${errorMessage(failure)} 再次提交会读取同一批次的回执，请保留当前预览。`);
    } finally {
      if (active.current && scopeRef.current === started && generation === epoch.current) {
        locked.current = false;
        setBusy('');
      }
    }
  }
  function draftChanged(bookId: string, draft: Draft) {
    setPreview(null);
    setReceipt(null);
    setAcknowledged(false);
    commandKey.current = '';
    setList((old) =>
      old
        ? {
            ...old,
            items: old.items
              .map((row) =>
                row.book_id === bookId && row.draft_id === draft.id
                  ? { ...row, expected_version: draft.version, description: draft.description }
                  : row,
              )
              .filter(
                (row) =>
                  !(
                    row.book_id === bookId &&
                    row.draft_id === draft.id &&
                    draft.status !== 'draft'
                  ),
              ),
          }
        : old,
    );
    setSelected((old) =>
      draft.status === 'draft' ? new Set(old) : new Set([...old].filter((id) => id !== draft.id)),
    );
    setError('凭证已更新；已保留筛选和其他选择，请按当前版本重新准备批量预览。');
    onChanged?.();
  }
  const frozen = !!busy || !!preview || inlineBusy || restoring;
  function openInline(target: InlineReviewTarget) {
    inlineQueue.current = [...(preview?.items ?? list?.items ?? [])];
    if (onOpenInline) {
      setInlineBusy(true);
      onOpenInline(target, inlineQueue.current, draftChanged, () => setInlineBusy(false));
    } else setInlineTarget(target);
  }
  if (packageItems)
    return (
      <ReviewPackageWorkbench
        key={scope}
        session={session}
        items={packageItems}
        onBack={() => {
          setPackageItems(null);
          void load();
        }}
        onOpenDraft={onOpenDraft}
        onBusyChange={onBusyChange}
        onChanged={onChanged}
        onOpenInline={onOpenInline}
      />
    );
  return (
    <main className="review-workbench">
      <section className="page-heading">
        <div>
          <p className="eyebrow">ONE DESK, MANY COMPANIES</p>
          <h1>集中复核，把时间留给判断。</h1>
          <p className="page-description">
            按企业、期间和业务类型查看待办。先核对预览，再明确确认；每个账套分别保存审核回执。
          </p>
        </div>
        <button
          type="button"
          className="button secondary"
          disabled={loading || frozen}
          onClick={() => {
            clear();
            void load();
          }}
        >
          <RefreshCw size={16} />
          刷新待复核
        </button>
      </section>
      <section className="queue-intro">
        <ClipboardCheck size={24} />
        <div>
          <strong>跨企业批量复核</strong>
          <p>预览完成金额、证据、科目和异常检查。审核通过后仍需会计执行导出。</p>
        </div>
        <span>
          <ShieldCheck size={15} />
          仅包含有复核权限的账套
        </span>
      </section>
      {error && (
        <p className="inline-error" role="alert">
          <TriangleAlert size={15} />
          {error}
        </p>
      )}
      <div className="batch-review-grid">
        <section className="panel review-pending-panel">
          <div className="panel-heading">
            <div>
              <h2>
                待复核事项<span className="count-label">{list?.pagination.total ?? 0}</span>
              </h2>
              <p>基础检查分组用于定位待办，正式审核会重新核验全部事实</p>
            </div>
          </div>
          <div className="batch-review-filters">
            <label>
              <span>客户企业</span>
              <select
                aria-label="复核企业"
                value={company}
                disabled={frozen}
                onChange={(event) => {
                  setCompany(event.target.value);
                  setOffset(0);
                }}
              >
                <option value="">全部获授权企业</option>
                {list?.companies.map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.name}
                  </option>
                ))}
              </select>
            </label>
            <label>
              <span>会计期间</span>
              <input
                aria-label="复核期间"
                type="month"
                min="2000-01"
                max="2099-12"
                value={period}
                disabled={frozen}
                onChange={(event) => {
                  setPeriod(event.target.value);
                  setOffset(0);
                }}
              />
            </label>
            <label>
              <span>业务类型</span>
              <select
                aria-label="复核业务类型"
                value={template}
                disabled={frozen}
                onChange={(event) => {
                  setTemplate(event.target.value);
                  setOffset(0);
                }}
              >
                <option value="all">全部业务类型</option>
                {Object.entries(reviewTemplateLabels).map(([value, name]) => (
                  <option key={value} value={value}>
                    {name}
                  </option>
                ))}
              </select>
            </label>
            <label>
              <span>检查分组</span>
              <select
                aria-label="复核风险分组"
                value={risk}
                disabled={frozen}
                onChange={(event) => {
                  setRisk(event.target.value);
                  setOffset(0);
                }}
              >
                <option value="all">全部待复核</option>
                <option value="basic_clear">基础检查正常</option>
                <option value="basic_exception">需处理基础异常</option>
              </select>
            </label>
          </div>
          <div className="batch-selection-bar">
            <label>
              <input
                type="checkbox"
                aria-label="选择本页凭证"
                disabled={loading || frozen || !list?.items.length}
                checked={
                  !!list?.items.length && list.items.every((item) => selected.has(item.draft_id))
                }
                onChange={(event) =>
                  setSelected(
                    new Set(event.target.checked ? list?.items.map((item) => item.draft_id) : []),
                  )
                }
              />
              选择本页
            </label>
            <span>已选 {selected.size} 张</span>
            <button
              type="button"
              className="button primary small"
              disabled={loading || frozen || !selected.size}
              onClick={() => void makePreview()}
            >
              {busy === 'preview' ? (
                <LoaderCircle size={15} className="spinner" />
              ) : (
                <ClipboardCheck size={15} />
              )}
              预览所选凭证
            </button>
            <button
              type="button"
              className="button secondary small"
              disabled={loading || frozen || !selected.size}
              onClick={() => {
                if (list) setPackageItems(reviewSelection(list.items, selected));
              }}
            >
              <ClipboardCheck size={15} />
              准备同类复核包
            </button>
          </div>
          {loading ? (
            <div className="loading-block">
              <LoaderCircle size={16} className="spinner" />
              正在读取待复核事项…
            </div>
          ) : groups.length ? (
            groups.map((group) => (
              <section key={group.key} className="batch-review-group">
                <h3>{group.label}</h3>
                {group.items.map((item) => (
                  <article key={item.draft_id} className="batch-review-row">
                    <input
                      type="checkbox"
                      aria-label={`选择凭证 ${item.description}`}
                      checked={selected.has(item.draft_id)}
                      disabled={frozen}
                      onChange={(event) => select(item.draft_id, event.target.checked)}
                    />
                    <div>
                      <strong>{item.description}</strong>
                      <p>
                        {item.book_name} · {item.source_date} · 版本 {item.expected_version}
                      </p>
                    </div>
                    <span className="batch-review-amount">¥{item.source_amount}</span>
                    <button
                      type="button"
                      className="text-button"
                      disabled={!!busy}
                      onClick={() => openInline(item)}
                    >
                      查看证据
                      <ArrowRight size={13} />
                    </button>
                  </article>
                ))}
              </section>
            ))
          ) : (
            <div className="empty-state">
              <div className="empty-icon">
                <ClipboardCheck size={27} />
              </div>
              <strong>当前没有可复核事项</strong>
              <p>待办会按当前账套复核权限筛选。后台生成草稿后可在这里集中处理。</p>
            </div>
          )}
          <div className="business-pagination">
            <span>
              {list?.pagination.total ?? 0} 张待办 · 第 {Math.floor(offset / 20) + 1} 页
            </span>
            <div>
              <button
                type="button"
                className="text-button"
                disabled={loading || frozen || offset === 0}
                onClick={() => setOffset((value) => Math.max(0, value - 20))}
              >
                上一页
              </button>
              <button
                type="button"
                className="text-button"
                disabled={loading || frozen || !list?.pagination.has_more}
                onClick={() => setOffset((value) => value + 20)}
              >
                下一页
              </button>
            </div>
          </div>
        </section>
        <aside className="panel batch-review-preview" aria-label="批量复核预览">
          <div className="review-panel-header">
            <div className="review-heading">
              <span className="review-icon">
                <ClipboardCheck size={18} />
              </span>
              <div>
                <h2>逐张核对，再集中确认</h2>
                <p>预览保存当前版本与证据指纹</p>
              </div>
            </div>
            {preview && (
              <button
                type="button"
                className="icon-button"
                aria-label="关闭复核预览"
                disabled={!!busy}
                onClick={() => {
                  clear();
                  void load();
                }}
              >
                <X size={16} />
              </button>
            )}
          </div>
          {!preview ? (
            <div className="empty-state">
              <div className="empty-icon">
                <ShieldCheck size={27} />
              </div>
              <strong>选择要复核的凭证</strong>
              <p>金额、分录、证据与阻断异常将在预览中逐张展示。</p>
            </div>
          ) : (
            <div className="batch-preview-content">
              <div className="batch-preview-summary">
                <strong>{preview.items.length} 张凭证</strong>
                <span>
                  可确认 {preview.ready_count} · 阻断 {preview.blocked_count}
                </span>
                <p>同一账套任何一张未通过，该账套整批不提交；其他账套分别返回结果。</p>
              </div>
              {preview.items.map((item) => (
                <article
                  className={`batch-preview-draft ${item.ready ? '' : 'has-issues'}`}
                  key={item.draft_id}
                >
                  <div>
                    <span className={`badge ${item.ready ? 'approved' : 'draft'}`}>
                      {item.ready ? '预览时检查通过' : '需人工处理'}
                    </span>
                    <strong>
                      {item.company_name} · {item.period}
                    </strong>
                  </div>
                  <h3>{item.description}</h3>
                  <p>
                    {item.book_name} · ¥{item.source_amount} · 草稿版本 {item.expected_version}
                    {item.fact_version !== null && ` · 事实版本 ${item.fact_version}`}
                  </p>
                  <table>
                    <thead>
                      <tr>
                        <th>科目</th>
                        <th>借方</th>
                        <th>贷方</th>
                      </tr>
                    </thead>
                    <tbody>
                      {item.lines.map((line, i) => (
                        <tr key={i}>
                          <td>
                            {line.account_code}
                            {Object.entries(line.auxiliary_values ?? {}).map(([key, value]) => (
                              <small key={key}>{value}</small>
                            ))}
                          </td>
                          <td>{line.debit}</td>
                          <td>{line.credit}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  <p className="batch-evidence-ref">证据：{item.evidence_ref || '尚未补充'}</p>
                  {item.issues.length > 0 && (
                    <ul className="batch-preview-issues">
                      {item.issues.map((issue) => (
                        <li key={issue}>{issue}</li>
                      ))}
                    </ul>
                  )}
                  <button
                    type="button"
                    className="text-button"
                    disabled={!!busy}
                    onClick={() => openInline(item)}
                  >
                    打开原件与凭证
                    <ArrowRight size={13} />
                  </button>
                </article>
              ))}
              {receipt ? (
                <section className="batch-review-receipt" aria-label="批量复核回执" role="status">
                  <h3>
                    {receipt.status === 'completed'
                      ? '所选账套已完成复核'
                      : receipt.status === 'partial'
                        ? '部分账套完成复核'
                        : '本批次未通过复核'}
                  </h3>
                  <p>
                    通过 {receipt.approved_count} 张 · 未通过 {receipt.failed_book_count} 个账套
                  </p>
                  {receipt.results.map((item) => (
                    <div key={item.book_id}>
                      <strong>
                        {item.status === 'succeeded' ? (
                          <CheckCircle2 size={15} />
                        ) : (
                          <TriangleAlert size={15} />
                        )}
                        {item.company_name} · {item.book_name}
                      </strong>
                      <p>
                        {item.status === 'succeeded'
                          ? `${item.approved_ids.length} 张已审核，等待会计导出`
                          : item.error}
                      </p>
                    </div>
                  ))}
                  <p className="field-help">
                    处理失败账套的原因后，关闭本预览并重新选择。重试本命令保持原回执。
                  </p>
                </section>
              ) : (
                <section className="batch-review-confirm">
                  <label>
                    <input
                      type="checkbox"
                      aria-label="确认已核对批量凭证证据与分录"
                      checked={acknowledged}
                      disabled={!!busy}
                      onChange={(event) => setAcknowledged(event.target.checked)}
                    />
                    我已核对所选凭证的证据、金额和会计处理
                  </label>
                  <p>
                    预览有效至{' '}
                    {new Date(preview.expires_at).toLocaleTimeString('zh-CN', {
                      timeZone: 'Asia/Shanghai',
                      hour12: false,
                    })}
                    。提交时再次检查权限、期间及当前事实。
                  </p>
                  <button
                    type="button"
                    className="button primary"
                    disabled={!!busy || !acknowledged || preview.ready_count === 0}
                    onClick={() => void confirm()}
                  >
                    {busy === 'confirm' ? (
                      <LoaderCircle className="spinner" size={16} />
                    ) : (
                      <ShieldCheck size={16} />
                    )}
                    确认批量复核
                  </button>
                </section>
              )}
            </div>
          )}
        </aside>
      </div>
      {inlineTarget && (
        <InlineReviewDrawer
          key={`${session.scope_key}:${inlineTarget.book_id}:${inlineTarget.draft_id}`}
          session={session}
          bookId={inlineTarget.book_id}
          draftId={inlineTarget.draft_id}
          queue={inlineQueue.current}
          onClose={() => setInlineTarget(null)}
          onNavigate={(book_id, draft_id) => setInlineTarget({ book_id, draft_id })}
          onChanged={draftChanged}
          onBusyChange={setInlineBusy}
        />
      )}
    </main>
  );
}
