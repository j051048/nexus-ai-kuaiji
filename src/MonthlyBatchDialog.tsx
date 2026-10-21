import { useId, useRef, useState } from 'react';
import { CalendarPlus, RefreshCw } from 'lucide-react';
import { api, errorMessage } from './api';
import { Modal, Spinner } from './AppPrimitives';
import type { Session } from './types';

type BatchRow = {
  company_id: string;
  company_name: string;
  status: 'created' | 'skipped' | 'blocked';
  book_id: string | null;
  plan: { copied: boolean; dropped?: string[]; reason?: string } | null;
  materials: { created: number; reused: number } | null;
  warnings: string[];
  message: string;
};

type BatchItem = {
  id: string;
  company_id: string;
  company_name: string;
  status: 'queued' | 'running' | 'succeeded' | 'skipped' | 'failed' | 'retry_wait';
  error_detail: string;
  result: BatchRow | null;
};

type BatchOperation = {
  id: string;
  kind: 'monthly_open';
  status: 'queued' | 'running' | 'completed' | 'attention' | 'cancelled';
  total: number;
  succeeded: number;
  skipped: number;
  failed: number;
  progress: { total: number; completed: number; percent: number };
  items: BatchItem[];
};

const itemStatusLabels: Record<BatchItem['status'], string> = {
  queued: '排队中',
  running: '处理中',
  succeeded: '已开期',
  skipped: '已存在',
  failed: '待人工处理',
  retry_wait: '等待重试',
};

/** 月初批量开期向导：为既有客户批量创建当月账套、继承分工并生成催资清单。 */
export function MonthlyBatchDialog({
  session,
  period,
  initialSearch,
  onClose,
  onOpened,
}: {
  session: Session;
  period: string;
  initialSearch?: string;
  onClose: () => void;
  onOpened: () => void;
}) {
  const [search, setSearch] = useState(initialSearch ?? '');
  const [generateMaterials, setGenerateMaterials] = useState(true);
  const [reason, setReason] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const errorId = useId();
  const [batch, setBatch] = useState<BatchOperation | null>(null);
  const requestKey = useRef('');
  const openedRef = useRef(onOpened);
  openedRef.current = onOpened;

  async function poll(id: string) {
    try {
      const value = await api<BatchOperation>(
        `/firm/batch-operations/${encodeURIComponent(id)}`,
        session,
      );
      setBatch(value);
      if (['queued', 'running'].includes(value.status)) {
        window.setTimeout(() => void poll(id), 800);
      } else {
        openedRef.current();
      }
    } catch (failure) {
      setError(errorMessage(failure));
    }
  }

  async function retryFailed() {
    if (!batch || busy) return;
    setBusy(true);
    setError('');
    try {
      const value = await api<BatchOperation>(
        `/firm/batch-operations/${encodeURIComponent(batch.id)}/retry`,
        session,
        {
          method: 'POST',
          body: {
            idempotency_key: crypto.randomUUID(),
            reason: '只重试失败的客户开期',
          },
        },
      );
      setBatch(value);
      if (['queued', 'running'].includes(value.status)) void poll(value.id);
      else onOpened();
    } catch (failure) {
      setError(errorMessage(failure));
    } finally {
      setBusy(false);
    }
  }

  async function run() {
    if (reason.trim().length < 3) {
      setError('请填写至少 3 个字的操作依据。');
      return;
    }
    setBusy(true);
    setError('');
    try {
      if (!requestKey.current) requestKey.current = crypto.randomUUID();
      const value = await api<BatchOperation>('/firm/batch-operations/monthly-open', session, {
        method: 'POST',
        body: {
          idempotency_key: requestKey.current,
          period,
          scope: 'awaiting',
          search: search.trim(),
          generate_materials: generateMaterials,
          reason: reason.trim(),
        },
      });
      setBatch(value);
      onOpened();
      if (['queued', 'running'].includes(value.status)) void poll(value.id);
    } catch (failure) {
      setError(errorMessage(failure));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      title="月初批量开期"
      subtitle={`${period} · 为尚未建账的企业批量创建账套、继承上月分工并生成催资清单`}
      onClose={onClose}
      busy={busy}
      describedBy={error ? errorId : undefined}
    >
      {batch ? (
        <div className="batch-result">
          <p role="status" className="batch-summary">
            已开期 {batch.succeeded} 家 · 已存在 {batch.skipped} 家 · 待人工处理 {batch.failed} 家
            {' · '}进度 {batch.progress.completed}/{batch.progress.total}
          </p>
          <div
            className="batch-progress"
            role="progressbar"
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={batch.progress.percent}
          >
            <span style={{ width: `${batch.progress.percent}%` }} />
          </div>
          <div className="batch-rows" aria-label="批量开期逐户结果">
            {batch.items.map((item) => {
              const row = item.result;
              return (
                <article key={item.id} className={'batch-row ' + item.status}>
                  <header>
                    <strong>{item.company_name}</strong>
                    <span className={'batch-badge ' + item.status}>
                      {itemStatusLabels[item.status]}
                    </span>
                  </header>
                  {row && (
                    <p>
                      {row.plan?.copied
                        ? '已继承上月服务单分工。'
                        : row.plan?.reason === 'source_without_plan'
                          ? '上月尚未填写服务单分工。'
                          : row.plan?.reason === 'already_planned'
                            ? '已有服务单分工，未覆盖。'
                            : ''}
                      {row.materials
                        ? `催资清单：新建 ${row.materials.created} 项、复用 ${row.materials.reused} 项。`
                        : ''}
                    </p>
                  )}
                  {(item.error_detail || row?.message) && (
                    <p className="batch-message">{item.error_detail || row?.message}</p>
                  )}
                  {(row?.warnings ?? []).map((warning) => (
                    <p className="batch-warning" key={warning}>
                      {warning}
                    </p>
                  ))}
                </article>
              );
            })}
            {!batch.items.length && (
              <p className="field-help">当前范围内没有待建账企业，无需开期。</p>
            )}
          </div>
          {error && (
            <p className="form-error" role="alert" id={errorId}>
              {error}
            </p>
          )}
          <div className="batch-actions">
            {batch.failed > 0 && (
              <button
                className="button secondary"
                type="button"
                disabled={busy || ['queued', 'running'].includes(batch.status)}
                onClick={() => void retryFailed()}
              >
                <RefreshCw size={14} />
                只重试失败项
              </button>
            )}
            <button
              className="button secondary"
              type="button"
              disabled={busy || ['queued', 'running'].includes(batch.status)}
              onClick={() => {
                requestKey.current = '';
                setBatch(null);
              }}
            >
              <RefreshCw size={14} />
              再来一批
            </button>
            <button
              className="button primary"
              type="button"
              disabled={busy || ['queued', 'running'].includes(batch.status)}
              onClick={onClose}
            >
              完成批量开期
            </button>
          </div>
        </div>
      ) : (
        <div className="batch-dialog">
          <p className="batch-info">
            只为已有历史账期的企业创建本月账套：长期账套沿用既有科目与当前有效成员，上月服务单分工重新核权后继承；
            首次接入的企业请先完成开通清单。
          </p>
          <label>
            企业名称筛选
            <input
              aria-label="批量开期搜索范围"
              placeholder="留空表示全部待建账企业（最多 200 家）"
              maxLength={120}
              value={search}
              disabled={busy}
              onChange={(event) => setSearch(event.target.value)}
            />
          </label>
          <label className="batch-checkbox">
            <input
              type="checkbox"
              aria-label="生成本月资料催收清单"
              checked={generateMaterials}
              disabled={busy}
              onChange={(event) => setGenerateMaterials(event.target.checked)}
            />
            按长期账套模板生成本月资料催收清单
          </label>
          <label>
            操作依据
            <textarea
              aria-label="批量开期操作依据"
              aria-describedby={error ? errorId : undefined}
              minLength={3}
              maxLength={1000}
              required
              value={reason}
              disabled={busy}
              onChange={(event) => setReason(event.target.value)}
            />
          </label>
          {error && (
            <p className="form-error" role="alert" id={errorId}>
              {error}
            </p>
          )}
          <div className="batch-actions">
            <button className="button secondary" type="button" disabled={busy} onClick={onClose}>
              取消
            </button>
            <button
              className="button primary"
              type="button"
              disabled={busy || reason.trim().length < 3}
              onClick={() => void run()}
            >
              {busy ? <Spinner /> : <CalendarPlus size={15} />}
              开始批量开期
            </button>
          </div>
        </div>
      )}
    </Modal>
  );
}
