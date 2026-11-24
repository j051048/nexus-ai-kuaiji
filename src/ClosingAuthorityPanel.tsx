import { useCallback, useEffect, useState } from 'react';
import { api, ApiError } from './api';
import { scheduleSubpanel } from './subpanel-scheduler';
import type { Session } from './types';

type Authority = {
  confirmed: boolean;
  mode: 'internal' | 'tplus_voucher' | 'hybrid' | string;
  profile_id: string | null;
  profile_version: number | null;
  profile_fingerprint: string | null;
  source_of_truth: string;
};

type Evidence = {
  id: string;
  period: string;
  authority_mode: string;
  source_system: string;
  source_object: string;
  source_id: string;
  source_version: string;
  content_fingerprint: string;
  amount_cents: number;
  status: string;
  created_at: string;
};

type Summary = {
  authority: Authority;
  evidence: Evidence[];
  status_counts: Record<string, number>;
  double_count_blocked: boolean;
  next_action: string;
};

const MODE_LABEL: Record<string, string> = {
  internal: '内部账簿',
  tplus_voucher: '外部 T+ 凭证',
  hybrid: '混合核对',
};

const STATUS_LABEL: Record<string, string> = {
  matched: '已对应（外部权威）',
  conflict: '冲突（内部已记账）',
  evidence_only: '仅核对（内部权威）',
  needs_review: '待人工确认',
};

function money(cents: number) {
  return (cents / 100).toLocaleString('zh-CN', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}

export function ClosingAuthorityPanel({ session, bookId }: { session: Session; bookId: string }) {
  const [summary, setSummary] = useState<Summary | null>(null);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [busy, setBusy] = useState(false);
  const [sourceSystem, setSourceSystem] = useState('tplus');
  const [sourceObject, setSourceObject] = useState('closing_voucher');
  const [sourceId, setSourceId] = useState('');
  const [sourceVersion, setSourceVersion] = useState('1');
  const [amount, setAmount] = useState('');
  const [details, setDetails] = useState('{"voucher":"记-001"}');
  const [reason, setReason] = useState('客户外部结转回执核对');

  const load = useCallback(
    async (signal?: AbortSignal) => {
      setError('');
      try {
        const data = await scheduleSubpanel(
          () =>
            api<Summary>(
              `/periodic/closing/summary?book_id=${encodeURIComponent(bookId)}`,
              session,
              {
                signal,
              },
            ),
          signal,
        );
        if (signal?.aborted) return;
        setSummary(data);
      } catch (caught) {
        if (signal?.aborted) return;
        setSummary(null);
        setError(caught instanceof ApiError ? caught.message : '结转权威信息读取失败');
      }
    },
    [bookId, session],
  );

  useEffect(() => {
    const controller = new AbortController();
    void load(controller.signal);
    return () => controller.abort();
  }, [load]);

  async function bind() {
    setBusy(true);
    setError('');
    setNotice('');
    try {
      let parsed: unknown = {};
      try {
        parsed = JSON.parse(details || '{}');
      } catch {
        throw new Error('外部证据详情必须是合法 JSON');
      }
      const result = await api<Evidence & { replayed?: boolean }>(
        '/periodic/closing/evidence',
        session,
        {
          method: 'POST',
          body: {
            book_id: bookId,
            source_system: sourceSystem,
            source_object: sourceObject,
            source_id: sourceId,
            source_version: sourceVersion,
            amount,
            reason,
            details: parsed,
          },
        },
      );
      setNotice(
        `${STATUS_LABEL[result.status] || result.status}：外部证据 ${result.source_id} / ${result.source_version}` +
          (result.replayed ? '（相同内容已登记，未重复）' : ''),
      );
      setSourceId('');
      await load();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : '登记失败');
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="closing-authority" aria-label="结转权威与外部证据">
      <header>
        <div>
          <h3>结转权威与外部证据</h3>
          <p>外部凭证只作核对，不生成第二张结转凭证；权威模式变化会使旧草稿复核失效。</p>
        </div>
        <button type="button" className="ghost" onClick={() => void load()} disabled={busy}>
          刷新
        </button>
      </header>
      {error ? (
        <div className="inline-error" role="alert">
          {error}
        </div>
      ) : null}
      {notice ? <div className="inline-notice">{notice}</div> : null}
      {!summary ? (
        <p className="muted">{error ? '无法读取结转权威。' : '正在读取结转权威…'}</p>
      ) : (
        <>
          <div className={`closing-authority-banner ${summary.authority.confirmed ? '' : 'warn'}`}>
            <strong>
              当前权威：
              {summary.authority.confirmed
                ? MODE_LABEL[summary.authority.mode] || summary.authority.mode
                : '未确认'}
            </strong>
            <span>{summary.authority.source_of_truth}</span>
            {summary.authority.profile_version ? (
              <code>profile v{summary.authority.profile_version}</code>
            ) : null}
          </div>
          {summary.double_count_blocked ? (
            <div className="inline-warning">
              外部权威模式下内部结转已阻断；外部证据只登记对应或冲突，不增加收入/利润。
            </div>
          ) : null}
          <p className="muted">{summary.next_action}</p>
          <div className="closing-evidence-list">
            <h4>已登记外部证据（{summary.evidence.length}）</h4>
            {summary.evidence.length === 0 ? (
              <p className="muted">尚无外部结转证据。未确认权威前，登记会被拒绝。</p>
            ) : (
              <table>
                <thead>
                  <tr>
                    <th>来源</th>
                    <th>编号/版本</th>
                    <th>金额</th>
                    <th>状态</th>
                    <th>登记时间</th>
                  </tr>
                </thead>
                <tbody>
                  {summary.evidence.map((item) => (
                    <tr key={item.id}>
                      <td data-label="来源">
                        <span className="closing-evidence-value">
                          {item.source_system} / {item.source_object}
                        </span>
                      </td>
                      <td data-label="编号/版本">
                        <span className="closing-evidence-value">
                          {item.source_id} · v{item.source_version}
                        </span>
                      </td>
                      <td data-label="金额">
                        <span className="closing-evidence-value">¥{money(item.amount_cents)}</span>
                      </td>
                      <td data-label="状态">
                        <span className={`evidence-status ${item.status}`}>
                          {STATUS_LABEL[item.status] || item.status}
                        </span>
                      </td>
                      <td data-label="登记时间">
                        <span className="closing-evidence-value">
                          {item.created_at.slice(0, 16).replace('T', ' ')}
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
          <fieldset className="closing-evidence-form">
            <legend>登记外部结转回执</legend>
            <label>
              来源系统
              <input
                value={sourceSystem}
                onChange={(event) => setSourceSystem(event.target.value)}
                maxLength={80}
              />
            </label>
            <label>
              来源对象
              <input
                value={sourceObject}
                onChange={(event) => setSourceObject(event.target.value)}
                maxLength={120}
              />
            </label>
            <label>
              外部编号
              <input
                value={sourceId}
                onChange={(event) => setSourceId(event.target.value)}
                maxLength={160}
                placeholder="如 CLOSE-2026-10-001"
              />
            </label>
            <label>
              版本
              <input
                value={sourceVersion}
                onChange={(event) => setSourceVersion(event.target.value)}
                maxLength={80}
              />
            </label>
            <label>
              金额（元）
              <input
                inputMode="decimal"
                value={amount}
                onChange={(event) => setAmount(event.target.value)}
                placeholder="0.00"
              />
            </label>
            <label className="wide">
              详情 JSON
              <textarea
                value={details}
                onChange={(event) => setDetails(event.target.value)}
                rows={2}
              />
            </label>
            <label className="wide">
              登记原因
              <input
                value={reason}
                onChange={(event) => setReason(event.target.value)}
                maxLength={1000}
              />
            </label>
            <button
              type="button"
              onClick={() => void bind()}
              disabled={busy || !sourceId || !amount}
            >
              {busy ? '登记中…' : '登记外部证据'}
            </button>
          </fieldset>
          <p className="muted small">登记只记录回执与指纹；不会复核、记账、申报或向客户发送。</p>
        </>
      )}
    </section>
  );
}
