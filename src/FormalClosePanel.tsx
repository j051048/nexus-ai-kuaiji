import { useCallback, useEffect, useRef, useState } from 'react';
import { api, apiBlob, errorMessage, ApiError } from './api';
import type { Session } from './types';
import { formatFormalCents } from './formal-books-types';
import {
  canIndependentlyReview,
  parseCloseRows,
  parseCoverageRows,
  parseReconciliation,
  parseReportRows,
  type CloseRow,
  type CoverageRow,
  type ReportRow,
} from './formal-close-types';
import './formal-close.css';

interface Props {
  session: Session;
  bookId: string;
  period: string;
  canReview: boolean;
  accountLabels?: Record<string, string>;
}
interface Data {
  reports: ReportRow[];
  closes: CloseRow[];
  coverages: CoverageRow[];
  bank: ReturnType<typeof parseReconciliation>;
  control: ReturnType<typeof parseReconciliation>;
}
const labels: Record<string, string> = {
  draft: '待核验',
  validated: '待独立复核',
  reviewed: '已复核',
  published: '已发布',
  locked: '已锁期',
  preflight_failed: '预检有阻断',
  preflight_passed: '预检通过，待独立复核',
  pending_review: '待独立核验',
  verified: '已核验',
  rejected: '已退回',
  legacy_unverified: '历史证据待补齐',
};
const checkLabels: Record<string, string> = {
  unposted_periodic: '期末结转',
  unapproved_drafts: '凭证复核',
  unposted_manual: '手工凭证',
  subledger_control: '子账与控制科目',
  bank_reconciliation: '银行对账',
  trial_balance: '借贷平衡',
  report_checks: '报表勾稽',
};
function summary(value: Record<string, unknown>): string {
  if (typeof value.reason === 'string' && value.reason) return value.reason;
  return value.passed === true ? '当前证据已对平。' : '请核对下方账户、科目明细及未完成证据。';
}
function EvidenceRows({
  value,
  accountLabels = {},
}: {
  value: Record<string, unknown>;
  accountLabels?: Record<string, string>;
}) {
  const rows = Array.isArray(value.rows)
    ? value.rows
    : Array.isArray(value.accounts)
      ? value.accounts
      : [];
  return (
    <>
      {rows.length > 0 && (
        <ul className="formal-close-evidence">
          {rows.map((raw, i) => {
            if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return null;
            const row = raw as Record<string, unknown>;
            const title = String(
              row.name ??
                row.account_name ??
                accountLabels[String(row.control_account_id ?? row.ledger_account_id ?? '')] ??
                row.control_account_id ??
                row.account_id ??
                `核对项 ${i + 1}`,
            );
            return (
              <li key={i}>
                <strong>{title}</strong> · {row.passed === true ? '已对平' : '需处理'}
                {typeof row.difference_cents === 'number' &&
                  Number.isSafeInteger(row.difference_cents) && (
                    <span>
                      {' '}
                      · {row.account_id ? '流水差额' : '子账差额'}{' '}
                      {formatFormalCents(row.difference_cents)}
                    </span>
                  )}
                {(['book_difference_cents', 'opening_book_difference_cents'] as const).map((key) =>
                  typeof row[key] === 'number' && Number.isSafeInteger(row[key]) ? (
                    <span key={key}>
                      {' '}
                      · {key === 'book_difference_cents' ? '账面期末差额' : '期初账面差额'}{' '}
                      {formatFormalCents(row[key])}
                    </span>
                  ) : null,
                )}
                {typeof row.unmatched_count === 'number' &&
                  Number.isSafeInteger(row.unmatched_count) && (
                    <p>未匹配流水：{row.unmatched_count} 条</p>
                  )}
                {typeof row.coverage_state === 'string' && (
                  <p>原件覆盖：{labels[row.coverage_state] ?? '缺少有效覆盖'}</p>
                )}
                {typeof row.reason === 'string' && <p>{row.reason}</p>}
                {row.aux !== null && typeof row.aux === 'object' && (
                  <p>
                    辅助项目：
                    {Object.values(row.aux)
                      .filter((x) => typeof x === 'string')
                      .join(' / ') || '无'}
                  </p>
                )}
              </li>
            );
          })}
        </ul>
      )}
      {value.continuity &&
      typeof value.continuity === 'object' &&
      (value.continuity as Record<string, unknown>).passed === false ? (
        <p>跨期衔接尚未通过，请会计负责人核对上期与本期的账户、期初及桥接证据。</p>
      ) : null}
    </>
  );
}
export const CloseEvidenceRows = EvidenceRows;
export function FrozenCloseReport({ report }: { report: ReportRow }) {
  const content = report.content;
  const groups = [
    {
      title: '资产负债表',
      check: 'balance_sheet_passed',
      totals: [
        ['资产合计', content.balance_sheet.assets],
        ['负债合计', content.balance_sheet.liabilities],
        ['权益合计', content.balance_sheet.equity],
        ['未结转利润', content.balance_sheet.unclosed_profit],
      ] as const,
      items: content.balance_sheet.items,
    },
    {
      title: '利润表',
      check: 'profit_statement_passed',
      totals: [
        ['收入合计', content.profit_statement.revenue_cents],
        ['费用合计', content.profit_statement.expense_cents],
        ['利润', content.profit_statement.profit_cents],
      ] as const,
      items: content.profit_statement.items,
    },
    {
      title: '现金流量表',
      check: 'cash_flow_passed',
      totals: [
        ['期初现金', content.cash_flow.opening_cash_cents],
        ['期末现金', content.cash_flow.closing_cash_cents],
        ['净变动', content.cash_flow.net_change_cents],
        ['经营活动', content.cash_flow.operating_cents],
        ['投资活动', content.cash_flow.investing_cents],
        ['筹资活动', content.cash_flow.financing_cents],
        ['未分类', content.cash_flow.unclassified_cents],
      ] as const,
      items: [],
    },
  ];
  return (
    <details className="formal-close-report" open>
      <summary>报表 V{report.version} · 冻结三表与勾稽依据</summary>
      <p>下列数值直接读取该版本冻结内容；后台复核时还会校验当前来源和内容指纹。</p>
      {groups.map((group) => (
        <section key={group.title} aria-label={group.title}>
          <h4>
            {group.title} · {content.checks[group.check] ? '勾稽通过' : '勾稽未通过'}
          </h4>
          <dl>
            {group.totals.map(([name, cents]) => (
              <div key={name}>
                <dt>{name}</dt>
                <dd>{formatFormalCents(cents)}</dd>
              </div>
            ))}
          </dl>
          {group.items.length > 0 && (
            <ul>
              {group.items.map((item) => (
                <li key={item.account_id}>
                  {item.code} {item.name}：{formatFormalCents(item.amount_cents)}
                </li>
              ))}
            </ul>
          )}
        </section>
      ))}
      <details>
        <summary>本版内容指纹</summary>
        <p>{report.content_hash}</p>
      </details>
    </details>
  );
}
export function FormalClosePanel(props: Props) {
  return (
    <CloseContent
      key={[props.session.scope_key, props.session.user_id, props.bookId, props.period].join(':')}
      {...props}
    />
  );
}
function CloseContent({ session, bookId, period, canReview, accountLabels }: Props) {
  const [data, setData] = useState<Data | null>(null);
  const [selectedReport, setSelectedReport] = useState('');
  const [reason, setReason] = useState('');
  const [loading, setLoading] = useState(true),
    [busy, setBusy] = useState(false);
  const [error, setError] = useState(''),
    [notice, setNotice] = useState('');
  const [uncertain, setUncertain] = useState(false);
  const live = useRef(true),
    busyRef = useRef(false),
    epoch = useRef(0);
  useEffect(() => {
    live.current = true;
    return () => {
      live.current = false;
      epoch.current++;
    };
  }, []);
  const load = useCallback(
    async (signal?: AbortSignal) => {
      const request = ++epoch.current;
      setLoading(true);
      setData(null);
      setError('');
      try {
        const query = `?book_id=${encodeURIComponent(bookId)}`;
        const [reports, closes, coverages, bank, control] = await Promise.all([
          api<unknown>(`/reports${query}`, session, { signal }),
          api<unknown>(`/b04-close${query}`, session, { signal }),
          api<unknown>(`/subledger/bank-coverages${query}`, session, { signal }),
          api<unknown>(`/subledger/bank-reconciliation${query}`, session, { signal }),
          api<unknown>(`/subledger/control-reconciliation${query}`, session, { signal }),
        ]);
        const scope = { bookId, period };
        const value = {
          reports: parseReportRows(reports, scope),
          closes: parseCloseRows(closes, scope),
          coverages: parseCoverageRows(coverages, scope),
          bank: parseReconciliation(bank),
          control: parseReconciliation(control),
        };
        if (live.current && request === epoch.current && !signal?.aborted) {
          setData(value);
          setUncertain(false);
        }
      } catch (failure) {
        if (live.current && request === epoch.current && !signal?.aborted) {
          setData(null);
          setError(`${errorMessage(failure)} 请重新读取；权限问题联系本账套管理员。`);
        }
      } finally {
        if (live.current && request === epoch.current && !signal?.aborted) setLoading(false);
      }
    },
    [bookId, period, session],
  );
  useEffect(() => {
    const controller = new AbortController();
    void load(controller.signal);
    return () => controller.abort();
  }, [load]);
  useEffect(() => {
    const revoke = () => {
      epoch.current++;
      setLoading(false);
      setData(null);
      setSelectedReport('');
      setReason('');
      setNotice('');
      setError('授权已变化，请联系本账套管理员确认后重新读取。');
    };
    window.addEventListener('accounting:forbidden', revoke);
    return () => window.removeEventListener('accounting:forbidden', revoke);
  }, []);
  const report = data?.reports.find((item) => item.id === selectedReport);
  const close = data?.closes
    .filter((item) => item.report_run_id === selectedReport)
    .sort((a, b) => b.version - a.version)[0];
  const locked = data?.closes.some((item) => ['locked', 'published'].includes(item.status));
  const disabled = busy || loading || !data || !canReview || uncertain;
  const needsReason = disabled || reason.trim().length < 3;
  async function write(
    path: string,
    body: unknown,
    kind: 'report' | 'close' | 'coverage',
    success: string,
  ) {
    if (busyRef.current || disabled) return;
    busyRef.current = true;
    const request = epoch.current;
    setBusy(true);
    setError('');
    setNotice('');
    try {
      const raw = await api<unknown>(path, session, { method: 'POST', body });
      const scope = { bookId, period };
      if (!raw || typeof raw !== 'object') throw new Error('返回数据不完整，请重新读取确认结果。');
      const object = raw as Record<string, unknown>;
      const item =
        kind === 'report'
          ? parseReportRows({ reports: [object.report] }, scope)[0]
          : kind === 'close'
            ? parseCloseRows({ runs: [object.close] }, scope)[0]
            : parseCoverageRows({ coverages: [raw] }, scope)[0];
      if (!live.current || request !== epoch.current) return;
      if (kind === 'report') setSelectedReport(item.id);
      setNotice(
        kind === 'close' && 'status' in item && item.status === 'preflight_failed'
          ? '预检已保存，但仍有阻断；请处理检查项后重新生成预检，不可锁期。'
          : success,
      );
      await load();
    } catch (failure) {
      if (live.current && request === epoch.current) {
        setData(null);
        setUncertain(!(failure instanceof ApiError) || failure.status >= 500);
        setError(
          `${errorMessage(failure)} 请先重新读取当前版本与结果，再由你决定下一步；不会自动重试或复核。`,
        );
      }
    } finally {
      busyRef.current = false;
      if (live.current) setBusy(false);
    }
  }
  async function downloadOriginal(coverage: CoverageRow) {
    if (busyRef.current || !data) return;
    const request = epoch.current;
    busyRef.current = true;
    setBusy(true);
    setError('');
    try {
      const blob = await apiBlob(
        `/books/${encodeURIComponent(bookId)}/import-wizard/files/${encodeURIComponent(coverage.source_file_id)}`,
        session,
      );
      const hash = Array.from(
        new Uint8Array(await crypto.subtle.digest('SHA-256', await blob.arrayBuffer())),
        (byte) => byte.toString(16).padStart(2, '0'),
      ).join('');
      if (!live.current || request !== epoch.current) return;
      if (hash !== coverage.source_sha256)
        throw new Error('原件内容与覆盖证据指纹不一致，未提供下载。');
      const url = URL.createObjectURL(blob);
      try {
        const link = document.createElement('a');
        link.href = url;
        link.download = `银行原件-${coverage.source_file_id}`;
        document.body.append(link);
        link.click();
        link.remove();
      } finally {
        URL.revokeObjectURL(url);
      }
      setNotice('已下载并校验银行原件，请与覆盖金额核对。');
    } catch (failure) {
      if (live.current && request === epoch.current)
        setError(`${errorMessage(failure)} 未完成原件核对前请勿核验覆盖。`);
    } finally {
      busyRef.current = false;
      if (live.current) setBusy(false);
    }
  }
  const command = (version: number) => ({ expected_version: version, reason: reason.trim() });
  return (
    <section className="formal-close-panel" aria-label="正式月结与报表发布">
      <header>
        <div>
          <h2>正式月结与报表发布</h2>
          <p>{period} · 当前账套。银行、子账先对平，报表与预检分别独立复核，再锁期和发布。</p>
        </div>
        <button className="button secondary" disabled={busy || loading} onClick={() => void load()}>
          重新读取月结进度
        </button>
      </header>
      {!canReview && <p>你可查看进度；核验、复核与发布需本账套复核权限，请联系会计负责人。</p>}
      {loading && <p role="status">正在读取月结证据…</p>}
      {error && (
        <p role="alert" className="inline-error">
          {error}
        </p>
      )}
      {notice && <p role="status">{notice}</p>}
      {data && (
        <>
          <div className="formal-close-grid">
            <article aria-label="银行对账检查">
              <h3>1. 银行对账 · {data.bank.passed ? '已对平' : '需处理'}</h3>
              <p>{summary(data.bank)}</p>
              <EvidenceRows value={data.bank} accountLabels={accountLabels} />
              <p>由获授权会计核对账户、已导入流水与原件，再由另一位复核员核验覆盖。</p>
              {data.coverages.map((coverage) => (
                <div className="formal-close-coverage" key={coverage.id}>
                  <strong>
                    银行覆盖 {coverage.account_id} · {labels[coverage.state]}
                  </strong>
                  <p>
                    期初 {formatFormalCents(coverage.opening_cents)} → 期末{' '}
                    {formatFormalCents(coverage.closing_cents)} · V{coverage.version}
                  </p>
                  <details>
                    <summary>原件证据定位</summary>
                    <p>原件：{coverage.source_file_id || '待补齐'}</p>
                    <p>导入批次：{coverage.source_preview_id || '待补齐'}</p>
                    <p>SHA-256：{coverage.source_sha256 || '待补齐，不能核验或下载'}</p>
                    <button
                      className="button secondary"
                      disabled={
                        busy ||
                        loading ||
                        coverage.state === 'legacy_unverified' ||
                        !coverage.source_file_id ||
                        !coverage.source_sha256
                      }
                      onClick={() => void downloadOriginal(coverage)}
                    >
                      下载银行原件核对
                    </button>
                  </details>
                  {coverage.state === 'pending_review' && (
                    <>
                      <p>
                        {coverage.created_by === session.user_id
                          ? '你是本次声明人，请交给另一位获授权复核员核验。'
                          : '核对原件、账户及期初期末金额后，填写下方办理说明再确认。'}
                      </p>
                      <button
                        className="button secondary"
                        disabled={
                          needsReason ||
                          !!locked ||
                          !canIndependentlyReview(coverage, session.user_id, canReview)
                        }
                        onClick={() =>
                          void write(
                            `/subledger/bank-coverages/${encodeURIComponent(coverage.id)}/review`,
                            { ...command(coverage.version), decision: 'verified' },
                            'coverage',
                            '银行覆盖已独立核验。',
                          )
                        }
                      >
                        核验银行覆盖
                      </button>
                    </>
                  )}
                </div>
              ))}
            </article>
            <article aria-label="子账控制检查">
              <h3>2. 子账控制 · {data.control.passed ? '已对平' : '需处理'}</h3>
              <p>{summary(data.control)}</p>
              <EvidenceRows value={data.control} accountLabels={accountLabels} />
              {!data.control.passed && (
                <p>
                  下一步：由会计负责人确认控制科目及辅助项目配置，补齐未结事项或收付核销后重新读取。没有业务不能用空配置跳过。
                </p>
              )}
            </article>
          </div>
          <label className="formal-close-reason">
            办理说明（至少 3 字）
            <input
              value={reason}
              disabled={busy || !canReview}
              maxLength={1000}
              onChange={(event) => setReason(event.target.value)}
              placeholder="说明已核对的原件、金额与本次处理依据"
            />
          </label>
          <article>
            <h3>3. 报表独立复核</h3>
            <label>
              本次报表版本
              <select
                value={selectedReport}
                disabled={busy}
                onChange={(event) => {
                  setSelectedReport(event.target.value);
                  setNotice('');
                }}
              >
                <option value="">请选择要办理的报表版本</option>
                {data.reports.map((item) => (
                  <option key={item.id} value={item.id}>
                    报表 V{item.version} · {labels[item.status]}
                  </option>
                ))}
              </select>
            </label>
            {report && <FrozenCloseReport report={report} />}
            <div className="formal-close-actions">
              <button
                className="button secondary"
                disabled={disabled || !!locked}
                onClick={() =>
                  void write(
                    '/reports',
                    { book_id: bookId, period },
                    'report',
                    '报表已生成，尚需核验与独立复核。',
                  )
                }
              >
                生成正式报表
              </button>
              {report?.status === 'draft' && (
                <button
                  className="button secondary"
                  disabled={disabled}
                  onClick={() =>
                    void write(
                      `/reports/${report.id}/validate`,
                      undefined,
                      'report',
                      '报表勾稽核验通过，待另一位复核员复核。',
                    )
                  }
                >
                  核验报表勾稽
                </button>
              )}
              {report?.status === 'validated' && (
                <button
                  className="button secondary"
                  disabled={
                    needsReason || !canIndependentlyReview(report, session.user_id, canReview)
                  }
                  onClick={() =>
                    void write(
                      `/reports/${report.id}/review`,
                      command(report.version),
                      'report',
                      '报表已独立复核，可生成月结预检。',
                    )
                  }
                >
                  独立复核报表
                </button>
              )}
            </div>
            {report?.status === 'validated' && report.created_by === session.user_id && (
              <p>你是报表编制人，请另一位获授权复核员办理独立复核。</p>
            )}
          </article>
          <article>
            <h3>4. 预检、锁期与发布</h3>
            <p>处理过证据、配置或草稿后须重新预检；预检返回成功并不代表已经通过。</p>
            <button
              className="button secondary"
              disabled={
                needsReason ||
                !!locked ||
                !report ||
                !['reviewed', 'published'].includes(report.status)
              }
              onClick={() =>
                report &&
                void write(
                  '/b04-close/preflight',
                  {
                    book_id: bookId,
                    period,
                    report_id: report.id,
                    idempotency_key: crypto.randomUUID(),
                    reason: reason.trim(),
                  },
                  'close',
                  '预检已通过，请另一位复核员确认当前版本。',
                )
              }
            >
              生成月结预检
            </button>
            {close && (
              <>
                <p role="status">
                  月结 V{close.version} · {labels[close.status]}
                </p>
                <ul className="formal-close-checks">
                  {close.checks.map((check) => (
                    <li key={check.code}>
                      <strong>
                        {checkLabels[check.code] ?? '月结检查'}：
                        {check.status === 'pass' ? '通过' : '需处理'}
                      </strong>
                      {check.status === 'blocked' && (
                        <>
                          <p>{summary(check.detail)}</p>
                          <p>
                            处理岗位：{check.owner_role ?? '获授权会计'} · 下一步：
                            {check.next_action ?? '核对对应原件及账务记录，完成后重新预检。'}
                          </p>
                          <EvidenceRows value={check.detail} accountLabels={accountLabels} />
                        </>
                      )}
                    </li>
                  ))}
                </ul>
                <div className="formal-close-actions">
                  {close.status === 'preflight_passed' && (
                    <button
                      className="button"
                      disabled={
                        needsReason || !canIndependentlyReview(close, session.user_id, canReview)
                      }
                      onClick={() =>
                        void write(
                          `/b04-close/${close.id}/review`,
                          command(close.version),
                          'close',
                          '月结预检已独立复核，可以确认锁期。',
                        )
                      }
                    >
                      独立复核月结预检
                    </button>
                  )}
                  {close.status === 'reviewed' && (
                    <button
                      className="button"
                      disabled={needsReason}
                      onClick={() => {
                        if (
                          window.confirm(
                            '确认锁定本账套本月份？锁期后不能直接更改正式账务，本次不支持重开。',
                          )
                        )
                          void write(
                            `/b04-close/${close.id}/lock`,
                            command(close.version),
                            'close',
                            '本月已正式锁期，可以发布报表。',
                          );
                      }}
                    >
                      确认正式锁期
                    </button>
                  )}
                  {close.status === 'locked' && (
                    <button
                      className="button"
                      disabled={needsReason}
                      onClick={() =>
                        void write(
                          `/b04-close/${close.id}/publish`,
                          command(close.version),
                          'close',
                          '正式报表已发布；请在月度服务单或交付页面生成资料包并请客户签收。',
                        )
                      }
                    >
                      发布正式报表
                    </button>
                  )}
                </div>
                {close.status === 'preflight_passed' && close.created_by === session.user_id && (
                  <p>你是本次预检生成人，请另一位获授权复核员确认。</p>
                )}
              </>
            )}
          </article>
        </>
      )}
    </section>
  );
}
