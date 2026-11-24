import { useEffect, useRef, useState } from 'react';
import { api, ApiError } from './api';
import { scheduleSubpanel } from './subpanel-scheduler';
import type { Session } from './types';
import { formatCents, parseYuanToCents } from './money';
import './oa-snapshot.css';

type Snapshot = {
  id: string;
  oa_event_id: string;
  oa_version: number;
  period: string;
  title: string;
  approved_by: string;
  approved_at: string;
  lines: { account_code: string; amount_cents: number; description: string }[];
  fingerprint: string;
};
type MaterialRequest = {
  id: string;
  snapshot_id: string;
  title: string;
  reason: string;
  status: string;
};

function cents(value: string) {
  const parsed = parseYuanToCents(value);
  return parsed === null ? 0 : Number(parsed);
}

export function OaSnapshotPanel({
  session,
  bookId,
  period,
  canManage,
}: {
  session: Session;
  bookId: string;
  period: string;
  canManage: boolean;
}) {
  const [snapshots, setSnapshots] = useState<Snapshot[]>([]);
  const [requests, setRequests] = useState<MaterialRequest[]>([]);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [eventId, setEventId] = useState('');
  const [version, setVersion] = useState('1');
  const [title, setTitle] = useState('');
  const [approvedBy, setApprovedBy] = useState('');
  const [approvedAt, setApprovedAt] = useState(new Date().toISOString().slice(0, 16));
  const [account, setAccount] = useState('6602.02');
  const [amount, setAmount] = useState('');
  const [reason, setReason] = useState('');
  const loadSequence = useRef(0);

  async function load(signal?: AbortSignal) {
    const sequence = ++loadSequence.current;
    try {
      const [a, b] = await Promise.all([
        scheduleSubpanel(
          () => api<{ items: Snapshot[] }>(`/books/${bookId}/oa-snapshots`, session, { signal }),
          signal,
        ),
        scheduleSubpanel(
          () =>
            api<{ items: MaterialRequest[] }>(
              `/books/${bookId}/oa-snapshots/material-requests`,
              session,
              { signal },
            ),
          signal,
        ),
      ]);
      if (sequence !== loadSequence.current || signal?.aborted) return;
      setSnapshots(a.items);
      setRequests(b.items);
      setError('');
    } catch (err) {
      if (sequence !== loadSequence.current || signal?.aborted) return;
      setError(err instanceof ApiError ? err.message : 'OA 快照读取失败');
    }
  }
  useEffect(() => {
    const controller = new AbortController();
    void load(controller.signal);
    return () => {
      loadSequence.current += 1;
      controller.abort();
    };
  }, [bookId, period]);

  async function register() {
    setBusy(true);
    setError('');
    loadSequence.current += 1;
    try {
      await scheduleSubpanel(() =>
        api(`/books/${bookId}/oa-snapshots`, session, {
          method: 'POST',
          body: {
            oa_event_id: eventId.trim(),
            oa_version: Number(version),
            period,
            title: title.trim(),
            approved_by: approvedBy.trim(),
            approved_at: new Date(approvedAt).toISOString(),
            lines: [
              {
                account_code: account.trim(),
                amount_cents: cents(amount),
                description: title.trim(),
              },
            ],
          },
        }),
      );
      setEventId('');
      setTitle('');
      setAmount('');
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'OA 快照登记失败');
    } finally {
      setBusy(false);
    }
  }

  async function request(snapshot: Snapshot) {
    setBusy(true);
    setError('');
    loadSequence.current += 1;
    try {
      await scheduleSubpanel(() =>
        api(`/books/${bookId}/oa-snapshots/material-requests`, session, {
          method: 'POST',
          body: {
            snapshot_id: snapshot.id,
            title: snapshot.title,
            reason: reason.trim() || '补交审批依据与原始附件',
          },
        }),
      );
      setReason('');
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : '材料请求创建失败');
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="oa-snapshot" aria-label="OA 审批快照与补件材料">
      <header>
        <div>
          <h3>OA 审批快照 · 只补材料</h3>
          <p>
            只登记已批准的 OA 业务事实，不重放审批、不替代补件任务；同版本同内容幂等，异内容拒绝。
          </p>
        </div>
        <button type="button" onClick={() => void load()} disabled={busy}>
          刷新
        </button>
      </header>
      {error && (
        <p className="oa-error" role="alert">
          {error}
        </p>
      )}
      <div className={`oa-grid${canManage ? '' : ' read-only'}`}>
        {canManage && (
          <div className="oa-form">
            <h4>登记审批快照</h4>
            <label>
              OA 业务编号
              <input
                value={eventId}
                onChange={(e) => setEventId(e.target.value)}
                placeholder="OA-2026-10-15"
              />
            </label>
            <label>
              审批版本
              <input
                value={version}
                onChange={(e) => setVersion(e.target.value)}
                inputMode="numeric"
              />
            </label>
            <label>
              事项摘要
              <input
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                placeholder="客户拜访交通费补件"
              />
            </label>
            <label>
              审批人
              <input value={approvedBy} onChange={(e) => setApprovedBy(e.target.value)} />
            </label>
            <label>
              审批时间
              <input
                type="datetime-local"
                value={approvedAt}
                onChange={(e) => setApprovedAt(e.target.value)}
              />
            </label>
            <div className="oa-line">
              <label>
                科目
                <input value={account} onChange={(e) => setAccount(e.target.value)} />
              </label>
              <label>
                金额
                <input
                  value={amount}
                  onChange={(e) => setAmount(e.target.value)}
                  inputMode="decimal"
                />
              </label>
            </div>
            <button
              type="button"
              className="primary"
              disabled={
                busy || !eventId.trim() || !title.trim() || !approvedBy.trim() || cents(amount) <= 0
              }
              onClick={() => void register()}
            >
              登记并冻结快照
            </button>
          </div>
        )}
        <div>
          <h4>已登记快照</h4>
          {snapshots.length === 0 ? (
            <p className="oa-empty">本账套本期还没有 OA 审批快照。</p>
          ) : (
            snapshots.map((row) => (
              <article className="oa-item" key={row.id}>
                <strong>{row.title}</strong>
                <span>
                  {row.oa_event_id} · v{row.oa_version} · {row.period}
                </span>
                <span>
                  审批人 {row.approved_by} ·{' '}
                  {row.lines
                    .map(
                      (line) => `${line.account_code} ¥${formatCents(BigInt(line.amount_cents))}`,
                    )
                    .join(' / ')}
                </span>
                <span className="oa-fingerprint">快照 {row.fingerprint.slice(0, 12)}</span>
                {canManage && (
                  <button type="button" disabled={busy} onClick={() => void request(row)}>
                    生成补件材料请求
                  </button>
                )}
              </article>
            ))
          )}
        </div>
      </div>
      <div className="oa-requests">
        <h4>补件材料请求</h4>
        {requests.length === 0 ? (
          <p className="oa-empty">还没有材料请求。</p>
        ) : (
          requests.map((row) => (
            <div key={row.id} className="oa-request">
              <strong>{row.title}</strong>
              <span>{row.reason}</span>
              <span>{row.status === 'open' ? '待补材料' : '已关闭'}</span>
            </div>
          ))
        )}
      </div>
    </section>
  );
}
