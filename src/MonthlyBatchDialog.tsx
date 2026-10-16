import { useState } from 'react';
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

type BatchResult = {
  period: string;
  opened: number;
  skipped: number;
  blocked: number;
  results: BatchRow[];
  notes: string[];
};

const statusLabels: Record<BatchRow['status'], string> = {
  created: '已开期',
  skipped: '已存在',
  blocked: '待人工处理',
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
  const [result, setResult] = useState<BatchResult | null>(null);

  async function run() {
    if (reason.trim().length < 3) {
      setError('请填写至少 3 个字的操作依据。');
      return;
    }
    setBusy(true);
    setError('');
    try {
      const value = await api<BatchResult>('/monthly/batch/open', session, {
        method: 'POST',
        body: {
          period,
          scope: 'awaiting',
          search: search.trim(),
          generate_materials: generateMaterials,
          reason: reason.trim(),
        },
      });
      setResult(value);
      onOpened();
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
    >
      {result ? (
        <div className="batch-result">
          <p role="status" className="batch-summary">
            已开期 {result.opened} 家 · 已存在 {result.skipped} 家 · 待人工处理 {result.blocked} 家
          </p>
          <div className="batch-rows" aria-label="批量开期逐户结果">
            {result.results.map((row) => (
              <article key={row.company_id} className={'batch-row ' + row.status}>
                <header>
                  <strong>{row.company_name}</strong>
                  <span className={'batch-badge ' + row.status}>{statusLabels[row.status]}</span>
                </header>
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
                {row.message && <p className="batch-message">{row.message}</p>}
                {row.warnings.map((item) => (
                  <p className="batch-warning" key={item}>
                    {item}
                  </p>
                ))}
              </article>
            ))}
            {!result.results.length && (
              <p className="field-help">当前范围内没有待建账企业，无需开期。</p>
            )}
          </div>
          {result.notes.length > 0 && (
            <ul className="batch-notes">
              {result.notes.map((note) => (
                <li key={note}>{note}</li>
              ))}
            </ul>
          )}
          {error && (
            <p className="form-error" role="alert">
              {error}
            </p>
          )}
          <div className="batch-actions">
            <button
              className="button secondary"
              type="button"
              disabled={busy}
              onClick={() => setResult(null)}
            >
              <RefreshCw size={14} />
              再来一批
            </button>
            <button className="button primary" type="button" disabled={busy} onClick={onClose}>
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
              minLength={3}
              maxLength={1000}
              required
              value={reason}
              disabled={busy}
              onChange={(event) => setReason(event.target.value)}
            />
          </label>
          {error && (
            <p className="form-error" role="alert">
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
