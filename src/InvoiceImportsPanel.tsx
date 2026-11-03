import { useCallback, useEffect, useRef, useState } from 'react';
import { FileSpreadsheet, LoaderCircle, RefreshCw, TriangleAlert } from 'lucide-react';
import { api, errorMessage } from './api';
import type { CatalogAccount } from './catalog-types';
import {
  accrualAccountCandidates,
  defaultInvoiceAccrualAccounts,
  invoiceAccrualEligible,
  type AccrualAccountRole,
  type InvoiceDirection,
} from './invoice-accrual-helpers';
import { prepareUploadIntent } from './upload-client';
import type { Session } from './types';
import './invoice-imports.css';

type InvoiceMatch = {
  source_id: string;
  date: string;
  counterparty: string;
  amount_cents: number;
} | null;

type InvoiceAccrual = {
  source_id: string;
  draft_id: string;
  status: 'draft' | 'approved' | 'exported';
  version: number;
};

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
  accrual: InvoiceAccrual | null;
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

type InvoicePreview = {
  upload_id: string;
  filename: string;
  size_bytes: number;
  row_count: number;
  direction: 'input' | 'output' | 'mixed';
  duplicates: number;
  matched: number;
  void: number;
  red: number;
  unknown_direction: number;
  unmatched_eligible: number;
  company_tax_linked: boolean;
  company_tax_matched: number;
  date_from: string | null;
  date_to: string | null;
  skipped: number;
  warnings: string[];
  source: string;
};

const DIRECTION_LABEL: Record<string, string> = {
  input: '进项',
  output: '销项',
  mixed: '混合',
  unknown: '待定',
};

const ACCRUAL_LABEL: Record<string, string> = {
  draft: '挂账草稿',
  approved: '已审核',
  exported: '已导出',
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
  const [preview, setPreview] = useState<InvoicePreview | null>(null);
  const [accounts, setAccounts] = useState<CatalogAccount[] | null>(null);
  const [accrualRow, setAccrualRow] = useState<InvoiceRow | null>(null);
  const [accrualForm, setAccrualForm] = useState({
    settlement_account: '',
    profit_account: '',
    tax_account: '',
    reason: '',
  });
  const [accrualBusy, setAccrualBusy] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const epoch = useRef(0);
  const mounted = useRef(true);
  const firstAccount = useRef<HTMLSelectElement>(null);
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
    setAccounts(null);
    setAccrualRow(null);
    setNotice('');
    void load();
    return () => {
      mounted.current = false;
      epoch.current += 1;
    };
  }, [load]);

  useEffect(() => {
    if (!accrualRow) return;
    const close = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && !accrualBusy) setAccrualRow(null);
    };
    window.addEventListener('keydown', close);
    return () => window.removeEventListener('keydown', close);
  }, [accrualRow, accrualBusy]);

  async function openDetail(id: string) {
    if (busy) return;
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

  async function previewFile() {
    if (!file) return;
    const name = file.name.toLowerCase();
    if (!name.endsWith('.csv') && !name.endsWith('.xlsx')) {
      setError('仅支持电子税务局导出的 .csv 或 .xlsx 文件');
      return;
    }
    setBusy(true);
    setError('');
    setNotice('');
    setPreview(null);
    onBusyChange?.(true);
    try {
      const uploadId = await prepareUploadIntent(session, file, 'invoice_import');
      setPreview(
        await api<InvoicePreview>(`${base}/preview`, session, {
          method: 'POST',
          body: { upload_id: uploadId },
        }),
      );
    } catch (failure) {
      setError(errorMessage(failure));
    } finally {
      if (mounted.current) setBusy(false);
      onBusyChange?.(false);
    }
  }

  async function confirmImport() {
    if (!preview) return;
    setBusy(true);
    setError('');
    setNotice('');
    onBusyChange?.(true);
    try {
      const created = await api<{ id: string; row_count: number; direction: string }>(
        base,
        session,
        {
          method: 'POST',
          body: { upload_id: preview.upload_id },
        },
      );
      setFile(null);
      setPreview(null);
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

  function optionsFor(role: AccrualAccountRole) {
    if (!accounts || !accrualRow || accrualRow.direction === 'unknown') return [];
    return accrualAccountCandidates(accounts, role, accrualRow.direction);
  }

  async function openAccrual(row: InvoiceRow) {
    if (row.direction === 'unknown') return;
    setError('');
    try {
      let available = accounts;
      if (!available) {
        const catalog = await api<{ accounts: CatalogAccount[] }>(
          `/books/${encodeURIComponent(bookId)}/catalog`,
          session,
        );
        available = catalog.accounts.filter((account) => account.active);
        setAccounts(available);
      }
      setAccrualForm({
        ...defaultInvoiceAccrualAccounts(available, row.direction),
        reason: '',
      });
      setAccrualRow(row);
      window.setTimeout(() => firstAccount.current?.focus(), 0);
    } catch (failure) {
      setError(errorMessage(failure));
    }
  }

  async function submitAccrual() {
    if (!accrualRow || !detail || accrualRow.direction === 'unknown') return;
    const needsTax = (accrualRow.tax_cents ?? 0) > 0;
    if (
      !accrualForm.settlement_account ||
      !accrualForm.profit_account ||
      (needsTax && !accrualForm.tax_account) ||
      !accrualForm.reason.trim()
    ) {
      setError('请选择往来、损益与税额科目，并填写处理依据。');
      return;
    }
    setAccrualBusy(true);
    setError('');
    onBusyChange?.(true);
    try {
      const result = await api<{ created: number; replayed: boolean }>(
        `${base}/${encodeURIComponent(detail.id)}/accruals`,
        session,
        {
          method: 'POST',
          body: {
            items: [
              {
                line: accrualRow.line,
                settlement_account: accrualForm.settlement_account,
                profit_account: accrualForm.profit_account,
                tax_account: accrualForm.tax_account,
              },
            ],
            reason: accrualForm.reason,
          },
        },
      );
      setNotice(
        result.created
          ? '已生成未审核往来挂账草稿，请到凭证复核中核对科目、辅助核算与原始发票。'
          : '该发票已生成过挂账草稿，已刷新当前状态。',
      );
      setAccrualRow(null);
      setDetail(await api<InvoiceBatchDetail>(`${base}/${encodeURIComponent(detail.id)}`, session));
    } catch (failure) {
      setError(errorMessage(failure));
    } finally {
      if (mounted.current) setAccrualBusy(false);
      onBusyChange?.(false);
    }
  }

  const accrualDirection: InvoiceDirection | null =
    accrualRow?.direction === 'input' || accrualRow?.direction === 'output'
      ? accrualRow.direction
      : null;

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
              setPreview(null);
              setError('');
            }}
          />
          <span>{file ? file.name : '选择发票导出表（CSV / XLSX）'}</span>
        </label>
        <button
          className="button primary"
          disabled={busy || !file || !!preview}
          onClick={() => void previewFile()}
        >
          {busy ? <LoaderCircle size={14} className="spinner" /> : <FileSpreadsheet size={14} />}
          预检文件
        </button>
      </div>
      {preview && (
        <section className="invoice-imports-preview" aria-label="发票导入预检结果">
          <div className="invoice-imports-preview-heading">
            <div>
              <strong>导入前预检</strong>
              <span>文件已安全暂存，尚未写入发票批次或产生凭证。</span>
            </div>
            <em>待确认</em>
          </div>
          <div className="invoice-imports-preview-grid">
            <div>
              <span>发票方向</span>
              <strong>{DIRECTION_LABEL[preview.direction] ?? preview.direction}</strong>
            </div>
            <div>
              <span>有效总行数</span>
              <strong>{preview.row_count}</strong>
            </div>
            <div>
              <span>作废 / 红冲</span>
              <strong>
                {preview.void} / {preview.red}
              </strong>
            </div>
            <div>
              <span>重复</span>
              <strong>{preview.duplicates}</strong>
            </div>
            <div>
              <span>银行候选</span>
              <strong>{preview.matched}</strong>
            </div>
            <div>
              <span>可挂账候选</span>
              <strong>{preview.unmatched_eligible}</strong>
            </div>
            <div>
              <span>企业税号</span>
              <strong>
                {preview.company_tax_linked ? `已匹配 ${preview.company_tax_matched} 行` : '未配置'}
              </strong>
            </div>
            <div>
              <span>开票日期</span>
              <strong>
                {preview.date_from || '—'}
                {preview.date_to && preview.date_to !== preview.date_from
                  ? ` 至 ${preview.date_to}`
                  : ''}
              </strong>
            </div>
          </div>
          {preview.unknown_direction > 0 && (
            <p className="invoice-imports-hint">
              有 {preview.unknown_direction} 行无法按企业税号判断进销项；落库后仍需人工核对。
            </p>
          )}
          {preview.skipped > 0 && (
            <p className="invoice-imports-hint">跳过 {preview.skipped} 行缺少发票号码的记录。</p>
          )}
          {preview.warnings.length > 0 && (
            <details className="invoice-imports-warnings">
              <summary>预检提示（{preview.warnings.length}）</summary>
              <ul>
                {preview.warnings.slice(0, 50).map((warning) => (
                  <li key={warning}>{warning}</li>
                ))}
              </ul>
            </details>
          )}
          <div className="invoice-imports-preview-actions">
            <button
              type="button"
              className="text-button"
              disabled={busy}
              onClick={() => {
                setPreview(null);
                setFile(null);
              }}
            >
              重新选择
            </button>
            <button
              type="button"
              className="button primary"
              disabled={busy}
              onClick={() => void confirmImport()}
            >
              {busy && <LoaderCircle size={14} className="spinner" />}
              确认导入并勾稽
            </button>
          </div>
        </section>
      )}
      {items && !items.length && !detail && (
        <p className="invoice-imports-empty">还没有导入过发票导出表。</p>
      )}
      {items && items.length > 0 && (
        <ul className="invoice-imports-list">
          {items.map((item) => (
            <li key={item.id}>
              <button
                type="button"
                disabled={busy}
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
                  <th>往来挂账</th>
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
                    <td>
                      {row.accrual ? (
                        <span className="invoice-imports-tag accrual">
                          {ACCRUAL_LABEL[row.accrual.status] ?? row.accrual.status}
                        </span>
                      ) : invoiceAccrualEligible(row) ? (
                        <button
                          type="button"
                          className="text-button invoice-imports-accrual-button"
                          disabled={busy || accrualBusy}
                          onClick={() => void openAccrual(row)}
                        >
                          生成挂账草稿
                        </button>
                      ) : (
                        '—'
                      )}
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
      {accrualRow && accrualDirection && (
        <div
          className="invoice-accrual-backdrop"
          onMouseDown={(event) => {
            if (event.target === event.currentTarget && !accrualBusy) setAccrualRow(null);
          }}
        >
          <section
            className="invoice-accrual-dialog"
            role="dialog"
            aria-modal="true"
            aria-labelledby="invoice-accrual-title"
          >
            <header>
              <div>
                <span>{accrualDirection === 'input' ? '进项发票' : '销项发票'}</span>
                <h3 id="invoice-accrual-title">生成往来挂账草稿</h3>
                <p>
                  发票 {accrualRow.number} · {money(accrualRow.total_cents)}
                </p>
              </div>
              <em>仅生成草稿</em>
            </header>
            <div className="invoice-accrual-summary">
              <span>
                不含税金额 <strong>{money(accrualRow.amount_cents)}</strong>
              </span>
              <span>
                税额 <strong>{money(accrualRow.tax_cents)}</strong>
              </span>
              <span>
                往来方{' '}
                <strong>
                  {(accrualDirection === 'input' ? accrualRow.seller : accrualRow.buyer) || '—'}
                </strong>
              </span>
            </div>
            <form
              className="invoice-accrual-form"
              onSubmit={(event) => {
                event.preventDefault();
                void submitAccrual();
              }}
            >
              <label>
                <span>{accrualDirection === 'input' ? '应付账款科目' : '应收账款科目'}</span>
                <select
                  ref={firstAccount}
                  value={accrualForm.settlement_account}
                  disabled={accrualBusy}
                  onChange={(event) =>
                    setAccrualForm((current) => ({
                      ...current,
                      settlement_account: event.target.value,
                    }))
                  }
                >
                  <option value="">请选择科目</option>
                  {optionsFor('settlement').map((account) => (
                    <option key={account.code} value={account.code}>
                      {account.code} {account.name}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                <span>{accrualDirection === 'input' ? '成本 / 费用科目' : '收入科目'}</span>
                <select
                  value={accrualForm.profit_account}
                  disabled={accrualBusy}
                  onChange={(event) =>
                    setAccrualForm((current) => ({
                      ...current,
                      profit_account: event.target.value,
                    }))
                  }
                >
                  <option value="">请选择科目</option>
                  {optionsFor('profit').map((account) => (
                    <option key={account.code} value={account.code}>
                      {account.code} {account.name}
                    </option>
                  ))}
                </select>
              </label>
              {(accrualRow.tax_cents ?? 0) > 0 && (
                <label>
                  <span>{accrualDirection === 'input' ? '进项税额科目' : '销项税额科目'}</span>
                  <select
                    value={accrualForm.tax_account}
                    disabled={accrualBusy}
                    onChange={(event) =>
                      setAccrualForm((current) => ({
                        ...current,
                        tax_account: event.target.value,
                      }))
                    }
                  >
                    <option value="">请选择科目</option>
                    {optionsFor('tax').map((account) => (
                      <option key={account.code} value={account.code}>
                        {account.code} {account.name}
                      </option>
                    ))}
                  </select>
                </label>
              )}
              <label className="invoice-accrual-reason">
                <span>处理依据</span>
                <textarea
                  rows={3}
                  maxLength={500}
                  value={accrualForm.reason}
                  disabled={accrualBusy}
                  placeholder="例如：发票已开具/认证，本月尚未收付款，按权责发生制挂往来。"
                  onChange={(event) =>
                    setAccrualForm((current) => ({ ...current, reason: event.target.value }))
                  }
                />
              </label>
              {accounts?.some(
                (account) =>
                  [
                    accrualForm.settlement_account,
                    accrualForm.profit_account,
                    accrualForm.tax_account,
                  ].includes(account.code) && account.auxiliary_requirements.length > 0,
              ) && (
                <p className="invoice-imports-hint">
                  所选科目需要辅助核算；草稿生成后须在凭证复核中补全往来、项目或人员。
                </p>
              )}
              <p className="invoice-accrual-note">
                系统只按发票金额和所选科目生成借贷平衡草稿，不会自动审核或导出；会计仍需按账套政策复核。
              </p>
              <div className="invoice-accrual-actions">
                <button
                  type="button"
                  className="text-button"
                  disabled={accrualBusy}
                  onClick={() => setAccrualRow(null)}
                >
                  取消
                </button>
                <button type="submit" className="button primary" disabled={accrualBusy}>
                  {accrualBusy && <LoaderCircle size={14} className="spinner" />}
                  生成挂账草稿
                </button>
              </div>
            </form>
          </section>
        </div>
      )}
    </section>
  );
}
