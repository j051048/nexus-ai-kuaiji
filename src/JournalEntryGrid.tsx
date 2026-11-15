import { Fragment, useEffect, useRef, useState } from 'react';
import { Trash2 } from 'lucide-react';
import type { BookDetail, JournalLine } from './types';
import { isCompositionKey } from './keyboard';
import './journal-grid.css';

const auxiliaryLabels = {
  department: '部门',
  employee: '人员',
  counterparty: '交易对方',
  project: '项目',
};
type AuxiliaryKey = keyof typeof auxiliaryLabels;
function auxiliaryKeys(line: JournalLine, accounts: BookDetail['accounts']): AuxiliaryKey[] {
  let required: unknown = [];
  try {
    required = JSON.parse(
      accounts.find((account) => account.code === line.account_code)?.auxiliary_requirements ||
        '[]',
    );
  } catch {
    /* API validation still checks the complete accounting definition. */
  }
  return [
    ...new Set([
      ...(Array.isArray(required) ? required : []),
      ...Object.keys(line.auxiliary_values ?? {}),
    ]),
  ].filter((key): key is AuxiliaryKey => typeof key === 'string' && key in auxiliaryLabels);
}

/** A compact accounting table on desktop, stacked accessible rows on phones.
 * Amounts remain decimal strings; this component never computes accounting. */
export function JournalEntryGrid({
  lines,
  accounts,
  readonly,
  busy,
  mode = 'standard',
  pageSize = 50,
  onChange,
}: {
  lines: JournalLine[];
  accounts: BookDetail['accounts'];
  readonly: boolean;
  busy: boolean;
  mode?: 'standard' | 'inline';
  /** Bound the rendered rows for controlled large vouchers; 0 renders all lines. */
  pageSize?: number;
  onChange: (lines: JournalLine[]) => void;
}) {
  const size = Math.max(1, pageSize || lines.length);
  const [page, setPage] = useState(0),
    [jump, setJump] = useState(''),
    [density, setDensity] = useState<'comfortable' | 'compact'>(() =>
      typeof localStorage !== 'undefined' && localStorage.getItem('journal-grid-density') === 'compact'
        ? 'compact'
        : 'comfortable',
    ),
    [widthMode, setWidthMode] = useState<'standard' | 'account' | 'amount'>(() => {
      const saved = typeof localStorage !== 'undefined' ? localStorage.getItem('journal-grid-width') : null;
      return saved === 'account' || saved === 'amount' ? saved : 'standard';
    });
  useEffect(() => {
    localStorage.setItem('journal-grid-density', density);
  }, [density]);
  useEffect(() => {
    localStorage.setItem('journal-grid-width', widthMode);
  }, [widthMode]);
  const table = useRef<HTMLTableElement>(null);
  const pageCount = Math.max(1, Math.ceil(lines.length / size));
  const current = Math.min(page, pageCount - 1);
  const start = current * size,
    end = Math.min(lines.length, start + size),
    visible = lines.slice(start, end);
  useEffect(() => {
    if (page > pageCount - 1) setPage(pageCount - 1);
  }, [page, pageCount]);
  function goToPage(target: number) {
    setPage(Math.min(pageCount - 1, Math.max(0, target)));
    requestAnimationFrame(() => {
      table.current?.scrollIntoView({ block: 'start', inline: 'nearest' });
    });
  }
  function locate() {
    const target = Number.parseInt(jump, 10);
    if (!Number.isFinite(target)) return;
    const index = Math.min(lines.length, Math.max(1, target)) - 1;
    goToPage(Math.floor(index / size));
    setJump('');
  }
  function update(index: number, field: 'account_code' | 'debit' | 'credit', value: string) {
    onChange(
      lines.map((line, position) => (position === index ? { ...line, [field]: value } : line)),
    );
  }
  function updateAuxiliary(index: number, key: AuxiliaryKey, value: string) {
    onChange(
      lines.map((line, position) => {
        if (position !== index) return line;
        const values = { ...line.auxiliary_values };
        if (value) values[key] = value;
        else delete values[key];
        return { ...line, auxiliary_values: values };
      }),
    );
  }
  const amount = (value: string | number | null | undefined) => {
    const parsed = typeof value === 'number' ? value : Number.parseFloat(value ?? '0');
    return Number.isFinite(parsed) ? parsed : 0;
  };
  const sum = (rows: JournalLine[], field: 'debit' | 'credit') =>
    rows.reduce((total, line) => total + amount(line[field]), 0);
  const money = (value: number) =>
    value.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const pageDebit = sum(visible, 'debit'),
    pageCredit = sum(visible, 'credit'),
    allDebit = sum(lines, 'debit'),
    allCredit = sum(lines, 'credit');
  const label = (index: number, field: 'account' | 'debit' | 'credit' | 'delete') =>
    mode === 'inline'
      ? `同屏${field === 'delete' ? '删除' : ''}第 ${index + 1} 行${{ account: '科目', debit: '借方', credit: '贷方', delete: '' }[field]}`
      : field === 'delete'
        ? `删除第 ${index + 1} 行分录`
        : `第 ${index + 1} 行${{ account: '会计科目', debit: '借方金额', credit: '贷方金额', delete: '' }[field]}`;
  return (
    <div className={'journal-grid-wrap' + (readonly ? ' journal-grid-readonly' : '')}>
      <div className="journal-grid-toolbar">
        <span>每页 {size} 行，当前第 {current + 1} / {pageCount} 页</span>
        <label className="journal-grid-width">
          列宽
          <select
            aria-label="凭证列宽"
            value={widthMode}
            onChange={(event) => setWidthMode(event.target.value as 'standard' | 'account' | 'amount')}
          >
            <option value="standard">标准</option>
            <option value="account">宽科目</option>
            <option value="amount">宽金额</option>
          </select>
        </label>
        <button
          type="button"
          className="text-button"
          aria-pressed={density === 'compact'}
          onClick={() => setDensity((value) => (value === 'compact' ? 'comfortable' : 'compact'))}
        >
          {density === 'compact' ? '舒适行距' : '紧凑行距'}
        </button>
      </div>
      <table
        ref={table}
        className={`journal-entry-grid density-${density} width-${widthMode}`}
        aria-label={mode === 'inline' ? '同屏凭证分录表' : '凭证分录表'}
      >
        <thead>
          <tr>
            <th scope="col" className="journal-grid-index">
              行
            </th>
            <th scope="col">会计科目</th>
            <th scope="col" className="journal-grid-amount">
              借方
              <span className="journal-grid-unit">（元）</span>
            </th>
            <th scope="col" className="journal-grid-amount">
              贷方
              <span className="journal-grid-unit">（元）</span>
            </th>
            <th scope="col" className="journal-grid-action">
              <span className="sr-only">操作</span>
            </th>
          </tr>
        </thead>
        <tbody>
          {visible.map((line, offset) => {
            const index = start + offset;
            return (
              <Fragment key={index}>
                <tr className="journal-grid-line">
                  <th scope="row" className="journal-grid-index">
                    {String(index + 1).padStart(2, '0')}
                  </th>
                  <td className="journal-grid-account" data-label="会计科目">
                    <select
                      aria-label={label(index, 'account')}
                      value={line.account_code}
                      disabled={readonly || busy}
                      onChange={(event) => update(index, 'account_code', event.target.value)}
                    >
                      <option value="">选择会计科目</option>
                      {accounts.map((account) => (
                        <option key={account.code} value={account.code}>
                          {account.code} · {account.name}
                        </option>
                      ))}
                    </select>
                  </td>
                  <td className="journal-grid-amount" data-label="借方">
                    <input
                      aria-label={label(index, 'debit')}
                      inputMode="decimal"
                      value={line.debit}
                      disabled={readonly || busy}
                      onChange={(event) => update(index, 'debit', event.target.value)}
                      onBlur={() => {
                        if (line.debit === '') update(index, 'debit', '0.00');
                      }}
                    />
                  </td>
                  <td className="journal-grid-amount" data-label="贷方">
                    <input
                      aria-label={label(index, 'credit')}
                      inputMode="decimal"
                      value={line.credit}
                      disabled={readonly || busy}
                      onChange={(event) => update(index, 'credit', event.target.value)}
                      onBlur={() => {
                        if (line.credit === '') update(index, 'credit', '0.00');
                      }}
                    />
                  </td>
                  <td className="journal-grid-action">
                    <button
                      type="button"
                      className="icon-button"
                      aria-label={label(index, 'delete')}
                      disabled={readonly || busy || lines.length <= 1}
                      onClick={() => onChange(lines.filter((_, position) => position !== index))}
                    >
                      <Trash2 size={14} />
                    </button>
                  </td>
                </tr>
                {auxiliaryKeys(line, accounts).length > 0 && (
                  <tr className="journal-grid-auxiliary">
                    <td colSpan={5}>
                      <div>
                        {auxiliaryKeys(line, accounts).map((key) => (
                          <label key={key}>
                            <span>{auxiliaryLabels[key]}</span>
                            <input
                              aria-label={
                                mode === 'inline'
                                  ? `同屏第 ${index + 1} 行${key}辅助核算`
                                  : `第 ${index + 1} 行${key}辅助核算`
                              }
                              maxLength={200}
                              value={line.auxiliary_values?.[key] ?? ''}
                              disabled={readonly || busy}
                              onChange={(event) => updateAuxiliary(index, key, event.target.value)}
                            />
                          </label>
                        ))}
                      </div>
                    </td>
                  </tr>
                )}
              </Fragment>
            );
          })}
        </tbody>
      </table>
      <div className="journal-grid-totals" data-testid="journal-totals">
        <span>本页 借 ¥{money(pageDebit)} · 贷 ¥{money(pageCredit)}</span>
        <span>全量 借 ¥{money(allDebit)} · 贷 ¥{money(allCredit)}</span>
      </div>
      {pageCount > 1 && (
        <div className="journal-grid-pager" role="group" aria-label="凭证分录分页">
          <span aria-live="polite">
            第 {start + 1}–{end} 行 / 共 {lines.length} 行
          </span>
          <div className="journal-grid-pager-actions">
            <button
              type="button"
              className="button secondary small"
              aria-label="上一页分录"
              disabled={current <= 0 || busy}
              onClick={() => goToPage(current - 1)}
            >
              上一页
            </button>
            <span>
              第 {current + 1} / {pageCount} 页
            </span>
            <button
              type="button"
              className="button secondary small"
              aria-label="下一页分录"
              disabled={current >= pageCount - 1 || busy}
              onClick={() => goToPage(current + 1)}
            >
              下一页
            </button>
          </div>
          <label className="journal-grid-locate">
            <span>定位行</span>
            <input
              aria-label="定位分录行号"
              type="number"
              min={1}
              max={lines.length}
              inputMode="numeric"
              value={jump}
              disabled={busy}
              onFocus={(event) => event.currentTarget.scrollIntoView({ block: 'center' })}
              onChange={(event) => setJump(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Enter' && !isCompositionKey(event)) {
                  event.preventDefault();
                  locate();
                }
              }}
            />
            <button
              type="button"
              className="button secondary small"
              disabled={busy || !jump}
              onClick={locate}
            >
              定位
            </button>
          </label>
        </div>
      )}
    </div>
  );
}
