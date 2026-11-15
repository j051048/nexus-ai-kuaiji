import { useEffect, useRef, useState } from 'react';
import { api, ApiError } from './api';
import type { Session } from './types';
import './tax-fee.css';

type Source = {
  id: string;
  book_id: string;
  version: number;
  status: string;
  filename: string;
  source_system: string;
  external_id: string;
};
type Row = {
  id: string;
  source_row_number: number;
  external_id: string;
  driver_id: string | null;
  vehicle_id: string | null;
  gross_cents: number;
  discount_cents: number;
  adjustment_cents: number;
  net_cents: number;
  status: string;
  reason: string;
  line_ordinal: number | null;
};
type Group = { id: string; name: string; row_count: number; net_cents: number };
type Batch = {
  id: string;
  book_id: string;
  period: string;
  status: string;
  version: number;
  draft_id: string;
  total_cents: number;
  row_count: number;
  skipped_count: number;
  policy: { version: string; driver_receivable_account: string; vehicle_revenue_account: string };
  policy_fingerprint: string;
  source_fingerprint: string;
  batch_fingerprint: string;
  stale: boolean;
  stale_reason: string;
  source_files: {
    id: string;
    filename: string;
    external_id: string;
    version: number;
    sha256: string;
  }[];
  rows: Row[];
  exceptions: Row[];
  by_driver: Group[];
  by_vehicle: Group[];
};
const money = (cents: number) =>
  `${cents < 0 ? '−' : ''}¥${(Math.abs(cents) / 100).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
const labels: Record<string, string> = {
  adopted: '已采用',
  zero: '零额',
  rejected: '拒绝',
  draft: '待复核',
  reviewed: '已复核',
  invalidated: '已失效',
};

type Props = { session: Session; bookId: string; period: string };
export function TaxFeeBatchPanel(props: Props) {
  return <ScopedTaxFeeBatchPanel key={`${props.session.scope_key}:${props.session.user_id}:${props.bookId}:${props.period}`} {...props} />;
}

function ScopedTaxFeeBatchPanel({
  session,
  bookId,
  period,
}: {
  session: Session;
  bookId: string;
  period: string;
}) {
  const url = `/tax/books/${encodeURIComponent(bookId)}/fee-batches`;
  const [sources, setSources] = useState<Source[]>([]);
  const [batches, setBatches] = useState<Batch[]>([]);
  const [selected, setSelected] = useState<string[]>([]);
  const [batch, setBatch] = useState<Batch | null>(null);
  const [policyVersion, setPolicyVersion] = useState('fee-v1');
  const [receivable, setReceivable] = useState('1122');
  const [revenue, setRevenue] = useState('6001');
  const [reason, setReason] = useState('独立复核规费批次');
  const [notice, setNotice] = useState('');
  const [failure, setFailure] = useState('');
  const [busy, setBusy] = useState(false);
  const revision = useRef(0);
  function checked(data: Batch) {
    if (data.book_id !== bookId || data.period !== period)
      throw new ApiError('返回的批次范围不一致，请刷新后核对。', 409);
    return data;
  }

  async function load() {
    const current = ++revision.current;
    setFailure('');
    try {
      const [sourceData, batchData] = await Promise.all([
        api<{ items: Source[] }>(`/tax/books/${encodeURIComponent(bookId)}/sources`, session),
        api<{ items: Batch[] }>(url, session),
      ]);
      if (current !== revision.current) return;
      if (sourceData.items.some(item => item.book_id !== bookId))
        throw new ApiError('返回的来源范围不一致，请刷新后核对。', 409);
      batchData.items.forEach(checked);
      setSources(sourceData.items.filter((item) => item.status === 'active'));
      setBatches(batchData.items);
      setBatch(previous => previous ? batchData.items.find(item => item.id === previous.id) ?? null : null);
    } catch (error) {
      if (current !== revision.current) return;
      setSources([]); setBatches([]); setSelected([]); setBatch(null); setNotice('');
      setFailure(error instanceof ApiError ? error.message : '规费批次读取失败。');
    }
  }
  useEffect(() => {
    void load();
    return () => { revision.current++; };
    /* eslint-disable-next-line react-hooks/exhaustive-deps */
  }, [bookId, session]);

  async function generate() {
    if (!selected.length) {
      setFailure('请选择至少一个已采用来源。');
      return;
    }
    setBusy(true);
    setFailure('');
    setNotice('');
    try {
      const data = await api<Batch>(url, session, {
        method: 'POST',
        body: {
          period,
          source_file_ids: selected,
          policy: {
            version: policyVersion,
            driver_receivable_account: receivable,
            vehicle_revenue_account: revenue,
          },
        },
      });
      setBatch(checked(data));
      setNotice(
        data.status === 'draft'
          ? '规费批次草稿已生成；须由另一名有复核权的会计独立复核。'
          : '已返回同一批次。',
      );
      await load();
    } catch (error) {
      setFailure(
        error instanceof ApiError ? `${error.message}（${error.status}）` : '规费批次生成失败。',
      );
    } finally {
      setBusy(false);
    }
  }
  async function review() {
    if (!batch) return;
    setBusy(true);
    setFailure('');
    setNotice('');
    try {
      const data = await api<Batch>(`${url}/${encodeURIComponent(batch.id)}/review`, session, {
        method: 'POST',
        body: { expected_version: batch.version, reason },
      });
      setBatch(checked(data));
      setNotice('批次已独立复核；凭证版本已冻结。');
      await load();
    } catch (error) {
      setFailure(error instanceof ApiError ? `${error.message}（${error.status}）` : '复核失败。');
    } finally {
      setBusy(false);
    }
  }
  async function invalidate() {
    if (!batch) return;
    setBusy(true);
    setFailure('');
    setNotice('');
    try {
      const data = await api<Batch>(`${url}/${encodeURIComponent(batch.id)}/invalidate`, session, {
        method: 'POST',
        body: { expected_version: batch.version, reason: '来源或规则变化，停止继续使用' },
      });
      setBatch(checked(data));
      setNotice('批次已失效；历史记录保留，不能继续复核或导出。');
      await load();
    } catch (error) {
      setFailure(
        error instanceof ApiError ? `${error.message}（${error.status}）` : '失效操作失败。',
      );
    } finally {
      setBusy(false);
    }
  }
  function toggle(id: string) {
    setSelected((current) =>
      current.includes(id) ? current.filter((item) => item !== id) : [...current, id],
    );
  }
  return (
    <section className="tax-fee-panel" aria-labelledby="tax-fee-title">
      <header>
        <div>
          <p className="tax-fee-kicker">TAX06 · 出租车规费</p>
          <h3 id="tax-fee-title">规费批次与独立复核</h3>
        </div>
        <button type="button" onClick={() => void load()} disabled={busy}>
          刷新
        </button>
      </header>
      <p className="tax-fee-note">
        仅使用来源台账中“已采用”的行；驾驶员应收与单车收入分开，优惠和调整必须保留源行理由。生成草稿不代表已复核，银行到账也不在这里记收入。
      </p>
      <div className="tax-fee-form">
        <label>
          策略版本
          <input value={policyVersion} onChange={(event) => setPolicyVersion(event.target.value)} />
        </label>
        <label>
          驾驶员应收科目
          <input value={receivable} onChange={(event) => setReceivable(event.target.value)} />
        </label>
        <label>
          单车收入科目
          <input value={revenue} onChange={(event) => setRevenue(event.target.value)} />
        </label>
      </div>
      <div className="tax-fee-sources">
        <strong>选择来源</strong>
        {sources.length === 0 ? (
          <span>当前账套没有活动来源，请先在来源台账导入并核定。</span>
        ) : (
          sources.map((source) => (
            <label key={source.id}>
              <input
                type="checkbox"
                checked={selected.includes(source.id)}
                onChange={() => toggle(source.id)}
              />{' '}
              {source.filename} · v{source.version} · {source.external_id}
            </label>
          ))
        )}
      </div>
      <button
        type="button"
        className="tax-fee-primary"
        disabled={busy}
        onClick={() => void generate()}
      >
        生成规费批次草稿
      </button>
      {(notice || failure) && (
        <div
          role={failure ? 'alert' : 'status'}
          className={failure ? 'tax-fee-error' : 'tax-fee-ok'}
        >
          {failure || notice}
        </div>
      )}
      {batch && (
        <div className="tax-fee-batch">
          <div className="tax-fee-summary">
            <div>
              <span>状态</span>
              <strong>{labels[batch.status] ?? batch.status}</strong>
            </div>
            <div>
              <span>批次金额</span>
              <strong>{money(batch.total_cents)}</strong>
            </div>
            <div>
              <span>采用行 / 跳过</span>
              <strong>
                {batch.row_count} / {batch.skipped_count}
              </strong>
            </div>
            <div>
              <span>版本</span>
              <strong>v{batch.version}</strong>
            </div>
          </div>
          <p className="tax-fee-fingerprint">
            策略 {batch.policy.version} · 批次指纹 {batch.batch_fingerprint.slice(0, 12)}… ·
            来源指纹 {batch.source_fingerprint.slice(0, 12)}…
          </p>
          {batch.stale && (
            <div role="alert" className="tax-fee-error">
              {batch.stale_reason}
            </div>
          )}
          <p className="tax-fee-fingerprint">
            原件索引：
            {batch.source_files.map((file) => (
              <span key={file.id}>
                {file.filename} · v{file.version} · {file.external_id} · sha{' '}
                {file.sha256.slice(0, 12)}…{' '}
              </span>
            ))}
          </p>
          <div className="tax-fee-groups" aria-label="按驾驶员和车辆对平">
            <div>
              <strong>按驾驶员</strong>
              {batch.by_driver.map((item) => (
                <span key={item.id}>
                  {item.name || item.id.slice(0, 6)} · {item.row_count} 行 · {money(item.net_cents)}
                </span>
              ))}
            </div>
            <div>
              <strong>按车辆</strong>
              {batch.by_vehicle.map((item) => (
                <span key={item.id}>
                  {item.name || item.id.slice(0, 6)} · {item.row_count} 行 · {money(item.net_cents)}
                </span>
              ))}
            </div>
          </div>
          <table>
            <thead>
              <tr>
                <th>来源行</th>
                <th>驾驶员/车辆</th>
                <th>毛额</th>
                <th>优惠</th>
                <th>调整</th>
                <th>净额</th>
                <th>状态</th>
              </tr>
            </thead>
            <tbody>
              {batch.rows.map((row) => (
                <tr key={row.id}>
                  <td>
                    {row.external_id} · {row.source_row_number}
                  </td>
                  <td>
                    {row.driver_id?.slice(0, 6) ?? '—'} / {row.vehicle_id?.slice(0, 6) ?? '—'}
                  </td>
                  <td>{money(row.gross_cents)}</td>
                  <td>{money(row.discount_cents)}</td>
                  <td>{money(row.adjustment_cents)}</td>
                  <td>{money(row.net_cents)}</td>
                  <td>
                    {labels[row.status] ?? row.status}
                    {row.reason ? ` · ${row.reason}` : ''}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {batch.status === 'draft' && (
            <div className="tax-fee-review">
              <label>
                复核理由
                <input value={reason} onChange={(event) => setReason(event.target.value)} />
              </label>
              <button type="button" disabled={busy} onClick={() => void review()}>
                独立复核并冻结
              </button>
              <button
                type="button"
                className="secondary"
                disabled={busy}
                onClick={() => void invalidate()}
              >
                标记失效
              </button>
            </div>
          )}
        </div>
      )}
      <details className="tax-fee-history">
        <summary>历史批次（{batches.length}）</summary>
        {batches.map((item) => (
          <button type="button" key={item.id} onClick={() => setBatch(item)}>
            {item.period} · v{item.version} · {labels[item.status] ?? item.status} ·{' '}
            {money(item.total_cents)}
          </button>
        ))}
      </details>
    </section>
  );
}
