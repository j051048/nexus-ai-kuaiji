import { useCallback, useEffect, useRef, useState } from 'react';
import { CheckCheck, FlaskConical, RefreshCw, TriangleAlert } from 'lucide-react';
import { api } from './api';
import type { Session } from './types';
import {
  labelIdentity,
  qualityBusinessTypes,
  qualityEligible,
  qualityFields,
  qualityGroupRate,
  qualityMeasurement,
  qualityModes,
  qualityStatuses,
} from './quality-helpers';
import type {
  QualityLabel,
  QualityMode,
  QualityOverview,
  QualityPreview,
  QualityRun,
  QualityStandard,
} from './quality-types';
import './quality.css';
import { QualityBenchmarkPanel } from './QualityBenchmarkPanel';

const encoded = encodeURIComponent;
const message = (error: unknown) =>
  error instanceof Error ? error.message : '无法读取评测证据，请刷新后重试。';

export function QualityWorkbench({
  session,
  bookId,
  onBack,
  onOpenDraft,
  onBusyChange,
}: {
  session: Session;
  bookId: string;
  onBack?: () => void;
  onOpenDraft?: (bookId: string, draftId: string) => void;
  onBusyChange?: (busy: boolean) => void;
}) {
  const [overview, setOverview] = useState<QualityOverview | null>(null),
    [preview, setPreview] = useState<QualityPreview | null>(null),
    [detail, setDetail] = useState<QualityStandard | null>(null),
    [runDetail, setRunDetail] = useState<QualityRun | null>(null);
  const [labels, setLabels] = useState<QualityLabel[]>([]),
    [selected, setSelected] = useState<string[]>([]),
    [mode, setMode] = useState<QualityMode>('rules'),
    [model, setModel] = useState(''),
    [prompt, setPrompt] = useState(''),
    [reason, setReason] = useState(''),
    [checked, setChecked] = useState(false);
  const [offset, setOffset] = useState(0),
    [operationBusy, setBusy] = useState(false),
    [loading, setLoading] = useState(false),
    [error, setError] = useState('');
  const [benchmarkBusy, setBenchmarkBusy] = useState(false);
  const busy = operationBusy || benchmarkBusy;
  const scope = `${session.scope_key}:${bookId}`,
    scopeRef = useRef(scope);
  scopeRef.current = scope;
  const active = useRef(true),
    epoch = useRef(0),
    commands = useRef(0),
    locked = useRef(false);
  const path = `/books/${encoded(bookId)}/quality`;
  useEffect(() => {
    active.current = true;
    return () => {
      active.current = false;
      epoch.current += 1;
      commands.current += 1;
    };
  }, [scope]);
  useEffect(() => {
    onBusyChange?.(busy);
    return () => onBusyChange?.(false);
  }, [busy, onBusyChange]);
  useEffect(() => {
    setOverview(null);
    setPreview(null);
    setDetail(null);
    setRunDetail(null);
    setSelected([]);
    setLabels([]);
    setReason('');
    setChecked(false);
    setError('');
    setOffset(0);
    locked.current = false;
    setBusy(false);
  }, [scope]);
  const load = useCallback(
    async (signal?: AbortSignal) => {
      const stamp = ++epoch.current,
        captured = scope;
      setLoading(true);
      try {
        const value = await api<QualityOverview>(`${path}?limit=20&offset=${offset}`, session, {
          signal,
        });
        if (
          active.current &&
          !signal?.aborted &&
          epoch.current === stamp &&
          captured === scopeRef.current
        ) {
          setOverview(value);
          setSelected((old) =>
            old.filter((id) =>
              value.standards.some((row) => row.id === id && qualityEligible(row)),
            ),
          );
          setError('');
        }
      } catch (failure) {
        if (
          active.current &&
          !signal?.aborted &&
          epoch.current === stamp &&
          captured === scopeRef.current
        ) {
          setOverview(null);
          setPreview(null);
          setDetail(null);
          setRunDetail(null);
          setSelected([]);
          setError(message(failure));
        }
      } finally {
        if (active.current && epoch.current === stamp && captured === scopeRef.current)
          setLoading(false);
      }
    },
    [offset, path, scope, session],
  );
  useEffect(() => {
    const controller = new AbortController();
    void load(controller.signal);
    return () => controller.abort();
  }, [load]);
  const command = useCallback(
    async (action: () => Promise<unknown>) => {
      if (locked.current) return;
      const captured = scope,
        stamp = ++commands.current;
      locked.current = true;
      setBusy(true);
      setError('');
      try {
        await action();
        if (active.current && captured === scopeRef.current && commands.current === stamp)
          await load();
      } catch (failure) {
        if (active.current && captured === scopeRef.current && commands.current === stamp) {
          setPreview(null);
          setDetail(null);
          setRunDetail(null);
          await load();
          setError(message(failure));
        }
      } finally {
        if (commands.current === stamp) locked.current = false;
        if (active.current && captured === scopeRef.current && commands.current === stamp)
          setBusy(false);
      }
    },
    [load, scope],
  );
  function inspect(kind: 'candidates' | 'standards', id: string) {
    const captured = scope;
    void command(async () => {
      const value = await api<QualityPreview & QualityStandard>(
        `${path}/${kind}/${encoded(id)}`,
        session,
      );
      if (captured !== scopeRef.current || !active.current) return;
      setPreview(value);
      setDetail(kind === 'standards' ? value : null);
      setLabels(value.field_labels ?? []);
      setReason('');
      setChecked(false);
      setRunDetail(null);
    });
  }
  function setLabel(label: QualityLabel, enabled: boolean) {
    setLabels((old) => [
      ...old.filter((item) => labelIdentity(item) !== labelIdentity(label)),
      ...(enabled ? [label] : []),
    ]);
  }
  function createStandard() {
    if (!preview?.sample_id) return;
    const sample = preview.sample_id;
    void command(async () => {
      const value = await api<QualityPreview & QualityStandard>(`${path}/standards`, session, {
        method: 'POST',
        body: {
          sample_id: sample,
          field_labels: labels,
          reason,
          idempotency_key: crypto.randomUUID(),
        },
      });
      if (active.current && scopeRef.current === scope) {
        setPreview(value);
        setDetail(value);
        setReason('');
        setChecked(false);
      }
    });
  }
  function confirmStandard() {
    if (!detail) return;
    void command(async () => {
      const value = await api<QualityPreview & QualityStandard>(
        `${path}/standards/${encoded(detail.id)}/confirm`,
        session,
        { method: 'POST', body: { reason, evidence_checked: checked } },
      );
      if (active.current && scopeRef.current === scope) {
        setPreview(value);
        setDetail(value);
        setReason('');
        setChecked(false);
      }
    });
  }
  function createRun() {
    void command(async () => {
      const value = await api<QualityRun>(`${path}/runs`, session, {
        method: 'POST',
        body: {
          standard_ids: selected,
          mode,
          ...(mode.startsWith('stored_') ? { model, prompt_version: prompt } : {}),
          idempotency_key: crypto.randomUUID(),
        },
      });
      if (active.current && scopeRef.current === scope) {
        setRunDetail(value);
        setPreview(null);
        setDetail(null);
      }
    });
  }
  return (
    <main className="quality-workbench">
      <section className="page-heading">
        <div>
          <p className="eyebrow">EVIDENCE / REPLAY</p>
          <h1>用经过独立核对的业务，检验自动化。</h1>
          <p className="page-description">
            冻结已审核分录与原件，另一位会计确认标准后回放。每项结果注明测试范围，缺少覆盖会单独列出。
          </p>
        </div>
        <div className="quality-actions">
          {onBack && (
            <button className="button secondary" disabled={busy} onClick={onBack}>
              返回工作台
            </button>
          )}
          <button
            className="button secondary"
            disabled={busy || loading}
            onClick={() => void load()}
          >
            <RefreshCw size={15} />
            刷新评测证据
          </button>
        </div>
      </section>
      {error && (
        <p className="inline-error" role="alert">
          <TriangleAlert size={16} />
          {error}
        </p>
      )}
      {overview && (
        <>
          <section className="quality-metrics">
            <article>
              <span>账套已记录人工时间</span>
              <strong>{qualityMeasurement(overview.human_minutes, ' 分钟')}</strong>
            </article>
            <article>
              <span>已响应补件的日历等待</span>
              <strong>
                {overview.supplement_wait_seconds === null
                  ? '尚未采集'
                  : `${Math.round(overview.supplement_wait_seconds / 60)} 分钟`}
              </strong>
            </article>
            <article>
              <span>人工导入回执差异项</span>
              <strong>{qualityMeasurement(overview.delivery_differences)}</strong>
              <small>
                观察 {overview.delivery_measurement.observed_receipts} 次回执
                {overview.delivery_measurement.truncated ? ' · 已截断' : ''}
              </small>
            </article>
          </section>
          <p className="quality-note">
            {overview.measurement_note.replace('未采集为null', '尚未采集的数据不参与计算')}
          </p>
          <section
            className="quality-card quality-group-summary"
            aria-label="企业月份与业务类型评测汇总"
          >
            <h2>{overview.period} · 按业务类型看回放结果</h2>
            <p>
              各回放范围只取最新一次最多 100
              个已选标准；计数保留历史，配置已变化时停止展示当前一致比例。业务类型工时、补件等待与交付差异尚不能准确归因，不分摊估算。
            </p>
            <div className="quality-table">
              <table>
                <thead>
                  <tr>
                    <th>业务类型</th>
                    <th>回放范围</th>
                    <th>确认标准</th>
                    <th>本次样本</th>
                    <th>一致</th>
                    <th>差异</th>
                    <th>未覆盖</th>
                    <th>失效</th>
                    <th>当前已测项一致比例</th>
                  </tr>
                </thead>
                <tbody>
                  {(overview.report_groups ?? []).map((row) => (
                    <tr key={`${row.mode}:${row.business_type}`}>
                      <td>{qualityBusinessTypes[row.business_type] ?? row.business_type}</td>
                      <td>{qualityModes[row.mode]}</td>
                      <td>
                        {overview.summary_by_business_type.find(
                          (item) => item.business_type === row.business_type,
                        )?.confirmed_count ?? 0}
                      </td>
                      <td>{row.sample_count}</td>
                      <td>{row.counts.passed}</td>
                      <td>{row.counts.different}</td>
                      <td>{row.counts.uncovered}</td>
                      <td>{row.counts.stale}</td>
                      <td>
                        {qualityGroupRate(row)}
                        <small className="quality-run-date">
                          {new Date(row.created_at).toLocaleString('zh-CN')}
                        </small>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {!(overview.report_groups ?? []).length && (
              <p className="quality-empty">尚无可汇总的独立确认样本回放。</p>
            )}
          </section>
          <section className="quality-grid">
            <div className="quality-card">
              <h2>选择已审核业务，建立标准</h2>
              <p>原始候选和未审核草稿不能成为标准。字段答案由会计核对原件后录入。</p>
              {overview.candidates.map((row) => (
                <button
                  className="quality-row"
                  key={row.id}
                  disabled={busy}
                  onClick={() => inspect('candidates', row.id)}
                >
                  <span>{row.counterparty}</span>
                  <strong>¥{row.amount}</strong>
                  <small>已审核 · 查看冻结输入</small>
                </button>
              ))}
              {!overview.candidates.length && (
                <p className="quality-empty">本页没有已审核业务样本。</p>
              )}
            </div>
            <div className="quality-card">
              <h2>独立确认的评测标准</h2>
              {overview.standards.map((row) => (
                <div className="quality-standard" key={row.id}>
                  <input
                    type="checkbox"
                    aria-label={`选择评测标准 ${row.counterparty} ${row.amount}`}
                    checked={selected.includes(row.id)}
                    disabled={busy || !qualityEligible(row)}
                    onChange={(event) =>
                      setSelected((old) =>
                        event.target.checked ? [...old, row.id] : old.filter((id) => id !== row.id),
                      )
                    }
                  />
                  <button
                    className="quality-row"
                    disabled={busy}
                    onClick={() => inspect('standards', row.id)}
                  >
                    <span>{row.counterparty}</span>
                    <strong>¥{row.amount}</strong>
                    <small>
                      {row.stale ? '证据已变化' : row.confirmed ? '已独立确认' : '待另一位会计确认'}{' '}
                      · {row.label_count} 个字段标注
                    </small>
                  </button>
                </div>
              ))}
              {!overview.standards.length && (
                <p className="quality-empty">先核对真实已审核业务并提议标准。</p>
              )}
            </div>
          </section>
          <section className="quality-card">
            <h2>
              <FlaskConical size={18} />
              回放当前配置或指定模型版本
            </h2>
            <div className="quality-run-controls">
              <label>
                回放范围
                <select
                  aria-label="评测回放范围"
                  disabled={busy}
                  value={mode}
                  onChange={(event) => setMode(event.target.value as QualityMode)}
                >
                  {Object.entries(qualityModes).map(([key, value]) => (
                    <option key={key} value={key}>
                      {value}
                    </option>
                  ))}
                </select>
              </label>
              {mode.startsWith('stored_') && (
                <>
                  <label>
                    模型名称
                    <input
                      aria-label="评测模型名称"
                      value={model}
                      onChange={(event) => setModel(event.target.value)}
                    />
                  </label>
                  <label>
                    提示词版本
                    <input
                      aria-label="评测提示词版本"
                      value={prompt}
                      onChange={(event) => setPrompt(event.target.value)}
                    />
                  </label>
                </>
              )}
              <button
                className="button primary"
                disabled={
                  busy ||
                  !selected.length ||
                  (mode.startsWith('stored_') && (!model.trim() || !prompt.trim()))
                }
                onClick={createRun}
              >
                回放 {selected.length} 个确认标准
              </button>
            </div>
            <p>
              最多 100
              个标准；模型回放使用已保存的成功结果。不会发起新模型请求，也不会修改或审批账务。
            </p>
            <div className="quality-history">
              {overview.runs.map((row) => (
                <button
                  key={row.id}
                  disabled={busy}
                  onClick={() => {
                    const captured = scope;
                    void command(async () => {
                      const value = await api<QualityRun>(
                        `${path}/runs/${encoded(row.id)}`,
                        session,
                      );
                      if (active.current && captured === scopeRef.current) {
                        setRunDetail(value);
                        setPreview(null);
                      }
                    });
                  }}
                >
                  {qualityModes[row.settings.mode]} · 一致 {row.counts.passed} / 差异{' '}
                  {row.counts.different} / 未覆盖 {row.counts.uncovered} ·{' '}
                  {new Date(row.created_at).toLocaleString('zh-CN')}
                </button>
              ))}
            </div>
          </section>
          <QualityBenchmarkPanel
            key={scope}
            session={session}
            bookId={bookId}
            selected={selected}
            runs={overview.runs}
            mode={mode}
            model={model}
            promptVersion={prompt}
            externalBusy={operationBusy}
            onBusyChange={setBenchmarkBusy}
            onRun={(value) => {
              setRunDetail(value);
              setPreview(null);
              void load();
            }}
          />
          <div className="quality-pagination">
            <button
              className="button secondary"
              disabled={busy || loading || offset === 0}
              onClick={() => {
                setSelected([]);
                setOffset((old) => Math.max(0, old - 20));
              }}
            >
              上一页
            </button>
            <span>
              第 {Math.floor(offset / 20) + 1} 页 · 已审核样本{' '}
              {overview.pagination.totals.candidates} / 标准 {overview.pagination.totals.standards}
            </span>
            <button
              className="button secondary"
              disabled={
                busy ||
                loading ||
                offset + 20 >= Math.max(...Object.values(overview.pagination.totals))
              }
              onClick={() => {
                setSelected([]);
                setOffset((old) => old + 20);
              }}
            >
              下一页
            </button>
          </div>
        </>
      )}
      {preview && (
        <section className="quality-card quality-detail" aria-label="评测标准原件与分录">
          <h2>{detail ? '冻结标准及独立确认' : '核对已审核业务原件'}</h2>
          <div className="quality-actions">
            <span>
              {preview.snapshot.payload.occurred_on} · {preview.snapshot.payload.counterparty} · ¥
              {preview.snapshot.payload.amount}
            </span>
            {onOpenDraft && (
              <button
                className="button secondary"
                disabled={busy}
                onClick={() => onOpenDraft(bookId, preview.draft_id)}
              >
                打开原凭证与附件
              </button>
            )}
          </div>
          <p>{preview.final.description}</p>
          <div className="quality-table">
            <table>
              <thead>
                <tr>
                  <th>科目</th>
                  <th>借方</th>
                  <th>贷方</th>
                  <th>辅助项</th>
                </tr>
              </thead>
              <tbody>
                {preview.final.lines.map((line, index) => (
                  <tr key={index}>
                    <td>{line.account_code}</td>
                    <td>{line.debit}</td>
                    <td>{line.credit}</td>
                    <td>{Object.values(line.auxiliary_values).join(' · ') || '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p>原件引用：{preview.evidence.evidence_ref || '见附件'}</p>
          {preview.evidence.attachments.map((item) => (
            <p className="quality-original" key={item.id}>
              {item.filename} · {item.size_bytes} 字节 · SHA256 {item.sha256}
            </p>
          ))}
          {preview.evidence.ocr.map((extraction) => (
            <div key={extraction.id} className="quality-ocr">
              <h3>
                {extraction.model} · {extraction.prompt_version}
              </h3>
              {extraction.result.documents.map((doc, index) => (
                <div key={index}>
                  <h4>第 {index + 1} 张票据</h4>
                  {doc.pages.map((page) => (
                    <details key={page.page}>
                      <summary>识别原文，第 {page.page} 页</summary>
                      <pre>{page.raw_text}</pre>
                    </details>
                  ))}
                  <p>选择需要测量的字段，按原件填写标准答案。留空表示原件未提供该字段。</p>
                  {Object.entries(qualityFields).map(([field, name]) => {
                    const key = { extraction_id: extraction.id, document_index: index, field },
                      existing = labels.find((item) => labelIdentity(item) === labelIdentity(key)),
                      label = existing ?? { ...key, page: doc.pages[0]?.page ?? 1, value: null };
                    return (
                      <div className="quality-label-row" key={field}>
                        <label>
                          <input
                            type="checkbox"
                            checked={!!existing}
                            disabled={busy || !!detail}
                            onChange={(event) => setLabel(label, event.target.checked)}
                          />
                          {name}
                        </label>
                        <span>AI：{String(doc[field] ?? '未提取')}</span>
                        <input
                          aria-label={`标准答案 ${name} ${index + 1}`}
                          value={label.value ?? ''}
                          placeholder="按原件填写，未提供则留空"
                          disabled={busy || !!detail || !existing}
                          onChange={(event) =>
                            setLabel({ ...label, value: event.target.value || null }, true)
                          }
                        />
                        <select
                          aria-label={`标准页码 ${name} ${index + 1}`}
                          value={label.page}
                          disabled={busy || !!detail || !existing}
                          onChange={(event) =>
                            setLabel({ ...label, page: Number(event.target.value) }, true)
                          }
                        >
                          {doc.pages.map((page) => (
                            <option key={page.page} value={page.page}>
                              第 {page.page} 页
                            </option>
                          ))}
                        </select>
                      </div>
                    );
                  })}
                </div>
              ))}
            </div>
          ))}
          {detail?.confirmed && (
            <p className="quality-confirmed">
              <CheckCheck size={18} />
              另一位会计已确认：{detail.confirmation?.reason}
            </p>
          )}
          {!detail?.confirmed && (
            <>
              <label className="quality-reason">
                原件核对依据
                <textarea
                  aria-label="评测标准核对依据"
                  value={reason}
                  maxLength={1000}
                  disabled={busy || (!!detail && !detail.can_confirm)}
                  onChange={(event) => setReason(event.target.value)}
                />
              </label>
              {detail ? (
                <>
                  <label>
                    <input
                      type="checkbox"
                      checked={checked}
                      disabled={busy || !detail.can_confirm || detail.stale}
                      onChange={(event) => setChecked(event.target.checked)}
                    />
                    我已独立核对原件、字段答案及最终分录
                  </label>
                  <button
                    className="button primary"
                    disabled={
                      busy || !detail.can_confirm || detail.stale || !checked || !reason.trim()
                    }
                    onClick={confirmStandard}
                  >
                    独立确认评测标准
                  </button>
                  {!detail.can_confirm && <p>提议人须交由另一位具有本账套复核权限的会计确认。</p>}
                </>
              ) : (
                <button
                  className="button primary"
                  disabled={busy || !reason.trim()}
                  onClick={createStandard}
                >
                  冻结并提议评测标准
                </button>
              )}
            </>
          )}
        </section>
      )}
      {runDetail && (
        <section className="quality-card quality-detail" aria-label="评测回放报告">
          <h2>逐项回放报告{runDetail.stale ? ' · 当前配置或证据已变化' : ''}</h2>
          <p>{runDetail.report.scope_note}</p>
          {runDetail.report.items.map((row) => (
            <article key={row.standard_id} className={`quality-result ${row.status}`}>
              <h3>
                {qualityStatuses[row.status]}{' '}
                <small>{qualityBusinessTypes[row.business_type] ?? '其他业务'}</small>
              </h3>
              <p>
                {String(
                  row.provenance.reason ??
                    `配置版本：${row.provenance.version ?? runDetail.settings.prompt_version ?? '当前'}`,
                )}
              </p>
              {row.differences.map((difference, index) => (
                <div key={index} className="quality-difference">
                  <strong>
                    {qualityFields[difference.field] ??
                      (difference.field === 'lines'
                        ? '科目及借贷金额'
                        : difference.field === 'auxiliary'
                          ? '辅助项'
                          : difference.field === 'reference'
                            ? '原件字段与页码引用'
                            : difference.field)}
                  </strong>
                  <div>
                    <span>标准：{JSON.stringify(difference.expected)}</span>
                    <span>候选：{JSON.stringify(difference.actual)}</span>
                  </div>
                </div>
              ))}
              {onOpenDraft && (
                <button
                  className="button secondary"
                  disabled={busy}
                  onClick={() => onOpenDraft(bookId, row.draft_id)}
                >
                  查看对应凭证
                </button>
              )}
            </article>
          ))}
        </section>
      )}
    </main>
  );
}
