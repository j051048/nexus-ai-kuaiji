import { Fragment } from 'react';
import { Trash2 } from 'lucide-react';
import type { BookDetail, JournalLine } from './types';
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
  onChange,
}: {
  lines: JournalLine[];
  accounts: BookDetail['accounts'];
  readonly: boolean;
  busy: boolean;
  mode?: 'standard' | 'inline';
  onChange: (lines: JournalLine[]) => void;
}) {
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
  const label = (index: number, field: 'account' | 'debit' | 'credit' | 'delete') =>
    mode === 'inline'
      ? `同屏${field === 'delete' ? '删除' : ''}第 ${index + 1} 行${{ account: '科目', debit: '借方', credit: '贷方', delete: '' }[field]}`
      : field === 'delete'
        ? `删除第 ${index + 1} 行分录`
        : `第 ${index + 1} 行${{ account: '会计科目', debit: '借方金额', credit: '贷方金额', delete: '' }[field]}`;
  return (
    <div className="journal-grid-wrap">
      <table
        className="journal-entry-grid"
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
            </th>
            <th scope="col" className="journal-grid-amount">
              贷方
            </th>
            <th scope="col" className="journal-grid-action">
              <span className="sr-only">操作</span>
            </th>
          </tr>
        </thead>
        <tbody>
          {lines.map((line, index) => (
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
          ))}
        </tbody>
      </table>
    </div>
  );
}
