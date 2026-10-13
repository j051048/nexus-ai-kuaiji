import { useCallback, useEffect, useRef, useState } from 'react';
import {
  ArrowLeft,
  ArrowRight,
  CheckCircle2,
  ClipboardCheck,
  Layers3,
  LoaderCircle,
  RefreshCw,
  ShieldCheck,
  TriangleAlert,
} from 'lucide-react';
import { api, ApiError, errorMessage } from './api';
import { basisLabel, eligiblePacketIds, packetSelection } from './review-package-helpers';
import type { ReviewPacketSet } from './review-package-types';
import type { ReviewReceipt, ReviewSelection } from './review-types';
import { InlineReviewDrawer, type InlineReviewTarget } from './InlineReviewDrawer';
import {
  clearReviewPackage,
  clearReviewSession,
  readReviewPackage,
  saveReviewPackage,
} from './review-session';
import type { Draft, Session } from './types';
import './review-packages.css';

export function ReviewPackageWorkbench({
  session,
  items,
  onBack,
  onOpenDraft,
  onBusyChange,
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
  items: ReviewSelection[];
  onBack: () => void;
  onOpenDraft: (bookId: string, draftId: string) => void;
  onBusyChange?: (value: boolean) => void;
  onChanged?: () => void;
}) {
  const signature = JSON.stringify(items);
  const remembered = useRef(readReviewPackage(session.scope_key, signature));
  const [packets, setPackets] = useState<ReviewPacketSet | null>(null);
  const [selected, setSelected] = useState(new Set<string>(remembered.current?.selected ?? []));
  const [acknowledged, setAcknowledged] = useState(false);
  const [receipt, setReceipt] = useState<ReviewReceipt | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const prepareKey = useRef(remembered.current?.prepareKey ?? crypto.randomUUID());
  const selectionKey = useRef(remembered.current?.selectionKey ?? { signature: '', key: '' });
  const confirmationKey = useRef(remembered.current?.confirmationKey ?? crypto.randomUUID());
  const active = useRef(true),
    epoch = useRef(0),
    locked = useRef(false);
  const authorized = useRef(false),
    prepareItems = useRef(items);
  const [changed, setChanged] = useState(remembered.current?.changed ?? false);
  const [inlineTarget, setInlineTarget] = useState<InlineReviewTarget | null>(null),
    [inlineBusy, setInlineBusy] = useState(false);
  const inlineQueue = useRef<InlineReviewTarget[]>([]);
  const scope = useRef(session.scope_key);
  scope.current = session.scope_key;
  useEffect(() => {
    active.current = true;
    locked.current = false;
    return () => {
      active.current = false;
      epoch.current++;
    };
  }, [session.scope_key]);
  useEffect(() => {
    onBusyChange?.(busy || inlineBusy);
    return () => onBusyChange?.(false);
  }, [busy, inlineBusy, onBusyChange]);
  const run = useCallback(
    async (action: (valid: () => boolean) => Promise<void>) => {
      if (locked.current) return;
      locked.current = true;
      setBusy(true);
      setError('');
      const target = session.scope_key,
        command = ++epoch.current;
      const valid = () => active.current && target === scope.current && epoch.current === command;
      try {
        await action(valid);
      } catch (failure) {
        if (active.current && target === scope.current && epoch.current === command) {
          setError(errorMessage(failure));
          if (failure instanceof ApiError && [401, 403, 404].includes(failure.status)) {
            authorized.current = false;
            clearReviewSession();
            setPackets(null);
            setSelected(new Set());
            setInlineTarget(null);
          }
        }
      } finally {
        if (active.current && target === scope.current && epoch.current === command) {
          locked.current = false;
          setBusy(false);
        }
      }
    },
    [session.scope_key],
  );
  const prepare = useCallback(async () => {
    await run(async (valid) => {
      const value = await api<ReviewPacketSet>('/review/packages', session, {
        method: 'POST',
        body: { items: prepareItems.current, idempotency_key: prepareKey.current },
      });
      if (!valid()) return;
      authorized.current = true;
      setPackets(value);
      setSelected((old) => new Set(packetSelection(value, old)));
      setAcknowledged(false);
      setReceipt(null);
      setChanged(false);
    });
  }, [session, items, run]);
  useEffect(() => {
    const savedId = remembered.current?.packageId;
    if (savedId) {
      void run(async (valid) => {
        const value = await api<ReviewPacketSet>(
          `/review/packages/${encodeURIComponent(savedId)}`,
          session,
        );
        if (!valid()) return;
        authorized.current = true;
        setPackets(value);
        setSelected((old) => new Set(packetSelection(value, old)));
        setAcknowledged(false);
      });
    } else void prepare();
  }, [prepare, run, session]);
  useEffect(() => {
    if (!authorized.current || busy) return;
    saveReviewPackage(session.scope_key, {
      signature,
      packageId: packets?.id ?? null,
      selected: [...selected],
      prepareKey: prepareKey.current,
      confirmationKey: confirmationKey.current,
      selectionKey: selectionKey.current,
      changed,
    });
  }, [session.scope_key, signature, packets, selected, busy, changed]);
  useEffect(() => {
    const revoke = () => {
      authorized.current = false;
      clearReviewSession();
      epoch.current++;
      setPackets(null);
      setSelected(new Set());
      setInlineTarget(null);
      setBusy(false);
      locked.current = false;
      setError('授权已更新，请返回待办重新读取。');
    };
    window.addEventListener('accounting:forbidden', revoke);
    window.addEventListener('accounting:unauthorized', revoke);
    return () => {
      window.removeEventListener('accounting:forbidden', revoke);
      window.removeEventListener('accounting:unauthorized', revoke);
    };
  }, []);
  async function refresh() {
    if (!packets) {
      await prepare();
      return;
    }
    const id = packets.id;
    await run(async (valid) => {
      const value = await api<ReviewPacketSet>(
        `/review/packages/${encodeURIComponent(id)}`,
        session,
      );
      if (!valid()) return;
      authorized.current = true;
      setPackets(value);
      setSelected((old) => new Set(packetSelection(value, old)));
      setAcknowledged(false);
    });
  }
  async function selectPackets() {
    if (!packets) return;
    const ids = packetSelection(packets, selected);
    if (!ids.length) return;
    const signature = JSON.stringify([packets.id, ids]);
    if (selectionKey.current.signature !== signature)
      selectionKey.current = { signature, key: crypto.randomUUID() };
    await run(async (valid) => {
      const value = await api<ReviewPacketSet>(
        `/review/packages/${encodeURIComponent(packets.id)}/selection`,
        session,
        { method: 'POST', body: { draft_ids: ids, idempotency_key: selectionKey.current.key } },
      );
      if (!valid()) return;
      setPackets(value);
      setSelected(new Set(ids));
      setAcknowledged(false);
      setReceipt(null);
      confirmationKey.current = crypto.randomUUID();
    });
  }
  async function confirm() {
    if (!packets?.parent_preview_id || !acknowledged || receipt || changed) return;
    const id = packets.id;
    await run(async (valid) => {
      const result = await api<ReviewReceipt>(
        `/review/packages/${encodeURIComponent(id)}/confirm`,
        session,
        { method: 'POST', body: { idempotency_key: confirmationKey.current } },
      );
      if (!valid()) return;
      setReceipt(result);
      onChanged?.();
    });
  }
  const isPreview = !!packets?.parent_preview_id;
  async function reprepare() {
    if (locked.current || !packets) return;
    await run(async (valid) => {
      const versions = await Promise.all(
        items.map(async (item) => {
          const draft = await api<Draft>(
            `/books/${encodeURIComponent(item.book_id)}/drafts/${encodeURIComponent(item.draft_id)}`,
            session,
          );
          return draft.status === 'draft' && !draft.source_inactive
            ? { ...item, expected_version: draft.version }
            : null;
        }),
      );
      if (!valid()) return;
      const current = versions.filter((row): row is ReviewSelection => row !== null);
      if (!current.length) {
        setError('所选凭证均已审核或失效，请返回待办重新选择。');
        return;
      }
      prepareItems.current = current;
      prepareKey.current = crypto.randomUUID();
      selectionKey.current = { signature: '', key: '' };
      confirmationKey.current = crypto.randomUUID();
      const value = await api<ReviewPacketSet>('/review/packages', session, {
        method: 'POST',
        body: { items: current, idempotency_key: prepareKey.current },
      });
      if (!valid()) return;
      authorized.current = true;
      setPackets(value);
      setSelected((old) => new Set(packetSelection(value, old)));
      setAcknowledged(false);
      setReceipt(null);
      setChanged(false);
    });
  }
  function draftChanged(bookId: string, draft: Draft) {
    setChanged(true);
    setAcknowledged(false);
    setReceipt(null);
    onChanged?.();
    prepareItems.current = prepareItems.current.map((row) =>
      row.book_id === bookId && row.draft_id === draft.id
        ? { ...row, expected_version: draft.version }
        : row,
    );
    setSelected((old) =>
      draft.status === 'draft' ? new Set(old) : new Set([...old].filter((id) => id !== draft.id)),
    );
  }
  function openInline(target: InlineReviewTarget) {
    inlineQueue.current = packets?.items ?? [];
    if (onOpenInline) {
      setInlineBusy(true);
      onOpenInline(target, inlineQueue.current, draftChanged, () => setInlineBusy(false));
    } else setInlineTarget(target);
  }
  function toggle(ids: string[], checked: boolean) {
    if (!packets || busy || isPreview) return;
    const eligible = eligiblePacketIds(packets);
    setSelected((old) => {
      const next = new Set(old);
      for (const id of ids)
        if (eligible.has(id)) {
          if (checked) next.add(id);
          else next.delete(id);
        }
      return next;
    });
  }
  return (
    <main className="review-package-workbench">
      <section className="page-heading">
        <div>
          <p className="eyebrow">COMPARE THE EXCEPTIONS</p>
          <h1>同类业务，一起核对。</h1>
          <p className="page-description">
            按企业、往来身份、处理依据和分录结构整理。金额、日期、税额和原件差异逐笔保留。
          </p>
        </div>
        <div className="review-package-actions">
          <button
            className="button secondary"
            disabled={busy || inlineBusy}
            onClick={() => {
              clearReviewPackage(session.scope_key);
              onBack();
            }}
          >
            <ArrowLeft size={15} />
            返回待复核
          </button>
          <button
            className="button secondary"
            disabled={busy || !!receipt || inlineBusy}
            onClick={() => void refresh()}
          >
            <RefreshCw size={15} />
            重新核验复核包
          </button>
        </div>
      </section>
      {error && (
        <p className="inline-error" role="alert">
          <TriangleAlert size={16} />
          {error}
        </p>
      )}
      {changed && (
        <section className="review-package-stale" role="status">
          <TriangleAlert size={18} />
          <div>
            <strong>凭证已更新，原复核预览已暂停</strong>
            <p>已保留当前筛选和其他选择。按当前版本重新准备后，再核对变化并确认。</p>
          </div>
          <button
            className="button secondary"
            disabled={busy || inlineBusy}
            onClick={() => void reprepare()}
          >
            <RefreshCw size={15} />
            按当前版本重新准备
          </button>
        </section>
      )}
      {!packets ? (
        <section className="panel empty-state">
          {busy ? (
            <>
              <LoaderCircle className="spinner" size={24} />
              <strong>正在准备当前证据和完整检查</strong>
            </>
          ) : (
            <>
              <ClipboardCheck size={24} />
              <strong>复核包尚未准备完成</strong>
              <button className="button secondary" onClick={() => void prepare()}>
                重试读取原请求
              </button>
            </>
          )}
        </section>
      ) : (
        <>
          <section className="review-package-summary" aria-label="复核包检查汇总">
            <div>
              <Layers3 size={22} />
              <strong>{packets.packages.length} 个复核包</strong>
              <span>
                {packets.items.length} 张凭证 · 当前检查通过 {packets.ready_count}
              </span>
            </div>
            <p>
              阻断 {packets.blocked_count} · 需要重新准备 {packets.stale_count}
              。每组限定同一企业和期间；往来身份未确认时逐张展示。
            </p>
            <p>当前检查结果用于辅助真人复核，最终提交仍逐笔核验权限、版本、证据和金额。</p>
          </section>
          {!isPreview && (
            <div className="review-package-selection">
              <label>
                <input
                  type="checkbox"
                  aria-label="选择复核包内当前检查通过的凭证"
                  checked={
                    packets.ready_count > 0 &&
                    packetSelection(packets, selected).length === packets.ready_count
                  }
                  disabled={busy || changed || !packets.ready_count}
                  onChange={(event) =>
                    setSelected(event.target.checked ? eligiblePacketIds(packets) : new Set())
                  }
                />
                选择当前检查通过的凭证
              </label>
              <span>已选 {packetSelection(packets, selected).length} 张</span>
              <button
                className="button primary"
                disabled={busy || changed || !packetSelection(packets, selected).length}
                onClick={() => void selectPackets()}
              >
                <ClipboardCheck size={16} />
                生成所选复核预览
              </button>
            </div>
          )}
          <div className="review-package-grid">
            {packets.packages.map((packet) => (
              <section className="panel review-packet" key={packet.id}>
                <div className="panel-heading">
                  <div>
                    <h2>
                      {packet.counterparty || '往来名称待核对'}
                      <span className="count-label">{packet.count}</span>
                    </h2>
                    <p>
                      {packet.company_name} · {packet.period} · {packet.book_name}
                    </p>
                  </div>
                  {!isPreview && (
                    <label className="review-packet-select">
                      <input
                        type="checkbox"
                        aria-label={`选择同类复核包 ${packet.counterparty} ${packet.id}`}
                        disabled={busy || !packet.ready_count}
                        checked={
                          packet.ready_count > 0 &&
                          packet.items
                            .filter((row) => row.check_state === 'ready')
                            .every((row) => selected.has(row.draft_id))
                        }
                        onChange={(event) =>
                          toggle(
                            packet.items.map((row) => row.draft_id),
                            event.target.checked,
                          )
                        }
                      />
                      选本组
                    </label>
                  )}
                </div>
                <div className="review-packet-basis">
                  <span className="badge">{basisLabel(packet.basis)}</span>
                  <span>
                    {packet.party_status === 'resolved' ? '往来身份已确认' : '核对原始往来名称'}
                  </span>
                  <p>
                    {packet.line_shape
                      .map((line) => `${line.side === 'debit' ? '借' : '贷'} ${line.account_code}`)
                      .join(' / ')}
                  </p>
                </div>
                <div className="review-packet-amounts">
                  <span>
                    合计 <b>¥{packet.total_amount}</b>
                  </span>
                  <span>
                    单笔 ¥{packet.min_amount}–{packet.max_amount}
                  </span>
                </div>
                {packet.items.map((item) => (
                  <article className={`review-packet-item ${item.check_state}`} key={item.draft_id}>
                    <div>
                      {!isPreview && (
                        <input
                          type="checkbox"
                          aria-label={`选择复核包凭证 ${item.description}`}
                          checked={selected.has(item.draft_id)}
                          disabled={busy || item.check_state !== 'ready'}
                          onChange={(event) => toggle([item.draft_id], event.target.checked)}
                        />
                      )}
                      <strong>{item.description}</strong>
                      <span
                        className={`badge ${item.check_state === 'ready' ? 'approved' : 'exception'}`}
                      >
                        {item.check_state === 'ready'
                          ? '当前检查通过'
                          : item.check_state === 'stale'
                            ? '需要重新准备'
                            : '需人工处理'}
                      </span>
                    </div>
                    <p>
                      {item.source_date} · ¥{item.source_amount} · 草稿 v{item.expected_version}
                      {item.fact_version !== null && ` / 事实 v${item.fact_version}`}
                    </p>
                    <p>
                      原始往来名称：{item.comparison.original_counterparty || '未填写'} · 业务税额：
                      {item.comparison.business_tax_amount ?? '未填写'}
                    </p>
                    {item.comparison.recognized.map((recognized) => (
                      <p key={recognized.run_id}>
                        票据金额：{recognized.total_amount ?? '未识别'} · 票据税额：
                        {recognized.tax_amount ?? '未识别'}
                      </p>
                    ))}
                    <p className="review-packet-reference">证据：{item.evidence_ref || '待补充'}</p>
                    {item.stale_reason && (
                      <p className="review-packet-issue">{item.stale_reason}</p>
                    )}
                    {item.issues.map((issue) => (
                      <p className="review-packet-issue" key={issue}>
                        {issue}
                      </p>
                    ))}
                    <details>
                      <summary>核对逐笔分录及辅助项</summary>
                      <table>
                        <thead>
                          <tr>
                            <th>科目 / 辅助项</th>
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
                                  <small key={key}>
                                    {key}：{value}
                                  </small>
                                ))}
                              </td>
                              <td>{line.debit}</td>
                              <td>{line.credit}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </details>
                    <button
                      className="text-button"
                      disabled={busy}
                      onClick={() => openInline(item)}
                    >
                      打开原件与凭证
                      <ArrowRight size={13} />
                    </button>
                  </article>
                ))}
              </section>
            ))}
          </div>
          {receipt ? (
            <section className="panel review-package-confirm" role="status">
              <h2>
                {receipt.status === 'completed' ? '所选复核包已完成审核' : '本次复核有未完成账套'}
              </h2>
              <p>
                已审核 {receipt.approved_count} 张 · 未完成 {receipt.failed_book_count}{' '}
                个账套。导出仍由会计另行执行。
              </p>
              {receipt.results.map((result) => (
                <p key={result.book_id}>
                  {result.status === 'succeeded' ? (
                    <CheckCircle2 size={14} />
                  ) : (
                    <TriangleAlert size={14} />
                  )}
                  {result.company_name} · {result.book_name}：
                  {result.status === 'succeeded'
                    ? `${result.approved_ids.length} 张已审核`
                    : result.error}
                </p>
              ))}
            </section>
          ) : (
            isPreview && (
              <section className="panel review-package-confirm">
                <h2>确认所选复核包</h2>
                <label>
                  <input
                    type="checkbox"
                    aria-label="确认已核对同类复核包的证据和差异"
                    checked={acknowledged}
                    disabled={busy}
                    onChange={(event) => setAcknowledged(event.target.checked)}
                  />
                  我已核对所选凭证的原件、差异和会计处理
                </label>
                <p>
                  预览有效至{' '}
                  {new Date(packets.expires_at).toLocaleTimeString('zh-CN', {
                    timeZone: 'Asia/Shanghai',
                    hour12: false,
                  })}
                  ；变化后须重新准备，重复提交读取原审核回执。
                </p>
                <button
                  className="button primary"
                  disabled={
                    busy ||
                    changed ||
                    !acknowledged ||
                    packets.stale_count > 0 ||
                    packets.blocked_count > 0 ||
                    packets.confirmed
                  }
                  onClick={() => void confirm()}
                >
                  <ShieldCheck size={16} />
                  确认所选复核包
                </button>
              </section>
            )
          )}
        </>
      )}
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
