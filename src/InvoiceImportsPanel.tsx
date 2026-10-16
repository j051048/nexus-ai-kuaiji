import { useCallback, useEffect, useRef, useState } from 'react';
import { FileSpreadsheet, LoaderCircle, RefreshCw, TriangleAlert } from 'lucide-react';
import { api, errorMessage } from './api';
import { prepareUploadIntent } from './upload-client';
import type { Session } from './types';
import './invoice-imports.css';

type InvoiceMatch = {
  source_id: string;
  date: string;
  counterparty: string;
  amount_cents: number;
} | null;

type InvoiceRow = {
  number: string;
  code: string;
  date: string | null;
  seller: string;
  buyer: string;
  amount_cents: number | null;
  tax_cents: number | null;
  total_cents: number | null;
  kind: string;
  status: string;
  direction: 'input' | 'output' | 'unknown';
  line: number;
  void: boolean;
  red: boolean;
  duplicate: boolean;
  match: InvoiceMatch;
};

type InvoiceBatchSummary = {
  id: string;
  filename: string;
  row_count: number;
  direction: 'input' | 'output' | 'mixed';
  duplicates: number;
  matched: number;
  warnings: string[];
  created_at: string;
};

type InvoiceBatchDetail = InvoiceBatchSummary & {
  notes: {
    warnings?: string[];
    skipped?: number;
    duplicates?: number;
    matched?: number;
    void?: number;
    red?: number;
    source?: string;
    company_tax_linked?: boolean;
  };
  rows: InvoiceRow[];
};

const DIRECTION_LABEL: Record<string, string> = {
  input: '进项',
  output: '销项',
  mixed: '混合',
  unknown: '待定',
};

function money(cents: number | null) {
  if (cents === null || cents === undefined) return '—';
  const value = Math.abs(cents) / 100;
  return `${cents < 0 ? '-' : ''}¥${value.toLocaleString('zh-CN', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`;
}

export function InvoiceImportsPanel({
  session,
  bookId,
  onBusyChange,
}: {
  session: Session;
  bookId: string;
  onBusyChange?: (busy: boolean) => void;
}) {
  const [items, setItems] = useState<InvoiceBatchSummary[] | null>(null);
  const [detail, setDetail] = useState<InvoiceBatchDetail | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const epoch = useRef(0);
  const mounted = useRef(true);
  const base = `/books/${encodeURIComponent(bookId)}/invoice-imports`;

  const load = useCallback(
    async (keepDetail = false) => {
      const current = ++epoch.current;
      try {
        const value = await api<{ items: InvoiceBatchSummary[] }>(`${base}?limit=20`, session);
        if (mounted.current && epoch.current === current) {
          setItems(value.items);
          if (!keepDetail) setDetail(null);
        }
      } catch (failure) {
        if (mounted.current && epoch.current === current) setError(errorMessage(failure));
      }
    },
    [base, session],
  );

  useEffect(() => {
    mounted.current = true;
    setItems(null);
    setDetail(null);
    setNotice('');
    void load();
    return () => {
      mounted.current = false;
      epoch.current += 1;
    };
  }, [load]);

  async function openDetail(id: string) {
    if (detail?.id === id) {
      setDetail(null);
      return;
    }
    setError('');
    try {
      setDetail(await api<InvoiceBatchDetail>(`${base}/${encodeURIComponent(id)}`, session));
    } catch (failure) {
      setError(errorMessage(failure));
    }
  }

  async function importFile() {
    if (!file) return;
    const name = file.name.toLowerCase();
    if (!name.endsWith('.csv') && !name.endsWith('.xlsx')) {
      setError('仅支持电子税务局导出的 .csv 或 .xlsx 文件');
      return;
    }
    setBusy(true);
    setError('');
    setNotice('');
    onBusyChange?.(true);
    try {
      const uploadId = await prepareUploadIntent(session, file, 'invoice_import');
      const created = await api<{ id: string; row_count: number; direction: string }>(
        base,
        session,
        {
          method: 'POST',
          body: { upload_id: uploadId },
        },
      );
      setFile(null);
      setNotice(
        `已导入 ${created.row_count} 行（${DIRECTION_LABEL[created.direction] ?? created.direction}），只作勾稽证据，不产生凭证。`,
      );
      await load(true);
      setDetail(
        await api<InvoiceBatchDetail>(`${base}/${encodeURIComponent(created.id)}`, session),
      );
    } catch (failure) {
      setError(errorMessage(failure));
    } finally {
      if (mounted.current) setBusy(false);
      onBusyChange?.(false);
    }
  }

  return (
    <section className="panel invoice-imports" aria-label="发票导出表勾稽">
      <div className="panel-heading">
        <div>
          <h2>发票导出表</h2>
          <p>
            导入电子税务局《全量发票查询导出表》，按“交易对手 + 价税合计 + 方向”给出银行流水线索；
            导入只是证据与勾稽，不产生任何凭证。
          </p>
        </div>
        <button
          className="text-button"
          disabled={busy}
          onClick={() => void load()}
          aria-label="刷新发票导入批次"
        >
          <RefreshCw size={13} /> 刷新
        </button>
      </div>
      {error && (
        <p className="inline-error" role="alert">
          <TriangleAlert size={15} />
          {error}
        </p>
      )}
      {notice && (
        <p className="form-success" role="status">
          {notice}
        </p>
      )}
      <div className="invoice-imports-actions">
        <label className="invoice-imports-file">
          <FileSpreadsheet size={15} />
          <input
            type="file"
            accept=".csv,.xlsx"
            disabled={busy}
            aria-label="选择发票导出表"
            onChange={(event) => {
              setFile(event.target.files?.[0] ?? null);
              setError('');
            }}
          />
          <span>{file ? file.name : '选择发票导出表（CSV / XLSX）'}</span>
        </label>
        <button
          className="button primary"
          disabled={busy || !file}
          onClick={() => void importFile()}
        >
          {busy ? <LoaderCircle size={14} className="spinner" /> : <FileSpreadsheet size={14} />}
          导入并勾稽
        </button>
      </div>
      {items && !items.length && !detail && (
        <p className="invoice-imports-empty">还没有导入过发票导出表。</p>
      )}
      {items && items.length > 0 && (
        <ul className="invoice-imports-list">
          {items.map((item) => (
            <li key={item.id}>
              <button
                type="button"
                className={'invoice-imports-batch' + (detail?.id === item.id ? ' selected' : '')}
                onClick={() => void openDetail(item.id)}
              >
                <strong>{item.filename}</strong>
                <span>
                  {DIRECTION_LABEL[item.direction] ?? item.direction} · {item.row_count} 行 · 重复{' '}
                  {item.duplicates} · 勾稽 {item.matched}
                </span>
                <small>{item.created_at.slice(0, 16).replace('T', ' ')}</small>
              </button>
            </li>
          ))}
        </ul>
      )}
      {detail && (
        <div className="invoice-imports-detail">
          <div className="invoice-imports-notes">
            <span>来源 {detail.notes.source ?? '—'}</span>
            <span>跳过 {detail.notes.skipped ?? 0} 行</span>
            <span>作废 {detail.notes.void ?? 0}</span>
            <span>红字 {detail.notes.red ?? 0}</span>
            <span>重复 {detail.notes.duplicates ?? 0}</span>
            <span>勾稽候选 {detail.notes.matched ?? 0}</span>
            {detail.notes.company_tax_linked === false && (
              <span className="invoice-imports-hint">企业税号未配置，方向待定</span>
            )}
          </div>
          {(detail.notes.warnings?.length ?? 0) > 0 && (
            <details className="invoice-imports-warnings">
              <summary>解析提示（{detail.notes.warnings?.length}）</summary>
              <ul>
                {detail.notes.warnings?.slice(0, 50).map((warning) => (
                  <li key={warning}>{warning}</li>
                ))}
              </ul>
            </details>
          )}
          <div className="invoice-imports-table">
            <table>
              <thead>
                <tr>
                  <th>发票号码</th>
                  <th>开票日期</th>
                  <th>销方</th>
                  <th>购方</th>
                  <th>价税合计</th>
                  <th>方向</th>
                  <th>状态</th>
                  <th>银行勾稽</th>
                </tr>
              </thead>
              <tbody>
                {detail.rows.slice(0, 200).map((row) => (
                  <tr
                    key={`${row.number}:${row.line}`}
                    className={row.void ? 'invoice-imports-void' : undefined}
                  >
                    <td>{row.number}</td>
                    <td>{row.date ?? '—'}</td>
                    <td>{row.seller || '—'}</td>
                    <td>{row.buyer || '—'}</td>
                    <td className="numeric">{money(row.total_cents)}</td>
                    <td>{DIRECTION_LABEL[row.direction] ?? row.direction}</td>
                    <td>
                      {row.status || '—'}
                      {row.duplicate && <em className="invoice-imports-tag">重复</em>}
                      {row.red && <em className="invoice-imports-tag red">红字</em>}
                    </td>
                    <td>
                      {row.match
                        ? `${row.match.date} · ${row.match.counterparty} · ${money(row.match.amount_cents)}`
                        : '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {detail.rows.length > 200 && (
              <p className="invoice-imports-empty">
                仅显示前 200 行，共 {detail.rows.length} 行；完整数据保留在导入批次中。
              </p>
            )}
          </div>
        </div>
      )}
    </section>
  );
}
