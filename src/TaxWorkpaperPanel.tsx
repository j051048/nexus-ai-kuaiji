import { useCallback, useEffect, useRef, useState } from 'react';
import { api, apiBlob, ApiError } from './api';
import { useScopedQuery } from './useScopedQuery';
import { TaxReadinessList } from './TaxReadinessList';
import { parseReadiness, type FilingReadiness } from './tax-readiness';
import type { Session } from './types';
import { taxEvidence } from './tax-evidence';
import {
  parseTaxContext,
  parseTaxList,
  parseTaxWorkpaper,
  taxAction,
  taxMoney,
  taxRateBp,
  taxFailureBlock,
  taxBlocked,
  taxBlockAfterRead,
  type TaxBlock,
  type TaxContext,
  type TaxWorkpaper,
} from './tax-workpapers';
import './tax-workpapers.css';

type Props = {
  session: Session;
  bookId: string;
  companyId: string;
  period: string;
  onBusyChange?: (busy: boolean) => void;
};
type AccountRole = 'vat_payable' | 'excluded';
function failureText(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 401) return '登录已失效，请重新登录后返回此账套。';
    if (error.code === 'admin_mfa_required')
      return '需要二次验证。完成验证后重新读取，原操作不会自动提交。';
    if (error.status === 403 || error.status === 404)
      return '当前账套授权已变化或没有这项权限。请联系账套负责人确认后重新读取。';
    if (error.status === 409 || error.status === 422)
      return `${error.message} 请核对当前来源与版本；需要时由会计重新生成底稿，不会自动重试。`;
  }
  return '税务数据暂时无法读取或校验未通过。请重新读取；仍失败时联系账套负责人。';
}
function accountKey(id: string, version: number) {
  return JSON.stringify([id, version]);
}
export function TaxWorkpaperPanel(props: Props) {
  if (!props.session.firm_id) return <p role="alert">请先选择所属事务所，再读取税务底稿。</p>;
  return (
    <TaxContent
      key={JSON.stringify([
        props.session.scope_key,
        props.session.user_id,
        props.session.firm_id,
        props.companyId,
        props.bookId,
        props.period,
      ])}
      {...props}
    />
  );
}
function TaxContent({ session, bookId, companyId, period, onBusyChange }: Props) {
  const scope = { firm_id: session.firm_id ?? '', company_id: companyId, book_id: bookId, period };
  const query = `book_id=${encodeURIComponent(bookId)}&period=${encodeURIComponent(period)}`;
  const [failure, setFailure] = useState(''),
    [notice, setNotice] = useState('');
  const [busy, setBusy] = useState(''),
    [selectedId, setSelectedId] = useState('');
  const [roles, setRoles] = useState<Record<string, AccountRole | ''>>({});
  const [generationReason, setGenerationReason] = useState(''),
    [reviewReason, setReviewReason] = useState('');
  const [confirmed, setConfirmed] = useState(false);
  const [blocked, setBlocked] = useState<TaxBlock>(null);
  const [denied, setDenied] = useState(false);
  const live = useRef(true),
    locked = useRef(false),
    write = useRef(new AbortController());
  useEffect(() => {
    live.current = true;
    return () => {
      live.current = false;
      write.current.abort();
    };
  }, []);
  useEffect(() => {
    onBusyChange?.(!!busy);
    return () => onBusyChange?.(false);
  }, [busy, onBusyChange]);
  const load = useCallback(
    async (signal: AbortSignal) => {
      try {
        const [context, papers] = await Promise.all([
          api<unknown>(`/tax/context?${query}`, session, { signal }),
          api<unknown>(`/tax/workpapers?${query}`, session, { signal }),
        ]);
        // Accept both responses atomically, before any field is put on screen.
        return { context: parseTaxContext(context, scope), papers: parseTaxList(papers, scope) };
      } catch (error) {
        if (signal.aborted) throw error;
        throw new Error(failureText(error));
      }
      // This component is remounted for any identity/scope change.
    },
    [query, session],
  );
  const read = useScopedQuery(load, JSON.stringify([session.scope_key, companyId, bookId, period]));
  const data = denied ? null : read.data;
  const context = data?.context,
    papers = data?.papers ?? [];
  const selected = selectedId ? papers.find((p) => p.id === selectedId) : papers[0];
  const latest = papers[0]?.version ?? 0;
  const supported =
    context?.profile?.taxpayer_type === 'small_scale' && context.profile.filing_cycle === 'monthly';
  const mapped =
    !!context &&
    context.accounts.every((a) => !!roles[accountKey(a.account_id, a.account_version)]);
  const actions =
    selected && context
      ? taxAction(selected, context, session.user_id, latest, taxBlocked(blocked, selected))
      : { review: false, export: false };

  function refresh() {
    if (locked.current) return;
    setFailure('');
    setNotice('');
    setDenied(false);
    setConfirmed(false);
    setReviewReason('');
    setRoles({});
    // Read again after a transient/unknown response; never auto-submit. A source
    // conflict remains bound to that frozen version, not all future versions.
    setBlocked(taxBlockAfterRead);
    read.reload();
  }
  async function run(name: string, operation: () => Promise<void>, target?: TaxWorkpaper) {
    if (locked.current) return;
    locked.current = true;
    setBusy(name);
    setFailure('');
    setNotice('');
    try {
      await operation();
    } catch (error) {
      if (!live.current || write.current.signal.aborted) return;
      setFailure(failureText(error));
      setConfirmed(false);
      setReviewReason('');
      if (error instanceof ApiError && [401, 403, 404].includes(error.status)) {
        setDenied(true);
        read.reload();
      } else
        setBlocked(taxFailureBlock(error instanceof ApiError ? error.status : undefined, target));
    } finally {
      if (live.current) {
        locked.current = false;
        setBusy('');
      }
    }
  }
  async function generate() {
    if (!context || !supported || !mapped || !context.permissions.edit) return;
    await run('generate', async () => {
      const value = await api<unknown>('/tax/workpapers', session, {
        method: 'POST',
        signal: write.current.signal,
        body: {
          book_id: bookId,
          period,
          reason: generationReason.trim(),
          vat_accounts: context.accounts.map((a) => ({
            account_id: a.account_id,
            account_version: a.account_version,
            role: roles[accountKey(a.account_id, a.account_version)],
          })),
        },
      });
      const paper = parseTaxWorkpaper(value, scope);
      if (!live.current) return;
      setSelectedId(paper.id);
      setBlocked(null);
      setConfirmed(false);
      setReviewReason('');
      setNotice('已生成待复核底稿，请由另一位有权限的会计独立复核。尚未申报。');
      read.reload();
    });
  }
  async function review() {
    if (!selected || !actions.review || !confirmed || reviewReason.trim().length < 3) return;
    const target = selected;
    await run(
      'review',
      async () => {
        const raw = await api<unknown>(
          `/tax/workpapers/${encodeURIComponent(target.id)}/review`,
          session,
          {
            method: 'POST',
            signal: write.current.signal,
            body: {
              book_id: bookId,
              expected_version: target.version,
              reason: reviewReason.trim(),
            },
          },
        );
        const result = parseTaxWorkpaper(raw, scope);
        if (
          result.id !== target.id ||
          result.version !== target.version ||
          result.source_digest !== target.source_digest ||
          result.status !== 'reviewed'
        )
          throw new Error('Invalid review response');
        if (!live.current) return;
        setConfirmed(false);
        setReviewReason('');
        setNotice('独立复核已完成，可导出当前底稿。导出仍不代表申报。');
        read.reload();
      },
      target,
    );
  }
  async function download() {
    if (!selected || !actions.export) return;
    const target = selected;
    await run(
      'export',
      async () => {
        const blob = await apiBlob(
          `/tax/workpapers/${encodeURIComponent(target.id)}/csv?book_id=${encodeURIComponent(bookId)}&expected_version=${target.version}&source_digest=${target.source_digest}`,
          session,
          { signal: write.current.signal },
        );
        if (!live.current) return;
        const url = URL.createObjectURL(blob),
          a = document.createElement('a');
        a.href = url;
        a.download = `税务底稿-${period}-v${target.version}.csv`;
        a.click();
        setTimeout(() => URL.revokeObjectURL(url), 0);
        setNotice('已导出复核底稿；请按正常申报流程办理，系统未提交申报。');
      },
      target,
    );
  }
  const [readiness, setReadiness] = useState<FilingReadiness | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    let live = true;
    api<unknown>(
      `/tax/readiness?book_id=${encodeURIComponent(bookId)}&period=${encodeURIComponent(period)}`,
      session,
      { signal: controller.signal },
    )
      .then((raw) => {
        if (live) setReadiness(parseReadiness(raw));
      })
      .catch(() => {
        if (live) setReadiness(null);
      });
    return () => {
      live = false;
      controller.abort();
    };
  }, [session, bookId, period]);

  return (
    <section className="tax-workpaper" aria-label="税务底稿" aria-busy={!!busy || read.loading}>
      <header className="tax-heading">
        <div>
          <h2>税务底稿</h2>
          <p>
            {context?.scope.book_name ?? '当前账套'} · {period}
          </p>
        </div>
        <button className="button secondary" disabled={!!busy} onClick={refresh}>
          重新读取税务数据
        </button>
      </header>
      <p className="tax-boundary">
        用于会计核对与导出，尚未申报。来源会在复核和导出时重新校验；模型不会代替独立复核。
      </p>
      <TaxReadinessList data={readiness} />
      {(failure || read.error) && (
        <div role="alert" className="tax-alert">
          {failure || read.error}
        </div>
      )}
      {notice && (
        <p role="status" className="tax-notice">
          {notice}
        </p>
      )}
      {read.loading && <p role="status">正在读取本账套税务档案与底稿…</p>}
      {context && !read.loading && (
        <>
          <ol className="tax-steps">
            <li>1 核对档案</li>
            <li>2 确认来源与科目</li>
            <li>3 独立复核后导出</li>
          </ol>
          <TaxProfileForm
            key={context.profile?.id ?? 'empty'}
            context={context}
            busy={!!busy}
            onSave={(body) =>
              run('profile', async () => {
                await api('/tax/profile', session, {
                  method: 'PUT',
                  signal: write.current.signal,
                  body: { ...body, book_id: bookId },
                });
                if (live.current) {
                  setNotice('税务档案已保存。请核对当前生效档案；影响本月来源时需重新生成底稿。');
                  read.reload();
                }
              })
            }
          />
          <section className="tax-card" aria-label="来源与科目用途">
            <h3>2. 确认来源与科目</h3>
            <p>
              发票来自已导入的原始文件；以下是本月正式凭证使用的科目。请逐项确认用途，系统不会按名称猜测。
            </p>
            {!context.profile && (
              <p className="tax-alert">请先由有管理权限的负责人登记本月税务档案。</p>
            )}
            {context.profile && !supported && (
              <p className="tax-alert">
                当前仅支持小规模纳税人按月底稿。一般纳税人或季度场景请交人工处理，不会生成或自动申报。
              </p>
            )}
            {context.blocking_reasons.length > 0 && (
              <ul className="tax-alert">
                {context.blocking_reasons.map((r) => (
                  <li key={r}>{r}</li>
                ))}
              </ul>
            )}
            {context.accounts.length === 0 ? (
              <p>本月没有正式凭证发生科目；生成时仍会校验发票来源及账套状态。</p>
            ) : (
              <div className="tax-accounts">
                {context.accounts.map((a) => (
                  <label key={accountKey(a.account_id, a.account_version)}>
                    <span>
                      {a.code} {a.name} <small>版本 {a.account_version}</small>
                    </span>
                    <select
                      aria-label={`${a.code} ${a.name} 版本 ${a.account_version} 用途`}
                      value={roles[accountKey(a.account_id, a.account_version)] ?? ''}
                      disabled={!!busy || !context.permissions.edit}
                      onChange={(e) =>
                        setRoles((r) => ({
                          ...r,
                          [accountKey(a.account_id, a.account_version)]: e.target.value as
                            AccountRole | '',
                        }))
                      }
                    >
                      <option value="">请选择用途</option>
                      <option value="vat_payable">计入增值税账面对照金额</option>
                      <option value="excluded">不计入</option>
                    </select>
                  </label>
                ))}
              </div>
            )}
            {context.permissions.edit ? (
              <>
                <label className="tax-field">
                  生成说明
                  <textarea
                    maxLength={1000}
                    value={generationReason}
                    disabled={!!busy}
                    onChange={(e) => setGenerationReason(e.target.value)}
                  />
                </label>
                <button
                  className="button primary"
                  disabled={!!busy || !supported || !mapped || !!context.blocking_reasons.length}
                  onClick={() => void generate()}
                >
                  {busy === 'generate' ? '正在生成…' : '生成税务底稿'}
                </button>
              </>
            ) : (
              <p>当前账号仅可查看来源，生成底稿请联系本账套会计。</p>
            )}
          </section>
          <section className="tax-card" aria-label="底稿复核与导出">
            <h3>3. 独立复核后导出</h3>
            {papers.length === 0 ? (
              <p>本月还没有底稿。完成档案与科目核对后，由会计生成。</p>
            ) : (
              <>
                <label className="tax-field">
                  底稿版本
                  <select
                    value={selected?.id ?? selectedId}
                    disabled={!!busy}
                    onChange={(e) => {
                      setSelectedId(e.target.value);
                      setConfirmed(false);
                      setReviewReason('');
                    }}
                  >
                    <option value="" disabled>
                      请选择版本
                    </option>
                    {papers.map((p) => (
                      <option key={p.id} value={p.id}>
                        第 {p.version} 版 · {p.status === 'reviewed' ? '已复核' : '待复核'}
                        {p.version !== latest ? '（历史，仅供查看）' : ''}
                      </option>
                    ))}
                  </select>
                </label>
                {selected ? (
                  <TaxPaperDetails paper={selected} />
                ) : (
                  <p className="tax-alert">所选版本不在当前读取范围内，未自动切换到其他版本。</p>
                )}
                {selected?.created_by === session.user_id && selected.status === 'draft' && (
                  <p className="tax-alert">你是本版制单人，请交由另一位有复核权限的会计处理。</p>
                )}
                {selected && selected.profile_id !== context.profile?.id && (
                  <p className="tax-alert">
                    本版使用的档案与当前生效档案不一致，请由会计重新生成并独立复核。
                  </p>
                )}
                {selected && taxBlocked(blocked, selected) && (
                  <p className="tax-alert">
                    {blocked?.kind === 'source'
                      ? '本版来源或版本已变化，已暂停本版复核与导出。请由会计重新生成，再选择新版本。'
                      : '本次操作结果尚未确认，已暂停复核与导出。请重新读取并核对状态，再决定是否操作；不会自动重试。'}
                  </p>
                )}
                {actions.review && (
                  <>
                    <label className="tax-field">
                      独立复核说明
                      <textarea
                        value={reviewReason}
                        minLength={3}
                        maxLength={1000}
                        disabled={!!busy}
                        onChange={(e) => setReviewReason(e.target.value)}
                      />
                    </label>
                    <label className="tax-confirm">
                      <input
                        type="checkbox"
                        checked={confirmed}
                        disabled={!!busy}
                        onChange={(e) => setConfirmed(e.target.checked)}
                      />
                      我已核对来源、科目用途及差异，确认独立复核本版底稿
                    </label>
                    <button
                      className="button primary"
                      disabled={!!busy || !confirmed || reviewReason.trim().length < 3}
                      onClick={() => void review()}
                    >
                      独立复核底稿
                    </button>
                  </>
                )}
                <button
                  className="button secondary"
                  disabled={!!busy || !actions.export}
                  onClick={() => void download()}
                >
                  导出已复核 CSV
                </button>
                <p className="field-help">
                  仅当前、已独立复核且来源未变化的版本可导出；历史版本只能查看。独立复核与导出均不会自动申报。
                </p>
              </>
            )}
          </section>
        </>
      )}
    </section>
  );
}

function TaxProfileForm({
  context,
  busy,
  onSave,
}: {
  context: TaxContext;
  busy: boolean;
  onSave: (body: Record<string, unknown>) => Promise<void>;
}) {
  const p = context.profile;
  const [open, setOpen] = useState(!p),
    [kind, setKind] = useState(p?.taxpayer_type ?? 'small_scale'),
    [cycle, setCycle] = useState(p?.filing_cycle ?? 'monthly');
  const [vat, setVat] = useState(p ? String(p.vat_rate_bp / 100) : ''),
    [surtax, setSurtax] = useState(p ? String(p.surtax_rate_bp / 100) : '');
  const [effective, setEffective] = useState(context.scope.period),
    [reason, setReason] = useState(''),
    [error, setError] = useState('');
  return (
    <section className="tax-card" aria-label="税务档案">
      <h3>1. 核对税务档案</h3>
      {p ? (
        <p>
          {p.taxpayer_type === 'small_scale' ? '小规模纳税人' : '一般纳税人'} ·{' '}
          {p.filing_cycle === 'monthly' ? '按月' : '按季'} · 增值税率 {p.vat_rate_bp / 100}% ·
          附加税率 {p.surtax_rate_bp / 100}% · {p.effective_from} 起生效
          <br />
          适用依据：{p.reason}
        </p>
      ) : (
        <p>尚无本月适用档案。请按企业实际口径填写税率和依据，系统不预设税率。</p>
      )}
      {context.permissions.manage ? (
        <>
          <button className="text-button" disabled={busy} onClick={() => setOpen(!open)}>
            {open ? '收起档案录入' : '登记新的生效档案'}
          </button>
          {open && (
            <form
              onSubmit={(e) => {
                e.preventDefault();
                setError('');
                try {
                  const body = {
                    taxpayer_type: kind,
                    filing_cycle: cycle,
                    vat_rate_bp: taxRateBp(vat),
                    surtax_rate_bp: taxRateBp(surtax),
                    effective_from: effective,
                    reason: reason.trim(),
                  };
                  void onSave(body);
                } catch (e) {
                  setError(e instanceof Error ? e.message : '请检查税率。');
                }
              }}
            >
              <div className="tax-form-grid">
                <label>
                  纳税人类型
                  <select
                    value={kind}
                    disabled={busy}
                    onChange={(e) => setKind(e.target.value as typeof kind)}
                  >
                    <option value="small_scale">小规模纳税人</option>
                    <option value="general">一般纳税人（底稿暂不支持）</option>
                  </select>
                </label>
                <label>
                  申报周期
                  <select
                    value={cycle}
                    disabled={busy}
                    onChange={(e) => setCycle(e.target.value as typeof cycle)}
                  >
                    <option value="monthly">按月</option>
                    <option value="quarterly">按季（底稿暂不支持）</option>
                  </select>
                </label>
                <label>
                  增值税率（%）
                  <input
                    inputMode="decimal"
                    required
                    value={vat}
                    disabled={busy}
                    onChange={(e) => setVat(e.target.value)}
                  />
                </label>
                <label>
                  附加税率（%）
                  <input
                    inputMode="decimal"
                    required
                    value={surtax}
                    disabled={busy}
                    onChange={(e) => setSurtax(e.target.value)}
                  />
                </label>
                <label>
                  生效月份
                  <input
                    type="month"
                    required
                    value={effective}
                    disabled={busy}
                    onChange={(e) => setEffective(e.target.value)}
                  />
                </label>
              </div>
              <label className="tax-field">
                适用依据
                <textarea
                  required
                  minLength={3}
                  maxLength={1000}
                  value={reason}
                  disabled={busy}
                  onChange={(e) => setReason(e.target.value)}
                />
              </label>
              {p && (
                <p className="field-help">
                  已保存档案不可覆盖。同一生效月不能重复登记，后续变更请填写新的生效月份。
                </p>
              )}
              {error && (
                <p role="alert" className="tax-alert">
                  {error}
                </p>
              )}
              <button className="button secondary" disabled={busy || reason.trim().length < 3}>
                保存税务档案
              </button>
            </form>
          )}
        </>
      ) : (
        <p className="field-help">档案由企业或事务所管理员维护，当前账号不能修改。</p>
      )}
    </section>
  );
}
function TaxPaperDetails({ paper: p }: { paper: TaxWorkpaper }) {
  const evidence = taxEvidence(p);
  const amounts = [
    ['销项不含税金额', p.output_net],
    ['销项票面税额', p.output_tax],
    ['进项不含税金额', p.input_net],
    ['进项票面税额（不代表抵扣）', p.input_tax],
    ['测算增值税', p.tax],
    ['测算附加税', p.surtax],
    ['正式账对应金额', p.ledger],
    ['账表差异', p.variance],
  ];
  return (
    <>
      <div className="tax-totals">
        {amounts.map(([label, value]) => (
          <div key={label}>
            <span>{label}</span>
            <strong>{taxMoney(value)}</strong>
          </div>
        ))}
      </div>
      <p>
        制单人：{p.created_by} · {p.created_at}
        {p.reviewed_by ? ` · 复核人：${p.reviewed_by}` : ''}
      </p>
      {p.variance !== '0.00' && (
        <p className="tax-alert">账表存在差异，请逐项核对并在独立复核说明中记录处理依据。</p>
      )}
      {p.review_reason && <p>复核意见：{p.review_reason}</p>}
      <section aria-label="本版冻结科目用途">
        <h4>本版生成时的科目用途</h4>
        <p className="field-help">
          用于正式账的对照金额；测算税额由发票和税务档案决定。以下冻结选择不会随上方新底稿选项改变。
        </p>
        {evidence.accounts.length ? (
          <ul className="tax-frozen-accounts">
            {evidence.accounts.map((a) => (
              <li key={accountKey(a.account_id, a.account_version)}>
                <span>
                  {a.code} {a.name} · 版本 {a.account_version}
                </span>
                <strong>{a.label}</strong>
              </li>
            ))}
          </ul>
        ) : (
          <p>本版没有正式凭证发生科目，账面对照金额为零，请核对差异。</p>
        )}
      </section>
      <details>
        <summary>查看本版来源证据（{p.lines.length} 行发票）</summary>
        <p>
          来源摘要：<code className="tax-digest">{p.source_digest}</code>
        </p>
        <div className="tax-evidence">
          {evidence.invoices.map((invoice) => (
            <dl key={JSON.stringify([invoice.batch_id, invoice.row_index])}>
              <div>
                <dt>发票号码</dt>
                <dd>{invoice.number}</dd>
              </div>
              <div>
                <dt>开票日期</dt>
                <dd>{invoice.issued_on}</dd>
              </div>
              <div>
                <dt>方向</dt>
                <dd>{invoice.direction === 'output' ? '销项' : '进项'}</dd>
              </div>
              <div>
                <dt>交易对方</dt>
                <dd>{invoice.counterparty}</dd>
              </div>
              <div>
                <dt>不含税金额（元）</dt>
                <dd>{taxMoney(invoice.amount)}</dd>
              </div>
              <div>
                <dt>票面税额（元）</dt>
                <dd>{taxMoney(invoice.tax)}</dd>
              </div>
              <div>
                <dt>价税合计（元）</dt>
                <dd>{taxMoney(invoice.total)}</dd>
              </div>
              <div>
                <dt>导入批次 / 行</dt>
                <dd>
                  {invoice.batch_id} / {invoice.row_index}
                </dd>
              </div>
              <div>
                <dt>原件摘要</dt>
                <dd>{invoice.original_sha256}</dd>
              </div>
              <div>
                <dt>来源行摘要</dt>
                <dd>{invoice.row_fingerprint}</dd>
              </div>
            </dl>
          ))}
        </div>
        <p className="field-help">
          这是生成时冻结的证据；复核和导出时会重新检查来源，读取本页不代表来源仍有效。
        </p>
      </details>
    </>
  );
}
