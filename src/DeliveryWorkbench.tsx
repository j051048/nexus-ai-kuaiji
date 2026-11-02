import { lazy, Suspense, useCallback, useEffect, useRef, useState } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import {
  ArrowLeft,
  ArrowDownToLine,
  CheckCheck,
  ClipboardList,
  FileCheck2,
  FileUp,
  LoaderCircle,
  RefreshCw,
  TriangleAlert,
} from 'lucide-react';
import { api, apiBlob, errorMessage } from './api';
import {
  decodeReceiptBytes,
  deliveryIssueLabel,
  deliveryStates,
  deliveryTime,
  validateReceiptFile,
} from './delivery-helpers';
import type {
  DeliveryDetail,
  DeliveryOverview,
  DeliveryReceipt,
  DeliveryReceiptItem,
  DeliveryWorkbenchProps,
} from './delivery-types';
import './delivery.css';
import { DeliveryNotifications } from './DeliveryNotifications';
import { MonthlyPackagePanel } from './MonthlyPackagePanel';
import './tax-workpapers.css';

const TaxWorkpaperPanel = lazy(() =>
  import('./TaxWorkpaperPanel').then((m) => ({ default: m.TaxWorkpaperPanel })),
);
export function DeliveryWorkbench(props: DeliveryWorkbenchProps) {
  const location = useLocation(),
    navigate = useNavigate();
  const tax = new URLSearchParams(location.search).get('section') === 'tax';
  const [busy, setBusy] = useState(false);
  const onBusyChange = useCallback(
    (value: boolean) => {
      setBusy(value);
      props.onBusyChange?.(value);
    },
    [props.onBusyChange],
  );
  function chooseTax(value: boolean) {
    if (busy) return;
    const query = new URLSearchParams(location.search);
    if (value) query.set('section', 'tax');
    else query.delete('section');
    void navigate({ pathname: location.pathname, search: query.toString() }, { replace: true });
  }
  return (
    <>
      <div className="delivery-sections" role="group" aria-label="本月交付作业">
        <button
          className="button secondary"
          aria-pressed={!tax}
          disabled={busy}
          onClick={() => chooseTax(false)}
        >
          交付与签收
        </button>
        <button
          className="button secondary"
          aria-pressed={tax}
          disabled={busy}
          onClick={() => chooseTax(true)}
        >
          税务底稿
        </button>
      </div>
      {tax ? (
        <Suspense fallback={<p role="status">正在打开税务底稿…</p>}>
          {props.companyId && props.period ? (
            <TaxWorkpaperPanel
              session={props.session}
              bookId={props.bookId}
              companyId={props.companyId}
              period={props.period}
              onBusyChange={onBusyChange}
            />
          ) : (
            <p role="status">正在确认账套授权范围…</p>
          )}
        </Suspense>
      ) : (
        <DeliveryReceipts {...props} onBusyChange={onBusyChange} />
      )}
    </>
  );
}

function DeliveryReceipts({
  session,
  bookId,
  period,
  initialExportId,
  onBack,
  onBusyChange,
  embedded = false,
}: DeliveryWorkbenchProps) {
  const [overview, setOverview] = useState<DeliveryOverview | null>(null);
  const [detail, setDetail] = useState<DeliveryDetail | null>(null);
  const [exportId, setExportId] = useState(initialExportId ?? '');
  const [offset, setOffset] = useState(0);
  const [receiptOffset, setReceiptOffset] = useState(0);
  const [file, setFile] = useState<{
    name: string;
    format: 'json' | 'csv';
    content: string;
  } | null>(null);
  const [reason, setReason] = useState('');
  const [note, setNote] = useState<{
    receipt: DeliveryReceipt;
    item: DeliveryReceiptItem;
    issue: string;
  } | null>(null);
  const [noteReason, setNoteReason] = useState('');
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const [detailError, setDetailError] = useState('');
  const [message, setMessage] = useState('');
  const scope = `${session.scope_key}:${bookId}:${initialExportId ?? ''}`;
  const scopeRef = useRef(scope);
  scopeRef.current = scope;
  const exportRef = useRef(exportId);
  exportRef.current = exportId;
  const requestEpoch = useRef(0);
  const detailEpoch = useRef(0);
  const active = useRef(true);
  const locked = useRef(false);
  const importKey = useRef('');
  const noteKey = useRef('');
  const input = useRef<HTMLInputElement>(null);
  const base = `/books/${encodeURIComponent(bookId)}/delivery`;
  useEffect(() => {
    active.current = true;
    return () => {
      active.current = false;
      requestEpoch.current++;
      detailEpoch.current++;
    };
  }, [scope]);
  useEffect(() => {
    onBusyChange?.(!!busy);
    return () => onBusyChange?.(false);
  }, [busy, onBusyChange]);
  const load = useCallback(
    async (signal?: AbortSignal) => {
      const epoch = ++requestEpoch.current;
      try {
        const value = await api<DeliveryOverview>(`${base}?limit=20&offset=${offset}`, session, {
          signal,
        });
        if (active.current && epoch === requestEpoch.current && !signal?.aborted)
          setOverview(value);
      } catch (caught) {
        if (active.current && epoch === requestEpoch.current && !signal?.aborted)
          setError(errorMessage(caught));
      }
    },
    [base, session, offset],
  );
  const loadDetail = useCallback(
    async (signal?: AbortSignal) => {
      if (!exportId) return;
      const epoch = ++detailEpoch.current;
      try {
        const value = await api<DeliveryDetail>(
          `${base}/exports/${encodeURIComponent(exportId)}?limit=20&offset=${receiptOffset}`,
          session,
          { signal },
        );
        if (active.current && epoch === detailEpoch.current && !signal?.aborted) {
          if (value.manifest.export_id !== exportId)
            throw new Error('交付记录与指定导出不一致，请返回待办重新读取。');
          setDetail(value);
          setDetailError('');
        }
      } catch (caught) {
        if (active.current && epoch === detailEpoch.current && !signal?.aborted) {
          setDetail(null);
          setDetailError(errorMessage(caught));
        }
      }
    },
    [base, session, exportId, receiptOffset],
  );
  useEffect(() => {
    setOverview(null);
    setDetail(null);
    setExportId(initialExportId ?? '');
    setOffset(0);
    setReceiptOffset(0);
    setFile(null);
    setReason('');
    setNote(null);
    setNoteReason('');
    setError('');
    setDetailError('');
    setMessage('');
    setBusy('');
    locked.current = false;
    importKey.current = '';
    noteKey.current = '';
  }, [scope, initialExportId]);
  useEffect(() => {
    const controller = new AbortController();
    void load(controller.signal);
    return () => {
      controller.abort();
      requestEpoch.current++;
    };
  }, [load]);
  useEffect(() => {
    const controller = new AbortController();
    setDetail(null);
    setDetailError('');
    setNote(null);
    setFile(null);
    setReason('');
    setError('');
    setMessage('');
    importKey.current = '';
    if (input.current) input.current.value = '';
    void loadDetail(controller.signal);
    return () => {
      controller.abort();
      detailEpoch.current++;
    };
  }, [loadDetail]);
  async function run(kind: string, action: () => Promise<void>) {
    if (locked.current) return;
    locked.current = true;
    setBusy(kind);
    setError('');
    setMessage('');
    const started = scope;
    try {
      await action();
    } catch (caught) {
      if (active.current && scopeRef.current === started) setError(errorMessage(caught));
    } finally {
      if (active.current && scopeRef.current === started) {
        locked.current = false;
        setBusy('');
      }
    }
  }
  function selectExport(id: string) {
    if (busy) return;
    setExportId(id);
    setReceiptOffset(0);
  }
  async function download(format: 'json' | 'csv') {
    const started = scope;
    const selected = exportId;
    await run('download', async () => {
      const blob = await apiBlob(
        `${base}/exports/${encodeURIComponent(selected)}/template?format=${format}`,
        session,
      );
      if (!active.current || scopeRef.current !== started) return;
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = `receipt-${selected}.${format}`;
      link.click();
      setTimeout(() => URL.revokeObjectURL(url), 0);
    });
  }
  async function chooseFile(selected: File | undefined) {
    if (!selected || busy) return;
    const started = scope;
    const selectedExport = exportId;
    try {
      const format = validateReceiptFile(selected.name, selected.size);
      const content = decodeReceiptBytes(await selected.arrayBuffer());
      if (!active.current || scopeRef.current !== started || selectedExport !== exportRef.current)
        return;
      setFile({ name: selected.name, format, content });
      setError('');
      setMessage('');
      importKey.current = '';
    } catch (caught) {
      if (active.current && scopeRef.current === started) {
        setFile(null);
        setError(errorMessage(caught));
      }
    }
  }
  async function submit() {
    if (!file || !reason.trim() || !exportId) return;
    const started = scope;
    const selectedExport = exportId;
    if (!importKey.current) importKey.current = crypto.randomUUID();
    await run('import', async () => {
      await api<DeliveryReceipt>(
        `${base}/exports/${encodeURIComponent(selectedExport)}/receipts/import`,
        session,
        {
          method: 'POST',
          body: {
            format: file.format,
            content: file.content,
            reason: reason.trim(),
            idempotency_key: importKey.current,
          },
        },
      );
      if (!active.current || scopeRef.current !== started) return;
      setMessage('回执事实已保存，差异由冻结导出清单重新核对。');
      setFile(null);
      setReason('');
      importKey.current = '';
      if (input.current) input.current.value = '';
      await Promise.all([load(), loadDetail()]);
    });
  }
  async function saveNote() {
    if (!note || !noteReason.trim()) return;
    const started = scope;
    if (!noteKey.current) noteKey.current = crypto.randomUUID();
    await run('note', async () => {
      await api(`${base}/discrepancies/notes`, session, {
        method: 'POST',
        body: {
          receipt_id: note.receipt.id,
          draft_id: note.item.draft_id,
          issue_key: note.issue,
          reason: noteReason.trim(),
          idempotency_key: noteKey.current,
        },
      });
      if (!active.current || scopeRef.current !== started) return;
      setNote(null);
      setNoteReason('');
      noteKey.current = '';
      setMessage('处理依据已追加；核验结果仍以回执事实和导出清单为准。');
      await loadDetail();
    });
  }
  const items = detail?.manifest.items ?? [];
  const Wrapper = embedded ? 'div' : 'main';
  return (
    <Wrapper className={'delivery-workbench' + (embedded ? ' embedded' : '')}>
      {embedded ? (
        <section className="book-embedded-heading">
          <div>
            <h2>交付与回执核验</h2>
            <p>
              {overview?.company_name ?? '当前账套'} · {overview?.book_name ?? ''} ·{' '}
              {overview?.period ?? ''}
            </p>
          </div>
          <button
            type="button"
            className="button secondary small"
            disabled={!!busy}
            onClick={() => {
              void load();
              void loadDetail();
            }}
          >
            <RefreshCw size={15} />
            刷新
          </button>
        </section>
      ) : (
        <section className="page-heading">
          <div>
            <p className="eyebrow">DELIVERY / RECEIPT / RECONCILIATION</p>
            <h1>导出之后，还要核对回执。</h1>
            <p className="page-description">
              {overview?.company_name} · {overview?.book_name} · {overview?.period}
              。会计导出的凭证保持冻结；回执和差异记录独立追加。
            </p>
          </div>
          <button type="button" className="button secondary" disabled={!!busy} onClick={onBack}>
            <ArrowLeft size={16} />
            返回账套
          </button>
        </section>
      )}
      <MonthlyPackagePanel
        key={scope}
        session={session}
        bookId={bookId}
        onBusyChange={onBusyChange}
      />
      <DeliveryNotifications
        key={'notify:' + scope}
        session={session}
        bookId={bookId}
        period={period}
      />
      <section className="delivery-notice">
        <FileCheck2 size={25} />
        <div>
          <strong>人工导入客户软件回执</strong>
          <p>
            目前通过中性 JSON / CSV
            核对凭证号、金额、科目与辅助值。尚未连接财务软件；核对一致仅表示所提交回执数据一致。
          </p>
        </div>
        <span>保留会计判断</span>
      </section>
      {error && (
        <p role="alert" className="inline-error">
          <TriangleAlert size={16} />
          {error}
        </p>
      )}
      {message && (
        <p role="status" className="inline-success">
          {message}
        </p>
      )}
      <div className="delivery-grid">
        <aside className="panel delivery-exports">
          <div className="panel-heading">
            <div>
              <h2>
                冻结的导出清单<span className="count-label">{overview?.pagination.total ?? 0}</span>
              </h2>
              <p>选择一次导出，追踪客户软件回执</p>
            </div>
            <button
              type="button"
              aria-label="刷新交付清单"
              className="icon-button"
              disabled={!!busy}
              onClick={() => {
                void load();
                void loadDetail();
              }}
            >
              <RefreshCw size={16} />
            </button>
          </div>
          {!overview ? (
            <div className="loading-block">
              <LoaderCircle size={16} className="spinner" />
              正在读取导出清单…
            </div>
          ) : overview.exports.length ? (
            overview.exports.map((row) => (
              <button
                type="button"
                key={row.id}
                className={`delivery-export-card ${row.id === exportId ? 'is-active' : ''}`}
                disabled={!!busy}
                onClick={() => selectExport(row.id)}
              >
                <span className="delivery-export-meta">
                  {deliveryTime(row.created_at)}
                  <span>{row.count} 张</span>
                </span>
                <strong>{row.filename}</strong>
                <span>
                  {row.manifest_status === 'captured' ? '原审核版本已留存' : '旧导出 · 待人工核实'}{' '}
                  · {row.receipt_count} 份回执
                </span>
              </button>
            ))
          ) : (
            <div className="empty-state">
              <ClipboardList size={28} />
              <strong>尚未导出凭证</strong>
              <p>会计审核并导出后，会在这里保留逐张清单。</p>
            </div>
          )}
          <div className="business-pagination">
            <span>第 {Math.floor(offset / 20) + 1} 页</span>
            <div>
              <button
                type="button"
                className="text-button"
                disabled={!!busy || offset === 0}
                onClick={() => {
                  setOffset((v) => Math.max(0, v - 20));
                  setExportId('');
                }}
              >
                上一页
              </button>
              <button
                type="button"
                className="text-button"
                disabled={!!busy || !overview?.pagination.has_more}
                onClick={() => {
                  setOffset((v) => v + 20);
                  setExportId('');
                }}
              >
                下一页
              </button>
            </div>
          </div>
        </aside>
        <section className="panel delivery-inspector">
          {!exportId ? (
            <div className="empty-state delivery-start">
              <div className="delivery-seal">
                <FileCheck2 size={30} />
              </div>
              <strong>把导出和客户软件凭证对应起来</strong>
              <p>保留原审核版本；确认接收结果，再逐行核对。</p>
            </div>
          ) : detailError ? (
            <div className="empty-state delivery-start" role="alert">
              <TriangleAlert size={26} />
              <strong>指定交付记录无法读取</strong>
              <p>{detailError}</p>
              <button
                className="button secondary"
                disabled={!!busy}
                onClick={() => void loadDetail()}
              >
                重新读取该交付记录
              </button>
            </div>
          ) : !detail ? (
            <div className="loading-block">
              <LoaderCircle size={17} className="spinner" />
              正在核对交付记录…
            </div>
          ) : (
            <>
              <div className="delivery-section-heading">
                <div>
                  <p className="eyebrow">FROZEN MANIFEST</p>
                  <h2>
                    {detail.manifest.status === 'captured'
                      ? `${items.length} 张凭证 · 原始导出版本`
                      : '旧导出历史清单待核实'}
                  </h2>
                </div>
                <span className="badge approved">凭证保持冻结</span>
              </div>
              {detail.manifest.status === 'pending_legacy' ? (
                <div className="delivery-legacy">
                  <p>{detail.manifest.reason}</p>
                  {detail.can_import && (
                    <button
                      type="button"
                      className="button secondary small"
                      disabled={!!busy}
                      onClick={() =>
                        void run('backfill', async () => {
                          await api(
                            `${base}/exports/${encodeURIComponent(exportId)}/backfill`,
                            session,
                            { method: 'POST' },
                          );
                          await Promise.all([load(), loadDetail()]);
                        })
                      }
                    >
                      核实历史审计并建立清单
                    </button>
                  )}
                </div>
              ) : (
                <>
                  <div className="delivery-fingerprint">
                    <span>导出指纹</span>
                    <code>{detail.manifest.canonical_fingerprint}</code>
                  </div>
                  {!detail.manifest.catalog_fingerprint && (
                    <p className="field-help">
                      清单由原审核审计与原 CSV 核实；历史科目配置版本未知。
                    </p>
                  )}
                  <details className="delivery-manifest-lines">
                    <summary>查看逐张审核版本与分录</summary>
                    {items.map((item) => (
                      <article key={item.draft_id}>
                        <h3>
                          {item.description}
                          <span>¥{item.amount}</span>
                        </h3>
                        <p>
                          {item.date} · 审核版本 {item.approved_version} · 导出版本{' '}
                          {item.exported_version}
                        </p>
                        <table>
                          <thead>
                            <tr>
                              <th>行</th>
                              <th>科目 / 辅助值</th>
                              <th>借方</th>
                              <th>贷方</th>
                            </tr>
                          </thead>
                          <tbody>
                            {item.lines.map((line) => (
                              <tr key={line.ordinal}>
                                <td>{line.ordinal}</td>
                                <td>
                                  {line.account_code}
                                  {Object.values(line.auxiliary_values).map((v, i) => (
                                    <small key={i}>{v}</small>
                                  ))}
                                </td>
                                <td>{line.debit}</td>
                                <td>{line.credit}</td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </article>
                    ))}
                  </details>
                </>
              )}
              {detail.can_import && (
                <section className="delivery-import">
                  <div className="delivery-section-heading">
                    <div>
                      <p className="eyebrow">RETURNED FACTS</p>
                      <h2>追加一份回执</h2>
                    </div>
                    <FileUp size={21} />
                  </div>
                  <p>
                    用客户软件导出的凭证记录填写模板。保留真实外部凭证号；结果不确定时填写 unknown。
                  </p>
                  <div className="delivery-template-actions">
                    <button
                      type="button"
                      className="button secondary small"
                      disabled={!!busy || detail.manifest.status !== 'captured'}
                      onClick={() => void download('json')}
                    >
                      <ArrowDownToLine size={14} />
                      JSON 回执模板
                    </button>
                    <button
                      type="button"
                      className="button secondary small"
                      disabled={!!busy || detail.manifest.status !== 'captured'}
                      onClick={() => void download('csv')}
                    >
                      <ArrowDownToLine size={14} />
                      CSV 回执模板
                    </button>
                  </div>
                  <label className="delivery-file-label">
                    <span>回执文件 · UTF-8 / 2 MB</span>
                    <input
                      ref={input}
                      type="file"
                      aria-label="交付回执文件"
                      accept=".json,.csv,application/json,text/csv"
                      disabled={!!busy}
                      onChange={(event) => void chooseFile(event.target.files?.[0])}
                    />
                  </label>
                  {file && (
                    <p className="field-help">
                      已选择 {file.name} · {file.format.toUpperCase()}
                    </p>
                  )}
                  <label>
                    <span>回执来源与核验依据</span>
                    <textarea
                      aria-label="回执来源与核验依据"
                      value={reason}
                      maxLength={1000}
                      disabled={!!busy}
                      onChange={(event) => {
                        setReason(event.target.value);
                        importKey.current = '';
                      }}
                      placeholder="例如：客户财务提供的十月凭证导入结果，已对照凭证号及明细"
                    />
                  </label>
                  <button
                    type="button"
                    className="button primary"
                    disabled={!!busy || !file || !reason.trim()}
                    onClick={() => void submit()}
                  >
                    {busy === 'import' ? (
                      <LoaderCircle size={16} className="spinner" />
                    ) : (
                      <FileCheck2 size={16} />
                    )}
                    导入并核对回执
                  </button>
                  <p className="field-help">
                    追加回执可以继续记录处理结果。系统不会重新发送凭证，也不会修改已导出分录。
                  </p>
                </section>
              )}
              <section className="delivery-receipts">
                <div className="delivery-section-heading">
                  <div>
                    <p className="eyebrow">RECEIPT HISTORY</p>
                    <h2>
                      回执与差异<span className="count-label">{detail.pagination.total}</span>
                    </h2>
                  </div>
                  <CheckCheck size={20} />
                </div>
                {!detail.receipts.length ? (
                  <p className="delivery-no-receipt">
                    尚未收到回执。导出文件不代表客户软件已成功入账。
                  </p>
                ) : (
                  detail.receipts.map((receipt) => (
                    <article className="delivery-receipt" key={receipt.id}>
                      <header>
                        <strong>{deliveryTime(receipt.created_at)}</strong>
                        <span>人工导入 · {receipt.import_format.toUpperCase()}</span>
                      </header>
                      <p>{receipt.reason}</p>
                      <div className="delivery-result-counts">
                        <span>
                          一致 <b>{receipt.summary.verified}</b>
                        </span>
                        <span>
                          待核验 <b>{receipt.summary.received_unverified}</b>
                        </span>
                        <span>
                          差异 <b>{receipt.summary.discrepancy}</b>
                        </span>
                      </div>
                      {receipt.items.map((item, index) => (
                        <div
                          key={`${item.draft_id}:${index}`}
                          className={`delivery-result-row ${item.state}`}
                        >
                          <div>
                            <span
                              className={`badge ${item.state === 'verified' ? 'approved' : 'draft'}`}
                            >
                              {deliveryStates[item.state]}
                            </span>
                            <strong>
                              {items.find((v) => v.draft_id === item.draft_id)?.description ??
                                item.draft_id}
                            </strong>
                            <p>外部凭证号：{item.external_voucher_id ?? '尚未提供'}</p>
                          </div>
                          {[...item.issues, ...item.missing_fields].map((issue) => (
                            <div className="delivery-issue" key={issue}>
                              <span>{deliveryIssueLabel(issue)}</span>
                              {detail.can_import && (
                                <button
                                  type="button"
                                  className="text-button"
                                  disabled={!!busy}
                                  onClick={() => {
                                    setNote({ receipt, item, issue });
                                    setNoteReason('');
                                    noteKey.current = '';
                                  }}
                                >
                                  记录处理依据
                                </button>
                              )}
                            </div>
                          ))}
                          {item.notes.map((row) => (
                            <p className="delivery-note" key={row.id}>
                              <span>{deliveryIssueLabel(row.issue_key)}</span>
                              {row.reason}
                            </p>
                          ))}
                        </div>
                      ))}
                    </article>
                  ))
                )}
                <div className="business-pagination">
                  <span>第 {Math.floor(receiptOffset / 20) + 1} 页</span>
                  <div>
                    <button
                      type="button"
                      className="text-button"
                      disabled={!!busy || receiptOffset === 0}
                      onClick={() => setReceiptOffset((v) => Math.max(0, v - 20))}
                    >
                      上一页回执
                    </button>
                    <button
                      type="button"
                      className="text-button"
                      disabled={!!busy || !detail.pagination.has_more}
                      onClick={() => setReceiptOffset((v) => v + 20)}
                    >
                      下一页回执
                    </button>
                  </div>
                </div>
              </section>
              {note && (
                <section className="delivery-note-editor">
                  <h3>追加处理依据 · {deliveryIssueLabel(note.issue)}</h3>
                  <textarea
                    aria-label="交付差异处理依据"
                    value={noteReason}
                    disabled={!!busy}
                    maxLength={1000}
                    onChange={(event) => {
                      setNoteReason(event.target.value);
                      noteKey.current = '';
                    }}
                    placeholder="记录已联系客户、差异原因及下一步核实动作"
                  />
                  <div>
                    <button
                      type="button"
                      className="button primary small"
                      disabled={!!busy || !noteReason.trim()}
                      onClick={() => void saveNote()}
                    >
                      保存处理依据
                    </button>
                    <button
                      type="button"
                      className="text-button"
                      disabled={!!busy}
                      onClick={() => setNote(null)}
                    >
                      取消
                    </button>
                  </div>
                  <p className="field-help">
                    文字说明保留处理过程；不能替代真实回执数据或改变核验结果。
                  </p>
                </section>
              )}
            </>
          )}
        </section>
      </div>
    </Wrapper>
  );
}
