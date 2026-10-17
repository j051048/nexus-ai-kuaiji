import { useEffect, useRef, useState } from 'react';
import {
  ArrowLeft,
  ArrowRight,
  CheckCircle2,
  Download,
  FileSpreadsheet,
  LoaderCircle,
  ShieldCheck,
  Upload,
  X,
} from 'lucide-react';
import { api, errorMessage } from './api';
import { downloadCsv, formatMoney } from './csv';
import type { Book, Session } from './types';
import type {
  ImportFileInfo,
  ImportMapping,
  ImportPreview,
  ImportProfile,
  ImportReceipt,
} from './import-wizard-types';
import {
  IMPORT_FIELD_LABELS,
  IMPORT_TEMPLATE,
  changeAmountMode,
  compatibleProfile,
  mappingError,
  mappingFields,
} from './import-wizard-helpers';
import { uploadLimitLabel, uploadSizeError } from './transport-limits';
import { attachmentForm, prepareDirectUpload } from './upload-client';
import './import-wizard.css';

export default function ChineseImportWizard({
  session,
  book,
  onClose,
  onImported,
  onBusyChange,
}: {
  session: Session;
  book: Book;
  onClose: () => void;
  onImported: (result: ImportReceipt) => void | Promise<void>;
  onBusyChange?: (busy: boolean) => void;
}) {
  const [step, setStep] = useState<1 | 2 | 3>(1);
  const [file, setFile] = useState<ImportFileInfo | null>(null);
  const [paste, setPaste] = useState('');
  const [profiles, setProfiles] = useState<ImportProfile[]>([]);
  const [namespace, setNamespace] = useState('bank-statement');
  const [bank, setBank] = useState('');
  const [mapping, setMapping] = useState<ImportMapping>({
    columns: {},
    date_format: 'ymd',
    amount_mode: 'direction',
    allow_grouping: false,
  });
  const [preview, setPreview] = useState<ImportPreview | null>(null);
  const [remember, setRemember] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [page, setPage] = useState(0);
  const [errorsOnly, setErrorsOnly] = useState(false);
  const [confirmed, setConfirmed] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const dialog = useRef<HTMLDivElement>(null);
  const mounted = useRef(true);
  const active = useRef(false);
  const busyCallback = useRef(onBusyChange);
  busyCallback.current = onBusyChange;
  const endpoint = `/books/${encodeURIComponent(book.id)}/import-wizard`;

  useEffect(() => {
    mounted.current = true;
    const controller = new AbortController();
    const oldFocus = document.activeElement as HTMLElement | null;
    const originalBodyOverflow = document.body.style.overflow;
    const originalDocumentOverflow = document.documentElement.style.overflow;
    document.body.style.overflow = 'hidden';
    document.documentElement.style.overflow = 'hidden';
    dialog.current?.focus();
    void api<{ profiles: ImportProfile[] }>(`${endpoint}/profiles`, session, {
      signal: controller.signal,
    })
      .then((value) => {
        if (!controller.signal.aborted) setProfiles(value.profiles);
      })
      .catch((value) => {
        if (!controller.signal.aborted) setError(errorMessage(value));
      });
    return () => {
      mounted.current = false;
      controller.abort();
      document.body.style.overflow = originalBodyOverflow;
      document.documentElement.style.overflow = originalDocumentOverflow;
      busyCallback.current?.(false);
      if (oldFocus?.isConnected) oldFocus.focus({ preventScroll: true });
    };
  }, [endpoint, session]);

  useEffect(() => {
    onBusyChange?.(busy);
  }, [busy, onBusyChange]);

  async function perform(action: () => Promise<void>) {
    if (active.current) return;
    active.current = true;
    setBusy(true);
    setError('');
    try {
      await action();
    } catch (value) {
      if (mounted.current) setError(errorMessage(value));
    } finally {
      active.current = false;
      if (mounted.current) setBusy(false);
    }
  }

  function inspected(value: ImportFileInfo) {
    setFile(value);
    setPreview(null);
    setConfirmed(false);
    const mode =
      value.suggested_columns.income !== undefined && value.suggested_columns.expense !== undefined
        ? 'split'
        : 'direction';
    setMapping(
      changeAmountMode(
        {
          columns: value.suggested_columns,
          date_format: 'ymd',
          amount_mode: mode,
          allow_grouping: false,
        },
        mode,
      ),
    );
  }

  async function upload(selected?: File) {
    if (!selected) return;
    setFile(null);
    setPreview(null);
    setConfirmed(false);
    setPaste('');
    await perform(async () => {
      const invalidSize = uploadSizeError(selected.size, 10 * 1024 * 1024);
      if (invalidSize) throw new Error(invalidSize);
      const direct = await prepareDirectUpload(session, selected, 'bank_import');
      const body = attachmentForm(selected, direct);
      const value = await api<ImportFileInfo>(`${endpoint}/files`, session, {
        method: 'POST',
        body,
      });
      if (mounted.current) inspected(value);
    });
    if (input.current) input.current.value = '';
  }

  function chooseProfile(id: string) {
    const profile = profiles.find((value) => value.id === id);
    if (!profile) return;
    setNamespace(profile.namespace);
    setBank(profile.bank_account);
    setPreview(null);
    if (file && compatibleProfile(profile, file.headers)) {
      setMapping(profile.mapping);
      setError('');
    } else setError('已选择账号。当前文件表头与保存格式不同，请重新确认字段对应。');
  }

  async function makePreview() {
    if (!file) return;
    const message = mappingError(mapping, file.headers);
    if (message) {
      setError(message);
      return;
    }
    await perform(async () => {
      const value = await api<ImportPreview>(`${endpoint}/previews`, session, {
        method: 'POST',
        body: {
          file_id: file.file_id,
          namespace: namespace.trim(),
          bank_account: bank.trim(),
          mapping,
        },
      });
      if (mounted.current) {
        setPreview(value);
        setStep(3);
        setPage(0);
        setErrorsOnly(false);
        setConfirmed(false);
      }
    });
  }

  const displayRows = preview?.rows.filter((row) => !errorsOnly || row.errors.length > 0) ?? [];
  const maxPage = Math.max(0, Math.ceil(displayRows.length / 20) - 1);
  const visible = displayRows.slice(page * 20, page * 20 + 20);
  function keydown(event: React.KeyboardEvent<HTMLDivElement>) {
    if (event.key === 'Escape') {
      event.preventDefault();
      event.stopPropagation();
      if (!busy) onClose();
      return;
    }
    if (event.key === 'Tab') {
      const nodes = dialog.current?.querySelectorAll<HTMLElement>(
        'button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex="0"]',
      );
      if (!nodes?.length) return;
      const first = nodes[0],
        last = nodes[nodes.length - 1];
      if (
        event.shiftKey &&
        (document.activeElement === first || document.activeElement === dialog.current)
      ) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    }
  }

  return (
    <div className="modal-backdrop import-wizard-backdrop">
      <div
        className="import-wizard-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="import-wizard-title"
        tabIndex={-1}
        ref={dialog}
        onKeyDown={keydown}
      >
        <header className="import-wizard-header">
          <div>
            <span className="eyebrow">导入工作台</span>
            <h2 id="import-wizard-title">导入原始流水</h2>
            <p>
              {book.company_name ?? book.name} · {book.period} · 人民币
            </p>
          </div>
          <button
            type="button"
            className="icon-button"
            aria-label="关闭导入向导"
            disabled={busy}
            onClick={onClose}
          >
            <X size={20} />
          </button>
        </header>
        <ol className="import-wizard-steps" aria-label="导入步骤">
          {['选择文件与账号', '确认字段对应', '核对并导入'].map((label, i) => (
            <li
              key={label}
              aria-current={step === i + 1 ? 'step' : undefined}
              className={step > i + 1 ? 'complete' : step === i + 1 ? 'current' : ''}
            >
              <span>{step > i + 1 ? <CheckCircle2 size={16} /> : i + 1}</span>
              {label}
            </li>
          ))}
        </ol>
        <main className="import-wizard-main">
          {error && (
            <p className="form-error" role="alert">
              {error}
            </p>
          )}
          {step === 1 && (
            <>
              <div className="import-wizard-title-row">
                <div>
                  <h3>把银行文件交给系统</h3>
                  <p>
                    支持UTF-8 / GBK CSV和单工作表Excel，最大 {uploadLimitLabel(10 * 1024 * 1024)}
                    、5,000行；超过 2 MB 的文件会自动改用大文件直传。
                  </p>
                </div>
                <button
                  type="button"
                  className="text-button"
                  onClick={() =>
                    downloadCsv(IMPORT_TEMPLATE.replace(/PERIOD/g, book.period), '中文流水模板.csv')
                  }
                >
                  <Download size={15} />
                  下载中文模板
                </button>
              </div>
              <input
                ref={input}
                type="file"
                accept=".csv,.xlsx"
                hidden
                aria-label="选择银行流水文件"
                disabled={busy}
                onChange={(event) => void upload(event.target.files?.[0])}
              />
              <button
                type="button"
                className="import-wizard-upload"
                disabled={busy}
                onClick={() => input.current?.click()}
              >
                {busy ? (
                  <LoaderCircle className="spinner" size={26} />
                ) : (
                  <FileSpreadsheet size={30} />
                )}
                <strong>{file?.filename ?? '选择CSV或Excel文件'}</strong>
                <span>
                  {file
                    ? `${file.row_count}条流水 · ${file.encoding}`
                    : '直接使用中文表头，下一步确认字段对应'}
                </span>
              </button>
              <details className="import-wizard-paste">
                <summary>或直接粘贴中文 / 原英文CSV</summary>
                <label htmlFor="csv-text">粘贴流水内容（含表头）</label>
                <textarea
                  id="csv-text"
                  rows={5}
                  value={paste}
                  onChange={(event) => {
                    setPaste(event.target.value);
                    setFile(null);
                    setPreview(null);
                    setConfirmed(false);
                  }}
                  disabled={busy}
                  placeholder="交易编号,交易日期,摘要,交易对方,金额,方向"
                />
                <button
                  type="button"
                  className="button secondary"
                  disabled={busy || !paste.trim()}
                  onClick={() =>
                    void perform(async () => {
                      const value = await api<ImportFileInfo>(`${endpoint}/paste`, session, {
                        method: 'POST',
                        body: { filename: '粘贴流水.csv', content: paste },
                      });
                      if (mounted.current) inspected(value);
                    })
                  }
                >
                  识别粘贴内容
                </button>
              </details>
              <div className="import-wizard-scope">
                <h3>固定来源与银行账号</h3>
                <p>同一个银行账号每次使用相同标识，交易号才能持续防重。</p>
                {profiles.length > 0 && (
                  <label>
                    使用保存的格式
                    <select
                      aria-label="使用保存的银行格式"
                      defaultValue=""
                      disabled={busy}
                      onChange={(event) => chooseProfile(event.target.value)}
                    >
                      <option value="">选择当前账套保存的格式</option>
                      {profiles.map((profile) => (
                        <option key={profile.id} value={profile.id}>
                          {profile.bank_account} · {profile.namespace} · v{profile.version}
                        </option>
                      ))}
                    </select>
                  </label>
                )}
                <div className="import-wizard-fields">
                  <label>
                    固定来源标识
                    <input
                      aria-label="固定来源标识"
                      value={namespace}
                      maxLength={100}
                      disabled={busy}
                      onChange={(event) => {
                        setNamespace(event.target.value);
                        setPreview(null);
                      }}
                      placeholder="例如 bank-statement"
                    />
                  </label>
                  <label>
                    银行账号标识（脱敏）
                    <input
                      aria-label="银行账号标识（脱敏）"
                      value={bank}
                      maxLength={100}
                      disabled={busy}
                      onChange={(event) => {
                        setBank(event.target.value);
                        setPreview(null);
                      }}
                      placeholder="例如 工商银行-末四位1234"
                    />
                  </label>
                </div>
              </div>
            </>
          )}
          {step === 2 && file && (
            <>
              <div className="import-wizard-title-row">
                <div>
                  <h3>确认每一列的含义</h3>
                  <p>系统只建议字段对应，金额、日期和方向由下面的确定规则转换。</p>
                </div>
                <span className="status-pill">
                  {file.row_count}条 · {file.headers.length}列
                </span>
              </div>
              <div className="import-wizard-fields">
                <label>
                  金额与收支模式
                  <select
                    aria-label="金额与收支模式"
                    disabled={busy}
                    value={mapping.amount_mode}
                    onChange={(event) =>
                      setMapping(
                        changeAmountMode(
                          mapping,
                          event.target.value as ImportMapping['amount_mode'],
                        ),
                      )
                    }
                  >
                    <option value="direction">正数金额 + 收入/支出列</option>
                    <option value="split">收入金额与支出金额分列</option>
                    <option value="income_positive">带正负金额：正数收入、负数支出</option>
                    <option value="expense_positive">带正负金额：正数支出、负数收入</option>
                  </select>
                </label>
                <label>
                  原表日期格式
                  <select
                    aria-label="原表日期格式"
                    value={mapping.date_format}
                    disabled={busy}
                    onChange={(event) =>
                      setMapping({
                        ...mapping,
                        date_format: event.target.value as ImportMapping['date_format'],
                      })
                    }
                  >
                    <option value="ymd">年-月-日（2026/10/01）</option>
                    <option value="dmy">日/月/年（01/10/2026）</option>
                    <option value="mdy">月/日/年（10/01/2026）</option>
                    <option value="excel_serial">Excel日期序号（整数）</option>
                  </select>
                </label>
              </div>
              <label className="import-wizard-check">
                <input
                  type="checkbox"
                  checked={mapping.allow_grouping}
                  disabled={busy}
                  onChange={(event) =>
                    setMapping({ ...mapping, allow_grouping: event.target.checked })
                  }
                />
                原表金额的逗号表示千位分隔符（1,234.56）
              </label>
              <div className="import-wizard-mapping">
                {mappingFields(mapping.amount_mode).map((field) => (
                  <label key={field}>
                    <span>
                      {IMPORT_FIELD_LABELS[field]}
                      {['date', 'description', 'amount', 'direction', 'income', 'expense'].includes(
                        field,
                      ) && <i>必选</i>}
                    </span>
                    <select
                      aria-label={`对应${IMPORT_FIELD_LABELS[field]}`}
                      disabled={busy}
                      value={mapping.columns[field] ?? ''}
                      onChange={(event) => {
                        const columns = { ...mapping.columns };
                        if (event.target.value === '') delete columns[field];
                        else columns[field] = Number(event.target.value);
                        setMapping({ ...mapping, columns });
                      }}
                    >
                      <option value="">不对应此字段</option>
                      {file.headers.map((header, i) => (
                        <option key={i} value={i}>
                          {header} · 第{i + 1}列
                        </option>
                      ))}
                    </select>
                    <small>
                      {mapping.columns[field] !== undefined
                        ? `示例：${file.sample_rows[0]?.[mapping.columns[field]!] || '空值'}`
                        : field === 'external_id' || field === 'stable_transaction_id'
                          ? '无交易号时生成文件行标识，跨文件仍需人工辨别重复'
                          : '选择原表对应的列'}
                    </small>
                  </label>
                ))}
              </div>
              <p className="field-help">
                原始文件与每行转换依据都会保留。借/贷标志不直接推断收入支出；公式单元格须先转成数值。
              </p>
            </>
          )}
          {step === 3 && preview && file && (
            <>
              <div className="import-wizard-title-row">
                <div>
                  <h3>核对本次导入</h3>
                  <p>
                    {preview.company_name} · {preview.period} · {preview.bank_account}
                  </p>
                </div>
                <span className="status-pill">{file.filename}</span>
              </div>
              <div className="import-wizard-totals">
                <div>
                  <span>新增流水</span>
                  <strong>
                    {preview.new_count}
                    <small>条</small>
                  </strong>
                </div>
                <div>
                  <span>重复将跳过</span>
                  <strong>
                    {preview.duplicate_count}
                    <small>条</small>
                  </strong>
                </div>
                <div>
                  <span>新增收入合计</span>
                  <strong>{formatMoney(preview.totals.income)}</strong>
                </div>
                <div>
                  <span>新增支出合计</span>
                  <strong>{formatMoney(preview.totals.expense)}</strong>
                </div>
              </div>
              {preview.error_count > 0 && (
                <p className="form-error" role="alert">
                  {preview.error_count}行有错误。请返回确认字段或修改原文件后重传，整批不会导入。
                </p>
              )}
              <div className="import-wizard-list-head">
                <h4>逐行预览</h4>
                <label className="import-wizard-check">
                  <input
                    type="checkbox"
                    checked={errorsOnly}
                    onChange={(event) => {
                      setErrorsOnly(event.target.checked);
                      setPage(0);
                    }}
                  />
                  只看错误
                </label>
              </div>
              <div className="import-wizard-rows">
                {visible.map((row) => (
                  <article
                    className={`import-wizard-row ${row.errors.length ? 'invalid' : ''}`}
                    key={row.line}
                  >
                    <div className="import-wizard-row-top">
                      <span>
                        原表第{row.original_position ?? row.line}行 ·{' '}
                        {row.status === 'new'
                          ? '待导入'
                          : row.status === 'duplicate'
                            ? '重复跳过'
                            : '需修正'}
                      </span>
                      <strong>
                        {row.source
                          ? `${row.source.direction === 'income' ? '收入' : '支出'} ${formatMoney(row.source.amount)}`
                          : '无法转换'}
                      </strong>
                    </div>
                    <h4>{row.source?.description ?? '请检查此行'}</h4>
                    <p>
                      {row.source?.date} · {row.source?.counterparty || '未填交易对方'}
                    </p>
                    {row.errors.map((text, i) => (
                      <p key={i} className="form-error">
                        {text}
                      </p>
                    ))}
                    {row.warnings.map((text, i) => (
                      <p key={i} className="import-wizard-warning">
                        {text}
                      </p>
                    ))}
                    <details>
                      <summary>查看原始行与交易号</summary>
                      <dl>
                        {file.headers.map((header, i) => (
                          <div key={i}>
                            <dt>{header}</dt>
                            <dd>{row.raw[i] || '空值'}</dd>
                          </div>
                        ))}
                      </dl>
                      <p>采用交易号：{row.source?.stable_transaction_id ?? '未生成'}</p>
                    </details>
                  </article>
                ))}
              </div>
              {!displayRows.length && <p className="field-help">当前筛选没有流水。</p>}
              {maxPage > 0 && (
                <div className="import-wizard-pager">
                  <button
                    className="button secondary"
                    type="button"
                    disabled={page === 0 || busy}
                    onClick={() => setPage(page - 1)}
                  >
                    上一页
                  </button>
                  <span>
                    第{page + 1} / {maxPage + 1}页
                  </span>
                  <button
                    className="button secondary"
                    type="button"
                    disabled={page >= maxPage || busy}
                    onClick={() => setPage(page + 1)}
                  >
                    下一页
                  </button>
                </div>
              )}
              <div className="import-wizard-commit">
                <label className="import-wizard-check">
                  <input
                    type="checkbox"
                    checked={remember}
                    disabled={busy}
                    onChange={(event) => setRemember(event.target.checked)}
                  />
                  为此账套和银行账号保存字段格式
                </label>
                <label className="import-wizard-check">
                  <input
                    type="checkbox"
                    checked={confirmed}
                    disabled={busy || preview.error_count > 0}
                    onChange={(event) => setConfirmed(event.target.checked)}
                  />
                  我已核对目标企业、月份、账号、字段和收入支出合计
                </label>
                <p>
                  <ShieldCheck size={15} />
                  导入保留原始来源，生成分录后仍须会计审核才能导出。
                </p>
              </div>
            </>
          )}
        </main>
        <footer className="import-wizard-footer">
          <button
            type="button"
            className="button secondary"
            disabled={busy}
            onClick={() => {
              if (step === 1) onClose();
              else {
                setStep(step === 3 ? 2 : 1);
                setError('');
              }
            }}
          >
            {step === 1 ? (
              '取消'
            ) : (
              <>
                <ArrowLeft size={15} />
                上一步
              </>
            )}
          </button>
          {step === 1 && (
            <button
              type="button"
              className="button primary"
              disabled={busy || !file || !namespace.trim() || !bank.trim()}
              onClick={() => {
                setStep(2);
                setError('');
              }}
            >
              确认字段
              <ArrowRight size={15} />
            </button>
          )}
          {step === 2 && (
            <button
              type="button"
              className="button primary"
              disabled={busy}
              onClick={() => void makePreview()}
            >
              {busy ? <LoaderCircle className="spinner" size={15} /> : <ArrowRight size={15} />}
              预览流水
            </button>
          )}
          {step === 3 && preview && (
            <button
              type="button"
              className="button primary"
              disabled={busy || !confirmed || preview.error_count > 0}
              onClick={() =>
                void perform(async () => {
                  const result = await api<ImportReceipt>(
                    `${endpoint}/previews/${encodeURIComponent(preview.id)}/confirm`,
                    session,
                    { method: 'POST', body: { remember_mapping: remember } },
                  );
                  await onImported(result);
                })
              }
            >
              {busy ? <LoaderCircle className="spinner" size={15} /> : <Upload size={15} />}确认导入
            </button>
          )}
        </footer>
      </div>
    </div>
  );
}
