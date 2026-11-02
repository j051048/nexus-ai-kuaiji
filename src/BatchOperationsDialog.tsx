import { useEffect, useRef, useState } from 'react';
import { api, apiBlob } from './api';
import { Modal } from './AppPrimitives';
import { downloadCsv } from './csv';
import {
  acceptBatch,
  acceptPlan,
  batchNames,
  blockedLabel,
  itemActions,
  itemLabel,
  batchResultUrl,
} from './batch-operations';
import type { BatchItem, BatchKind, BatchOperation, Plan } from './batch-operations';
import type { MonthlyClientRow } from './monthly-workbench-types';
import type { Session } from './types';
import { useOperationRequests } from './useOperationRequests';
import './batch-operations.css';

const root = '/firm/batch-operations';
const routeKind = (kind: BatchKind) => kind.replace('_', '-');
export function BatchOperationsDialog({
  session,
  period,
  rows,
  onClose,
  initialBatchId,
}: {
  session: Session;
  period: string;
  rows: MonthlyClientRow[];
  onClose: () => void;
  initialBatchId?: string;
}) {
  const [kind, setKind] = useState<'review_export' | 'package_draft'>('review_export');
  const [selected, setSelected] = useState<string[]>([]),
    [plan, setPlan] = useState<Plan | null>(null);
  const [batch, setBatch] = useState<BatchOperation | null>(null);
  const [history, setHistory] = useState<{ id: string; kind: BatchKind; period: string }[]>([]);
  const [offset, setOffset] = useState(0),
    [total, setTotal] = useState(0);
  const [action, setAction] = useState<{
    item: string;
    name: string;
    verb: 'cancel' | 'skip';
  } | null>(null);
  const [reason, setReason] = useState('');
  const [historyRevision, setHistoryRevision] = useState(0);
  const key = useRef(''),
    retryKey = useRef('');
  const { run, busy, error, denied } = useOperationRequests(session);
  const choices = [...new Map(rows.map((r) => [r.company_id, r])).values()];
  const request = (path: string, signal: AbortSignal, body?: unknown) =>
    api<unknown>(path, session, { signal, ...(body ? { method: 'POST', body } : {}) });
  async function load(id: string) {
    const ok = await run(
      async (signal) => acceptBatch(await request(`${root}/${encodeURIComponent(id)}`, signal), id),
      setBatch,
    );
    if (!ok) {
      setBatch(null);
      setAction(null);
    }
  }
  useEffect(() => {
    if (initialBatchId && historyRevision === 0 && offset === 0) {
      void load(initialBatchId);
      return;
    }
    void run(
      async (signal) => {
        const value = await api<{
          batches: { id: string; kind: BatchKind; request: { period: string } }[];
          total: number;
        }>(`${root}?limit=20&offset=${offset}`, session, { signal });
        if (
          !Array.isArray(value.batches) ||
          !Number.isSafeInteger(value.total) ||
          value.batches.some(
            (b) =>
              !b.id ||
              !Object.prototype.hasOwnProperty.call(batchNames, b.kind) ||
              !b.request?.period,
          )
        )
          throw new Error('批次列表无效');
        return value;
      },
      (value) => {
        setHistory(
          value.batches.map((b) => ({ id: b.id, kind: b.kind, period: b.request.period })),
        );
        setTotal(value.total);
      },
    );
  }, [offset, run, session, historyRevision, initialBatchId]);
  useEffect(() => {
    if (!batch || denied || busy || !['queued', 'running'].includes(batch.status)) return;
    const timer = window.setTimeout(() => {
      if (document.visibilityState === 'visible') void load(batch.id);
      else setBatch({ ...batch });
    }, 2000);
    return () => clearTimeout(timer);
    // load is intentionally read from the current render, never a stale selection.
  }, [batch, busy, denied]);
  function changeKind(value: typeof kind) {
    setKind(value);
    setPlan(null);
    setHistoryRevision((v) => v + 1);
    key.current = '';
  }
  async function preview() {
    setPlan(null);
    await run(
      async (signal) =>
        acceptPlan(
          await request(`${root}/${routeKind(kind)}/preview`, signal, {
            company_ids: selected,
            period,
          }),
          selected,
          period,
          kind,
        ),
      (value) => {
        setPlan(value);
        if (!key.current) key.current = crypto.randomUUID();
      },
    );
  }
  async function create() {
    if (!plan || !plan.ready || !key.current) return;
    const ok = await run(
      async (signal) => {
        const result = acceptBatch(
          await request(`${root}/${routeKind(kind)}`, signal, {
            company_ids: selected,
            period,
            confirmed_manifest: plan.manifest,
            idempotency_key: key.current,
          }),
        );
        if (
          result.kind !== kind ||
          result.request?.period !== period ||
          result.items.length !== selected.length ||
          result.items.some((i) => !selected.includes(i.company_id))
        )
          throw new Error('批次范围不符');
        return result;
      },
      (value) => {
        setBatch(value);
        setPlan(null);
      },
      true,
    );
    if (!ok) setPlan(null); // conflict/unknown require fresh observation and explicit confirmation
  }
  async function control() {
    if (!batch || !action || reason.trim().length < 3) return;
    const ok = await run(
      (signal) =>
        request(
          `${root}/${encodeURIComponent(batch.id)}/items/${encodeURIComponent(action.item)}/${action.verb}`,
          signal,
          { reason: reason.trim() },
        ),
      () => {
        setAction(null);
        setReason('');
      },
      true,
    );
    if (ok) await load(batch.id);
    else {
      setBatch(null);
      setAction(null);
    }
  }
  async function retry() {
    if (!batch) return;
    if (!retryKey.current) retryKey.current = crypto.randomUUID();
    await run(
      async (signal) =>
        acceptBatch(
          await request(`${root}/${encodeURIComponent(batch.id)}/retry`, signal, {
            idempotency_key: retryKey.current,
            reason: '人工确认仅重试已失败且未超限的明细',
          }),
          batch.id,
        ),
      (value) => {
        setBatch(value);
        retryKey.current = '';
      },
      true,
    );
  }
  async function failures() {
    if (!batch) return;
    await run(
      (signal) =>
        apiBlob(`${root}/${encodeURIComponent(batch.id)}/failures.csv`, session, { signal }),
      (blob) => {
        const url = URL.createObjectURL(blob),
          a = document.createElement('a');
        a.href = url;
        a.download = `batch-${batch.id}-failures.csv`;
        a.click();
        window.setTimeout(() => URL.revokeObjectURL(url), 1000);
      },
    );
  }
  async function downloadVouchers(item: BatchItem) {
    const book = item.result?.book_id,
      exported = item.result?.export_id;
    if (item.status !== 'succeeded' || typeof book !== 'string' || typeof exported !== 'string')
      return;
    await run(
      async (signal) => {
        const value = await api<{ id: string; csv: string; filename: string }>(
          `/books/${encodeURIComponent(book)}/exports/${encodeURIComponent(exported)}`,
          session,
          { signal },
        );
        if (
          value.id !== exported ||
          typeof value.csv !== 'string' ||
          typeof value.filename !== 'string' ||
          !value.filename
        )
          throw new Error('导出文件与所选记录不一致');
        return value;
      },
      (value) => downloadCsv(value.csv, value.filename),
    );
  }
  return (
    <Modal
      title="批量办理"
      subtitle={`${period} · 先预检，再确认；凭证必须已复核，交付包仅生成草稿`}
      onClose={onClose}
      busy={busy}
    >
      {error && (
        <p role="alert" className="form-error">
          {error}
        </p>
      )}
      {!denied && (
        <div className="batch-operations">
          {!batch ? (
            <>
              <label>
                办理内容
                <select
                  aria-label="批量办理内容"
                  value={kind}
                  disabled={busy}
                  onChange={(e) => changeKind(e.target.value as typeof kind)}
                >
                  <option value="review_export">已复核凭证导出</option>
                  <option value="package_draft">交付包草稿</option>
                </select>
              </label>
              <fieldset disabled={busy}>
                <legend>选择企业（仅当前月度列表这一页，不默认全选）</legend>
                {choices.map((row) => (
                  <label key={row.company_id} className="batch-checkbox">
                    <input
                      type="checkbox"
                      checked={selected.includes(row.company_id)}
                      onChange={(e) => {
                        setSelected(
                          e.target.checked
                            ? [...selected, row.company_id]
                            : selected.filter((id) => id !== row.company_id),
                        );
                        setPlan(null);
                        key.current = '';
                      }}
                    />
                    {row.company_name}
                  </label>
                ))}
                {!choices.length && <p>请先在月度列表查找需要办理的企业。</p>}
              </fieldset>
              <button
                className="button secondary"
                disabled={busy || !selected.length}
                onClick={() => void preview()}
              >
                预检所选企业
              </button>
              {plan && (
                <section aria-label="批量预检结果">
                  <p>
                    可办理 {plan.ready} 家 · 阻断 {plan.blocked} 家
                  </p>
                  {plan.companies.map((c) => (
                    <p key={c.company_id}>
                      {choices.find((r) => r.company_id === c.company_id)?.company_name}：
                      {c.blocked
                        ? blockedLabel(c.blocked)
                        : kind === 'review_export'
                          ? `已复核 ${c.drafts?.length} 张凭证`
                          : `已发布报表版本 ${c.report_version}`}
                    </p>
                  ))}
                  <p>阻断项将保留在逐户结果中；不会自动复核、发布或发送。</p>
                </section>
              )}
              <button
                className="button primary"
                disabled={busy || !plan?.ready}
                onClick={() => void create()}
              >
                确认办理 {plan?.ready ?? 0} 家
              </button>
              <section aria-label="已有批次">
                <h3>已有批次 · 全部月份</h3>
                <button
                  className="button secondary"
                  disabled={busy}
                  onClick={() => setHistoryRevision((v) => v + 1)}
                >
                  刷新批次记录
                </button>
                {history.map((h) => (
                  <button
                    className="button secondary"
                    disabled={busy}
                    key={h.id}
                    onClick={() => {
                      retryKey.current = '';
                      void load(h.id);
                    }}
                  >
                    {h.period} · {batchNames[h.kind]} · {h.id.slice(-6)}
                  </button>
                ))}
                {!history.length && <p>暂无批量记录</p>}
                <div className="batch-actions">
                  <button
                    className="button secondary"
                    disabled={busy || offset === 0}
                    onClick={() => setOffset(Math.max(0, offset - 20))}
                  >
                    上一页批次
                  </button>
                  <button
                    className="button secondary"
                    disabled={busy || offset + 20 >= total}
                    onClick={() => setOffset(offset + 20)}
                  >
                    下一页批次
                  </button>
                </div>
              </section>
            </>
          ) : (
            <>
              <h3>
                {batchNames[batch.kind]} · {batch.request?.period}
              </h3>
              <p role="status">
                已结束 {batch.progress.completed}/{batch.progress.total} 项 ·{' '}
                {batch.progress.percent}%
              </p>
              <div className="batch-rows" aria-label="批量逐户结果">
                {batch.items.map((item) => (
                  <article key={item.id} className="batch-row">
                    <header>
                      <strong>
                        {item.company_name ||
                          choices.find((r) => r.company_id === item.company_id)?.company_name ||
                          `企业 ${item.company_id}`}
                      </strong>
                      <span>{itemLabel(item)}</span>
                    </header>
                    {item.error_detail && <p>{item.error_detail}</p>}
                    {item.cancel_reason && <p>取消依据：{item.cancel_reason}</p>}
                    {item.skip_reason && <p>跳过依据：{item.skip_reason}</p>}
                    {item.status === 'succeeded' && (
                      <p>
                        {batch.kind === 'package_draft'
                          ? '交付包草稿已生成，请回企业交付页检查后发布。未自动发送。'
                          : batch.kind === 'review_export'
                            ? '已生成冻结导出记录，可下载凭证 CSV，并到企业交付页核验回执。'
                            : '开期已完成。'}
                      </p>
                    )}
                    <div className="batch-actions">
                      {batch.kind === 'review_export' &&
                        item.status === 'succeeded' &&
                        typeof item.result?.export_id === 'string' &&
                        typeof item.result?.book_id === 'string' && (
                          <button
                            className="button secondary"
                            disabled={busy}
                            onClick={() => void downloadVouchers(item)}
                          >
                            下载凭证 CSV
                          </button>
                        )}
                      {batchResultUrl(batch.kind, item, batch.request?.period ?? '') && (
                        <a
                          className="button secondary"
                          href={batchResultUrl(batch.kind, item, batch.request?.period ?? '')!}
                        >
                          打开办理结果
                        </a>
                      )}
                      {itemActions(item).map((verb) => (
                        <button
                          key={verb}
                          className="button secondary"
                          disabled={busy}
                          onClick={() => {
                            setAction({ item: item.id, name: item.company_name, verb });
                            setReason('');
                          }}
                        >
                          {verb === 'cancel' ? '取消此项' : '跳过此项'}
                        </button>
                      ))}
                    </div>
                  </article>
                ))}
              </div>
              {action && (
                <fieldset disabled={busy}>
                  <legend>
                    {action.verb === 'cancel' ? '取消' : '跳过'} · {action.name}
                  </legend>
                  <p>运行中的任务仅登记取消申请，已提交的结果会保留。</p>
                  <label>
                    操作原因
                    <textarea
                      aria-label="逐户操作原因"
                      value={reason}
                      maxLength={1000}
                      onChange={(e) => setReason(e.target.value)}
                    />
                  </label>
                  <button
                    className="button primary"
                    disabled={reason.trim().length < 3}
                    onClick={() => void control()}
                  >
                    确认{action.verb === 'cancel' ? '取消申请' : '跳过'}
                  </button>
                  <button className="button secondary" onClick={() => setAction(null)}>
                    保留此项
                  </button>
                </fieldset>
              )}
              <div className="batch-actions">
                <button
                  className="button secondary"
                  disabled={busy}
                  onClick={() => void load(batch.id)}
                >
                  刷新结果
                </button>
                <button
                  className="button secondary"
                  disabled={busy}
                  onClick={() => void failures()}
                >
                  下载失败清单
                </button>
                <button
                  className="button secondary"
                  disabled={
                    busy ||
                    ['running', 'queued'].includes(batch.status) ||
                    !batch.items.some(
                      (i) =>
                        i.status === 'failed' &&
                        !i.error_code?.includes('unknown') &&
                        typeof i.attempt === 'number' &&
                        typeof i.max_attempts === 'number' &&
                        i.attempt < i.max_attempts,
                    ) ||
                    batch.items.some((i) => i.error_code?.includes('unknown'))
                  }
                  onClick={() => void retry()}
                >
                  只重试失败项
                </button>
                <button
                  className="button secondary"
                  disabled={busy}
                  onClick={() => {
                    setBatch(null);
                    setAction(null);
                    setPlan(null);
                  }}
                >
                  返回批量选择
                </button>
              </div>
            </>
          )}
        </div>
      )}
    </Modal>
  );
}
