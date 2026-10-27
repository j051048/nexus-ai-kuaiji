import { useCallback, useEffect, useRef, useState } from 'react';
import { Download, FileArchive, RefreshCw } from 'lucide-react';
import { api, apiBlob, ApiError, errorMessage } from './api';
import type { Session } from './types';
import {
  canAcknowledgePackage,
  formatBytes,
  packageOpeningLabel,
  packageReceiptLabel,
  validatePackage,
  validateMonthlyOverview,
  type MonthlyPackage,
  type MonthlyPackageFile,
  type MonthlyPackageOverview,
} from './monthly-package-types';
import {
  acceptPackage,
  packageFromSearch,
  readRecovery,
  selectPackage,
} from './monthly-package-read';
import './monthly-package.css';

type MonthlyPackagePanelProps = {
  session: Session;
  bookId: string;
  onBusyChange?: (value: boolean) => void;
  clientOnly?: boolean;
  focusPackageId?: string;
};
export function MonthlyPackagePanel(props: MonthlyPackagePanelProps) {
  return (
    <MonthlyPackageContent
      key={JSON.stringify([
        props.session.scope_key,
        props.session.user_id,
        props.session.firm_id,
        props.bookId,
      ])}
      {...props}
    />
  );
}
function MonthlyPackageContent({
  session,
  bookId,
  onBusyChange,
  clientOnly = false,
  focusPackageId = '',
}: MonthlyPackagePanelProps) {
  const scope = `${session.scope_key}:${bookId}`;
  const [state, setState] = useState<{ scope: string; value: MonthlyPackageOverview } | null>(null);
  const [error, setError] = useState('');
  const [trackError, setTrackError] = useState('');
  const [message, setMessage] = useState('');
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState('');
  const [reportId, setReportId] = useState('');
  const [reason, setReason] = useState('');
  const [confirmed, setConfirmed] = useState(false);
  const [contents, setContents] = useState<{ id: string; files: MonthlyPackageFile[] } | null>(
    null,
  );
  const opened = useRef('');
  const current = useRef(scope);
  current.current = scope;
  const epoch = useRef(0),
    locked = useRef(false);
  const requestKey = useRef({ fingerprint: '', key: '' });
  const value = state?.scope === scope ? state.value : null;
  const base = `/books/${encodeURIComponent(bookId)}/delivery/monthly-package`;
  const track = useCallback(
    async (item: MonthlyPackage, generation: number) => {
      // Seeing the package is what the firm needs to learn; neither the read receipt nor
      // the file list may block viewing or signing.
      try {
        const updated =
          item.can_open && item.customer_opening_status === 'not_opened'
            ? await api<MonthlyPackage>(`${base}/${encodeURIComponent(item.id)}/open`, session, {
                method: 'POST',
              })
            : (
                await api<{ package: MonthlyPackage }>(
                  `${base}/${encodeURIComponent(item.id)}`,
                  session,
                )
              ).package;
        if (current.current !== scope || generation !== epoch.current) return;
        // Accept the response before touching the screen: a mismatched identity must
        // leave both the file list and the package state exactly as they were.
        const accepted = acceptPackage(updated, {
          id: item.id,
          bookId,
          companyId: item.company_id,
          period: item.period,
        });
        if (!accepted)
          throw new Error('交付包响应与请求的版本不一致，已停止使用；请重新读取当前账套。');
        opened.current = accepted.id;
        setContents(accepted.files?.length ? { id: accepted.id, files: accepted.files } : null);
        setState((prior) =>
          prior && prior.scope === scope
            ? {
                scope,
                value: {
                  ...prior.value,
                  packages: prior.value.packages.map((pack) =>
                    pack.id === accepted.id ? accepted : pack,
                  ),
                },
              }
            : prior,
        );
        setTrackError('');
      } catch (failure) {
        if (current.current !== scope || generation !== epoch.current) return;
        setTrackError(
          readRecovery(failure instanceof ApiError ? failure.status : undefined).message,
        );
      }
    },
    [base, bookId, scope, session],
  );
  const load = useCallback(
    async (signal?: AbortSignal) => {
      const generation = ++epoch.current;
      setLoading(true);
      try {
        const result = await api<MonthlyPackageOverview>(base, session, { signal });
        if (signal?.aborted || current.current !== scope || generation !== epoch.current) return;
        if (!validateMonthlyOverview(result, bookId))
          throw new Error('交付包范围或版本无效，请重新读取当前账套。');
        setState({ scope, value: result });
        setError('');
        if (clientOnly) {
          const wanted = focusPackageId || packageFromSearch(window.location.search);
          if (wanted) {
            const selection = selectPackage(result.packages, wanted);
            if (selection.kind === 'missing') setError(selection.message);
            else {
              if (selection.message) setError(selection.message);
              if (selection.item && opened.current !== selection.item.id)
                void track(selection.item, generation);
            }
          } else {
            const newest = result.packages.find((item) => !item.superseded);
            if (newest && opened.current !== newest.id) void track(newest, generation);
          }
        }
        setReportId((prior) =>
          result.reports.some((item) => item.id === prior && item.current)
            ? prior
            : result.reports.filter((item) => item.current).length === 1
              ? result.reports.find((item) => item.current)!.id
              : '',
        );
        return true;
      } catch (failure) {
        if (!signal?.aborted && current.current === scope && generation === epoch.current) {
          setState(null);
          setError(errorMessage(failure));
        }
      } finally {
        if (current.current === scope && generation === epoch.current) setLoading(false);
      }
    },
    [base, bookId, clientOnly, focusPackageId, scope, session, track],
  );
  useEffect(() => {
    current.current = scope;
    const controller = new AbortController();
    setState(null);
    setReason('');
    setConfirmed(false);
    setReportId('');
    setMessage('');
    setError('');
    setTrackError('');
    requestKey.current = { fingerprint: '', key: '' };
    opened.current = '';
    setContents(null);
    void load(controller.signal);
    return () => {
      controller.abort();
      epoch.current++;
      if (current.current === scope) current.current = '';
    };
  }, [load]);
  useEffect(() => {
    onBusyChange?.(!!busy);
    return () => onBusyChange?.(false);
  }, [busy, onBusyChange]);
  useEffect(() => {
    function revoked(event: Event) {
      if ((event as CustomEvent<string>).detail !== session.scope_key) return;
      epoch.current++;
      setState(null);
      setReason('');
      setConfirmed(false);
      setLoading(false);
      setError('当前授权已变更，请重新检查交付条件。');
    }
    window.addEventListener('accounting:forbidden', revoked);
    return () => window.removeEventListener('accounting:forbidden', revoked);
  }, [session.scope_key]);
  async function run(name: string, action: () => Promise<void>) {
    if (locked.current) return;
    locked.current = true;
    setBusy(name);
    setError('');
    setMessage('');
    try {
      await action();
    } catch (failure) {
      if (current.current === scope) {
        setError(errorMessage(failure));
        setState(null);
      }
    } finally {
      locked.current = false;
      if (current.current === scope) setBusy('');
    }
  }
  const selected = value?.reports.find((item) => item.id === reportId && item.current);
  async function create() {
    if (
      !value?.ready ||
      !value.permissions.generate ||
      !selected ||
      reason.trim().length < 3 ||
      !confirmed
    )
      return;
    const payload = {
      report_id: selected.id,
      expected_report_version: selected.version,
      reason: reason.trim(),
    };
    const fingerprint = JSON.stringify(payload);
    if (requestKey.current.fingerprint !== fingerprint)
      requestKey.current = { fingerprint, key: crypto.randomUUID() };
    await run('create', async () => {
      const generation = epoch.current;
      const item = await api<MonthlyPackage>(base, session, {
        method: 'POST',
        body: { ...payload, idempotency_key: requestKey.current.key },
      });
      if (current.current !== scope || generation !== epoch.current) return;
      if (!validatePackage(item, { bookId, companyId: value.company_id, period: value.period }))
        throw new Error('生成结果与当前企业月份不一致，请重新读取。');
      requestKey.current = { fingerprint: '', key: '' };
      setConfirmed(false);
      setReason('');
      const refreshed = await load();
      if (current.current === scope && refreshed)
        setMessage(`已冻结月度交付包 V${item.version}，请下载并交付客户。`);
    });
  }
  async function download(item: MonthlyPackage) {
    await run(item.id, async () => {
      const generation = epoch.current;
      const blob = await apiBlob(`${base}/${encodeURIComponent(item.id)}/download`, session);
      if (current.current !== scope || generation !== epoch.current) return;
      const url = URL.createObjectURL(blob),
        anchor = document.createElement('a');
      anchor.href = url;
      anchor.download = `月度交付-${item.period}-V${item.version}.zip`;
      anchor.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    });
  }
  async function acknowledge(item: MonthlyPackage) {
    if (!canAcknowledgePackage(item, session.user_id) || reason.trim().length < 3 || !confirmed)
      return;
    await run(item.id, async () => {
      const generation = epoch.current;
      const result = await api<MonthlyPackage>(
        `${base}/${encodeURIComponent(item.id)}/acknowledge`,
        session,
        { method: 'POST', body: { manifest_sha256: item.manifest_sha256, reason: reason.trim() } },
      );
      if (current.current !== scope || generation !== epoch.current) return;
      if (!validatePackage(result, { bookId, companyId: item.company_id, period: item.period }))
        throw new Error('签收结果范围不一致，请重新读取。');
      setReason('');
      setConfirmed(false);
      const refreshed = await load();
      if (current.current === scope && refreshed) setMessage('本版本客户签收已留痕。');
    });
  }
  return (
    <section className="panel monthly-package-panel" aria-label="月度交付包">
      <header className="panel-heading">
        <div>
          <h2>
            <FileArchive size={19} aria-hidden="true" />
            月度交付包
          </h2>
          <p>正式账簿、已发布报表与核验记录按版本冻结；原件提供索引，客户签收单独记录。</p>
        </div>
        <button
          className="button secondary small"
          type="button"
          disabled={!!busy || loading}
          onClick={() => void load()}
        >
          <RefreshCw size={14} />
          重新检查交付条件
        </button>
      </header>
      {loading && <p role="status">正在检查本月交付条件…</p>}
      {error && (
        <div role="alert" className="inline-error">
          {error} 请重新检查，或联系本企业获授权会计。
        </div>
      )}
      {trackError && (
        <div role="alert" className="inline-error">
          {trackError}{' '}
          <button
            type="button"
            className="button secondary small"
            disabled={!!busy}
            onClick={() => {
              const wanted = focusPackageId || packageFromSearch(window.location.search);
              const selection = value ? selectPackage(value.packages, wanted) : null;
              if (selection?.item) void track(selection.item, epoch.current);
            }}
          >
            重试读取
          </button>
        </div>
      )}
      {message && (
        <p role="status" className="inline-success">
          {message}
        </p>
      )}
      {value && (
        <>
          <p className="monthly-package-scope">月份 {value.period} · 当前获授权企业</p>
          {!clientOnly && value.blockers.length > 0 && (
            <div className="monthly-package-blockers">
              <strong>交付前还需处理</strong>
              <ul>
                {value.blockers.map((item, i) => (
                  <li key={`${item.code}:${i}`}>
                    {item.message}
                    {item.owner && (
                      <span className="monthly-package-owner">
                        {' '}
                        处理人：{item.owner} · 入口：{item.entry ?? '—'}
                      </span>
                    )}
                  </li>
                ))}
              </ul>
            </div>
          )}
          {!clientOnly && value.warnings.length > 0 && (
            <div className="monthly-package-warnings">
              <strong>建议在交付前确认</strong>
              <ul>
                {value.warnings.map((item, i) => (
                  <li key={`${item.code}:${i}`}>{item.message}</li>
                ))}
              </ul>
            </div>
          )}
          {!clientOnly && value.permissions.generate && (
            <form
              className="monthly-package-form"
              onSubmit={(event) => {
                event.preventDefault();
                void create();
              }}
            >
              <label>
                交付报表版本
                <select
                  value={reportId}
                  disabled={!!busy}
                  onChange={(event) => {
                    setReportId(event.target.value);
                    setConfirmed(false);
                  }}
                >
                  <option value="">请选择已发布且仍有效的报表</option>
                  {value.reports.map((item) => (
                    <option value={item.id} disabled={!item.current} key={item.id}>
                      报表 V{item.version}
                      {item.current ? ' · 当前有效' : ' · 需重新发布'}
                    </option>
                  ))}
                </select>
              </label>
              <button
                className="button"
                type="submit"
                disabled={
                  !value.ready || !selected || !!busy || reason.trim().length < 3 || !confirmed
                }
              >
                {busy === 'create' ? '正在冻结…' : '生成本月交付包'}
              </button>
            </form>
          )}
          {((!clientOnly && value.permissions.generate) ||
            value.packages.some((item) => canAcknowledgePackage(item, session.user_id))) && (
            <div className="monthly-package-confirm">
              <label>
                {clientOnly ? '签收说明' : '交付或签收说明'}
                <input
                  value={reason}
                  maxLength={500}
                  disabled={!!busy}
                  onChange={(event) => setReason(event.target.value)}
                  placeholder={
                    clientOnly ? '例如：已收到本月报表与账务资料' : '说明本次交付范围或收到的资料'
                  }
                />
              </label>
              <label className="monthly-package-check">
                <input
                  type="checkbox"
                  checked={confirmed}
                  disabled={!!busy}
                  onChange={(event) => setConfirmed(event.target.checked)}
                />
                {clientOnly
                  ? '我已核对月份与版本，并确认收到本版全部文件'
                  : '我已核对本企业、本月份及本次版本'}
              </label>
              {clientOnly && (
                <p className="monthly-package-note">
                  签收仅表示收到资料，不代表税务机关已受理，也不代表财务软件已入账。
                </p>
              )}
            </div>
          )}
          {!value.packages.length && (
            <p className="monthly-package-empty">
              {clientOnly
                ? '本月尚未发布交付包，请联系本月服务会计确认进度。'
                : '本月尚无冻结交付包。完成资料验收、会计复核、月结和报表发布后，由获授权会计生成。'}
            </p>
          )}
          <ul className="monthly-package-list">
            {value.packages.map((item) => (
              <li key={item.id}>
                <div>
                  <strong>
                    {item.period} ·{' '}
                    {clientOnly ? `本月资料包（第 ${item.version} 版）` : `交付包 V${item.version}`}
                  </strong>
                  <span className="status-pill">
                    {clientOnly
                      ? packageOpeningLabel(item)
                      : packageReceiptLabel(item.customer_receipt_status)}
                  </span>
                  <p>
                    {clientOnly
                      ? `${item.period} · 第 ${item.version} 版 · 报表 V${item.report_version}${item.superseded ? ' · 已有更新版本' : ''}`
                      : `报表 V${item.report_version} · 原件索引 · 财务软件导入回执另行核验`}
                  </p>
                  {clientOnly && item.opening && (
                    <p className="monthly-package-note">首次查看：{item.opening.opened_at}</p>
                  )}
                  {clientOnly && contents?.id === item.id && (
                    <details className="monthly-package-contents">
                      <summary>本版包含 {contents.files.length} 个文件</summary>
                      <ul>
                        {contents.files.map((file) => (
                          <li key={file.path}>
                            <code>{file.path}</code>
                            <span>{formatBytes(file.size_bytes)}</span>
                          </li>
                        ))}
                      </ul>
                    </details>
                  )}
                  <details>
                    <summary>{clientOnly ? '校验信息（供技术核对）' : '查看版本校验信息'}</summary>
                    <code>{item.manifest_sha256}</code>
                    <p>冻结时间：{item.created_at}</p>
                  </details>
                  {item.acknowledgement && (
                    <p>客户签收时间：{item.acknowledgement.acknowledged_at}</p>
                  )}
                </div>
                <div className="monthly-package-actions">
                  <button
                    type="button"
                    className={clientOnly ? 'button' : 'button secondary small'}
                    disabled={!!busy}
                    onClick={() => void download(item)}
                  >
                    <Download size={14} />
                    {clientOnly ? `下载第 ${item.version} 版资料包` : `下载交付包 V${item.version}`}
                  </button>
                  {canAcknowledgePackage(item, session.user_id) && (
                    <button
                      type="button"
                      className={clientOnly ? 'button' : 'button secondary small'}
                      disabled={!!busy || reason.trim().length < 3 || !confirmed}
                      onClick={() => void acknowledge(item)}
                    >
                      {clientOnly ? `确认收到第 ${item.version} 版` : `确认收到 V${item.version}`}
                    </button>
                  )}
                </div>
              </li>
            ))}
          </ul>
        </>
      )}
    </section>
  );
}
