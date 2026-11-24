import { Fragment, useEffect, useRef, useState } from 'react';
import { Trash2 } from 'lucide-react';
import type { BookDetail, JournalLine } from './types';
import { isCompositionKey } from './keyboard';
import { AccountPicker } from './AccountPicker';
import { MoneyCell } from './MoneyCell';
import './account-picker.css';
import { parseYuanToCents, sumCents } from './money';
import { balanceLineWithEquals, journalBalanceSummary, smartBalanceLines } from './journal-balance';
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
  onAddLine,
  onSaveShortcut,
}: {
  lines: JournalLine[];
  accounts: BookDetail['accounts'];
  readonly: boolean;
  busy: boolean;
  mode?: 'standard' | 'inline';
  /** Bound the rendered rows for controlled large vouchers; 0 renders all lines. */
  pageSize?: number;
  onChange: (lines: JournalLine[]) => void;
  onAddLine?: () => void;
  onSaveShortcut?: () => void;
}) {
  const size = Math.max(1, pageSize || lines.length);
  const [page, setPage] = useState(0),
    [jump, setJump] = useState(''),
    [density, setDensity] = useState<'comfortable' | 'compact'>(() =>
      typeof localStorage !== 'undefined' &&
      localStorage.getItem('journal-grid-density') === 'compact'
        ? 'compact'
        : 'comfortable',
    ),
    [widthMode, setWidthMode] = useState<'standard' | 'account' | 'amount'>(() => {
      const saved =
        typeof localStorage !== 'undefined' ? localStorage.getItem('journal-grid-width') : null;
      return saved === 'account' || saved === 'amount' ? saved : 'standard';
    });
  type ColumnKey = 'account' | 'debit' | 'credit';
  const defaultWidths: Record<ColumnKey, number> = { account: 300, debit: 150, credit: 150 };
  const [columnWidths, setColumnWidths] = useState<Record<ColumnKey, number> | null>(() => {
    try {
      const saved =
        typeof localStorage !== 'undefined' ? localStorage.getItem('journal-grid-columns') : null;
      if (!saved) return null;
      const parsed = JSON.parse(saved) as Partial<Record<ColumnKey, number>>;
      if (
        !parsed ||
        (['account', 'debit', 'credit'] as ColumnKey[]).some((key) => !Number.isFinite(parsed[key]))
      )
        return null;
      return {
        account: Math.min(640, Math.max(160, Number(parsed.account))),
        debit: Math.min(320, Math.max(100, Number(parsed.debit))),
        credit: Math.min(320, Math.max(100, Number(parsed.credit))),
      };
    } catch {
      return null;
    }
  });
  useEffect(() => {
    localStorage.setItem('journal-grid-density', density);
  }, [density]);
  useEffect(() => {
    localStorage.setItem('journal-grid-width', widthMode);
  }, [widthMode]);
  useEffect(() => {
    if (columnWidths) localStorage.setItem('journal-grid-columns', JSON.stringify(columnWidths));
    else localStorage.removeItem('journal-grid-columns');
  }, [columnWidths]);
  function adjustWidth(key: ColumnKey, delta: number) {
    setColumnWidths((current) => {
      const next = { ...(current ?? defaultWidths) };
      next[key] = Math.min(
        key === 'account' ? 640 : 320,
        Math.max(key === 'account' ? 160 : 100, next[key] + delta),
      );
      return next;
    });
  }
  function beginResize(key: ColumnKey, event: React.PointerEvent<HTMLButtonElement>) {
    event.preventDefault();
    const startX = event.clientX;
    const startWidth = (columnWidths ?? defaultWidths)[key];
    const onMove = (move: PointerEvent) => {
      const delta = move.clientX - startX;
      setColumnWidths((current) => {
        const next = { ...(current ?? defaultWidths) };
        next[key] = Math.min(
          key === 'account' ? 640 : 320,
          Math.max(key === 'account' ? 160 : 100, startWidth + delta),
        );
        return next;
      });
    };
    const onUp = () => {
      window.removeEventListener('pointermove', onMove);
      window.removeEventListener('pointerup', onUp);
    };
    window.addEventListener('pointermove', onMove);
    window.addEventListener('pointerup', onUp);
  }
  const columnStyle = columnWidths ?? { account: 0, debit: 0, credit: 0 };
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
  const amount = (value: string | number | null | undefined) =>
    (typeof value === 'number'
      ? parseYuanToCents(value.toFixed(2))
      : parseYuanToCents(value ?? '0')) ?? 0n;
  const sum = (rows: JournalLine[], field: 'debit' | 'credit') =>
    sumCents(rows.map((line) => amount(line[field])));
  const pageDebit = sum(visible, 'debit'),
    pageCredit = sum(visible, 'credit'),
    allDebit = sum(lines, 'debit'),
    allCredit = sum(lines, 'credit');
  const balance = journalBalanceSummary(lines);
  function handleAmountKeyDown(
    event: React.KeyboardEvent<HTMLInputElement>,
    index: number,
    side: 'debit' | 'credit',
  ) {
    if (isCompositionKey(event) || readonly || busy) return;
    if (event.key === '=' && !event.ctrlKey && !event.altKey && !event.metaKey) {
      event.preventDefault();
      const balanced = balanceLineWithEquals(lines, index);
      if (balanced) onChange(balanced);
      return;
    }
    if (event.key === 'Enter' && !event.ctrlKey && !event.altKey && !event.metaKey) {
      event.preventDefault();
      if (side === 'debit') {
        document
          .querySelector<HTMLInputElement>(`#journal-row-${index} td[data-label="贷方"] input`)
          ?.focus();
      } else {
        document.querySelector<HTMLInputElement>(`#journal-row-${index + 1} input`)?.focus();
      }
    }
  }
  const label = (index: number, field: 'account' | 'debit' | 'credit' | 'delete') =>
    mode === 'inline'
      ? `同屏${field === 'delete' ? '删除' : ''}第 ${index + 1} 行${{ account: '科目', debit: '借方', credit: '贷方', delete: '' }[field]}`
      : field === 'delete'
        ? `删除第 ${index + 1} 行分录`
        : `第 ${index + 1} 行${{ account: '会计科目', debit: '借方金额', credit: '贷方金额', delete: '' }[field]}`;
  function removeLine(index: number) {
    onChange(lines.filter((_, position) => position !== index));
    requestAnimationFrame(() => {
      const next = Math.min(index, Math.max(0, lines.length - 2));
      document.querySelector<HTMLInputElement>(`#journal-row-${next} input`)?.focus();
    });
  }
  return (
    <div
      className={'journal-grid-wrap' + (readonly ? ' journal-grid-readonly' : '')}
      onKeyDown={(event) => {
        if (isCompositionKey(event)) return;
        if (event.altKey && event.key === 'Enter' && onAddLine && !busy) {
          event.preventDefault();
          onAddLine();
          requestAnimationFrame(() => {
            document.querySelector<HTMLInputElement>(`#journal-row-${lines.length} input`)?.focus();
          });
        } else if (event.ctrlKey && event.key === 'Enter' && onSaveShortcut && !busy) {
          event.preventDefault();
          onSaveShortcut();
        }
      }}
    >
      <div className="journal-grid-toolbar">
        <span>
          每页 {size} 行，当前第 {current + 1} / {pageCount} 页
        </span>
        <label className="journal-grid-width">
          列宽
          <select
            aria-label="凭证列宽"
            value={widthMode}
            onChange={(event) =>
              setWidthMode(event.target.value as 'standard' | 'account' | 'amount')
            }
          >
            <option value="standard">标准</option>
            <option value="account">宽科目</option>
            <option value="amount">宽金额</option>
          </select>
        </label>
        <span className="journal-grid-width-buttons">
          <button
            type="button"
            aria-label="调窄会计科目列"
            onClick={() => adjustWidth('account', -20)}
          >
            科目−
          </button>
          <button
            type="button"
            aria-label="加宽会计科目列"
            onClick={() => adjustWidth('account', 20)}
          >
            科目＋
          </button>
          <button type="button" aria-label="调窄借方列" onClick={() => adjustWidth('debit', -20)}>
            借方−
          </button>
          <button type="button" aria-label="加宽借方列" onClick={() => adjustWidth('debit', 20)}>
            借方＋
          </button>
        </span>
        {columnWidths ? (
          <button type="button" className="text-button" onClick={() => setColumnWidths(null)}>
            重置列宽
          </button>
        ) : null}
        <button
          type="button"
          className="text-button"
          aria-pressed={density === 'compact'}
          onClick={() => setDensity((value) => (value === 'compact' ? 'comfortable' : 'compact'))}
        >
          {density === 'compact' ? '舒适行距' : '紧凑行距'}
        </button>
      </div>
      {(!readonly || onAddLine || onSaveShortcut) && (
        <p className="journal-grid-shortcuts" aria-label="键盘快捷键">
          {!readonly && '= 键配平当前行 · Enter 下一格'}
          {!readonly && (onAddLine || onSaveShortcut) && ' · '}
          {onAddLine && 'Alt+Enter 新增分录'}
          {onAddLine && onSaveShortcut && ' · '}
          {onSaveShortcut && 'Ctrl+Enter 保存草稿'}
        </p>
      )}
      <table
        ref={table}
        className={`journal-entry-grid density-${density} width-${widthMode}${columnWidths ? ' has-custom-widths' : ''}`}
        aria-label={mode === 'inline' ? '同屏凭证分录表' : '凭证分录表'}
      >
        <colgroup>
          <col className="journal-grid-col-index" />
          {columnWidths ? (
            <>
              <col style={{ width: columnStyle.account }} />
              <col style={{ width: columnStyle.debit }} />
              <col style={{ width: columnStyle.credit }} />
            </>
          ) : (
            <>
              <col />
              <col />
              <col />
            </>
          )}
          <col className="journal-grid-col-action" />
        </colgroup>
        <thead>
          <tr>
            <th scope="col" className="journal-grid-index">
              行
            </th>
            <th scope="col">
              会计科目
              <button
                type="button"
                className="journal-grid-resize"
                aria-label="调整会计科目列宽"
                onPointerDown={(event) => beginResize('account', event)}
                onKeyDown={(event) => {
                  if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') {
                    event.preventDefault();
                    adjustWidth('account', event.key === 'ArrowRight' ? 10 : -10);
                  }
                }}
              />
            </th>
            <th scope="col" className="journal-grid-amount">
              借方
              <span className="journal-grid-unit">（元）</span>
              <button
                type="button"
                className="journal-grid-resize"
                aria-label="调整借方列宽"
                onPointerDown={(event) => beginResize('debit', event)}
                onKeyDown={(event) => {
                  if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') {
                    event.preventDefault();
                    adjustWidth('debit', event.key === 'ArrowRight' ? 10 : -10);
                  }
                }}
              />
            </th>
            <th scope="col" className="journal-grid-amount">
              贷方
              <span className="journal-grid-unit">（元）</span>
              <button
                type="button"
                className="journal-grid-resize"
                aria-label="调整贷方列宽"
                onPointerDown={(event) => beginResize('credit', event)}
                onKeyDown={(event) => {
                  if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') {
                    event.preventDefault();
                    adjustWidth('credit', event.key === 'ArrowRight' ? 10 : -10);
                  }
                }}
              />
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
                <tr className="journal-grid-line" id={`journal-row-${index}`}>
                  <th scope="row" className="journal-grid-index">
                    {String(index + 1).padStart(2, '0')}
                  </th>
                  <td className="journal-grid-account" data-label="会计科目">
                    <AccountPicker
                      accounts={accounts}
                      value={line.account_code}
                      disabled={readonly || busy}
                      ariaLabel={label(index, 'account')}
                      onChange={(code) => update(index, 'account_code', code)}
                    />
                  </td>
                  <td className="journal-grid-amount" data-label="借方">
                    <input
                      aria-label={label(index, 'debit')}
                      inputMode="decimal"
                      value={line.debit}
                      disabled={readonly || busy}
                      onChange={(event) => update(index, 'debit', event.target.value)}
                      onKeyDown={(event) => handleAmountKeyDown(event, index, 'debit')}
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
                      onKeyDown={(event) => handleAmountKeyDown(event, index, 'credit')}
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
                      onClick={() => removeLine(index)}
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
        <span
          className={
            'journal-balance-pill ' +
            (balance.hasInvalidAmount ? 'invalid' : balance.balanced ? 'balanced' : 'unbalanced')
          }
          role="status"
          aria-live="polite"
        >
          {balance.hasInvalidAmount ? (
            '金额格式待核对'
          ) : balance.balanced ? (
            '借贷已平'
          ) : (
            <>
              差额 <MoneyCell cents={balance.shortAmountCents} options={{ currency: true }} />（
              {balance.shortSide === 'debit' ? '借方少' : '贷方少'}）
            </>
          )}
        </span>
        {!readonly && !balance.balanced && !balance.hasInvalidAmount && lines.length > 0 && (
          <button
            type="button"
            className="journal-balance-action"
            disabled={busy}
            onClick={() => {
              const balanced = smartBalanceLines(lines);
              if (balanced) onChange(balanced);
            }}
          >
            一键配平
          </button>
        )}
        <span>
          本页 借 <MoneyCell cents={pageDebit} options={{ currency: true }} /> · 贷{' '}
          <MoneyCell cents={pageCredit} options={{ currency: true }} />
        </span>
        <span>
          全量 借 <MoneyCell cents={allDebit} options={{ currency: true }} /> · 贷{' '}
          <MoneyCell cents={allCredit} options={{ currency: true }} />
        </span>
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
