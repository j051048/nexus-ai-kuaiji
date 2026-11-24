import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import type { JournalLine } from './types';
import { JournalEntryGrid } from './JournalEntryGrid';
import { balanceLineWithEquals, journalBalanceSummary, smartBalanceLines } from './journal-balance';

const line = (account_code: string, debit: string, credit: string): JournalLine => ({
  account_code,
  debit,
  credit,
});

describe('journal-balance 凭证借贷试算与快捷配平', () => {
  it('使用整数分精确判定平衡与借贷差额，不出现浮点误差', () => {
    const balanced = journalBalanceSummary([
      line('6602', '0.10', '0.00'),
      line('6602', '0.20', '0.00'),
      line('1002', '0.00', '0.30'),
    ]);
    expect(balanced.balanced).toBe(true);
    expect(balanced.shortSide).toBeNull();
    expect(balanced.shortAmountCents).toBe(0n);

    const shortCredit = journalBalanceSummary([
      line('6602', '128.55', '0.00'),
      line('1002', '0.00', '28.55'),
    ]);
    expect(shortCredit.balanced).toBe(false);
    expect(shortCredit.shortSide).toBe('credit');
    expect(shortCredit.shortAmountCents).toBe(10000n);

    const shortDebit = journalBalanceSummary([
      line('6602', '50.00', '0.00'),
      line('1002', '0.00', '88.88'),
    ]);
    expect(shortDebit.shortSide).toBe('debit');
    expect(shortDebit.shortAmountCents).toBe(3888n);
  });

  it('存在无法解析的金额时不误判为已平衡，且拒绝自动覆写', () => {
    const invalid = [line('6602', 'abc', '0.00'), line('1002', '0.00', '0.00')];
    const summary = journalBalanceSummary(invalid);
    expect(summary.hasInvalidAmount).toBe(true);
    expect(summary.balanced).toBe(false);
    expect(balanceLineWithEquals(invalid, 1)).toBeNull();
    expect(smartBalanceLines(invalid)).toBeNull();
  });

  it('= 键配平用其余全部行的差额填平当前行并清空另一侧', () => {
    const lines = [
      line('6602', '1000.00', '0.00'),
      line('2221', '60.00', '0.00'),
      line('1002', '500.00', '0.00'),
    ];
    const next = balanceLineWithEquals(lines, 2);
    expect(next).not.toBeNull();
    expect(next?.[2]).toEqual(line('1002', '0.00', '1060.00'));
    expect(journalBalanceSummary(next!).balanced).toBe(true);
  });

  it('一键配平优先填充末尾零金额空行，无空行时配平最后一行', () => {
    const withEmpty = [
      line('6602', '320.50', '0.00'),
      line('1002', '0.00', '0.00'),
      line('2202', '0.00', '100.00'),
    ];
    const balancedEmpty = smartBalanceLines(withEmpty);
    expect(balancedEmpty?.[1]).toEqual(line('1002', '0.00', '220.50'));
    expect(journalBalanceSummary(balancedEmpty!).balanced).toBe(true);

    const noEmpty = [line('6602', '320.50', '0.00'), line('1002', '0.00', '100.00')];
    const balancedLast = smartBalanceLines(noEmpty);
    expect(balancedLast?.[1]).toEqual(line('1002', '0.00', '320.50'));
    expect(journalBalanceSummary(balancedLast!).balanced).toBe(true);
  });

  it('在可编辑分录表展示差额、快捷提示与一键配平，只读态不展示编辑按钮', () => {
    const accounts = [
      { code: '6602', name: '管理费用' },
      { code: '1002', name: '银行存款' },
    ];
    const editableHtml = renderToStaticMarkup(
      createElement(JournalEntryGrid, {
        lines: [line('6602', '200.00', '0.00'), line('1002', '0.00', '80.00')],
        accounts,
        readonly: false,
        busy: false,
        onChange: () => {},
      }),
    );
    expect(editableHtml).toContain('= 键配平当前行');
    expect(editableHtml).toContain('贷方少');
    expect(editableHtml).toContain('120.00');
    expect(editableHtml).toContain('一键配平');

    const readonlyHtml = renderToStaticMarkup(
      createElement(JournalEntryGrid, {
        lines: [line('6602', '200.00', '0.00'), line('1002', '0.00', '80.00')],
        accounts,
        readonly: true,
        busy: false,
        onChange: () => {},
      }),
    );
    expect(readonlyHtml).toContain('贷方少');
    expect(readonlyHtml).not.toContain('一键配平');
    expect(readonlyHtml).not.toContain('= 键配平当前行');
  });
});
