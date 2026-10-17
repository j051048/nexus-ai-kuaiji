import { describe, expect, it } from 'vitest';
import {
  monthlyMoney,
  monthlyImportSummary,
  previousMonthlyPeriod,
  recurringLinesIssue,
  signedMonthlyCents,
  validMonthlyPeriod,
} from './monthly-helpers';
import {
  monthlyClosingLabel,
  monthlyCount,
  monthlyRowDestination,
} from './monthly-workbench-types';
import type { MonthlyClientRow } from './monthly-workbench-types';

describe('月度工作台的未知事实与精确金额', () => {
  it('缺失余额和权限保持待核实，明确的零仍显示零', () => {
    expect(monthlyMoney(null)).toBe('待核实');
    expect(monthlyMoney('0.00')).toBe('0.00');
    expect(monthlyCount(null)).toBe('未授权 / 待核实');
    expect(monthlyCount(0)).toBe('0');
    expect(monthlyClosingLabel('recheck')).toBe('需要当前预检');
    expect(monthlyClosingLabel('ready')).toBe('状态待核实');
  });
  it('负余额和大金额保持整数分精度，不允许多余小数或非金额内容', () => {
    expect(signedMonthlyCents('-999999999999.99')).toBe(-99999999999999n);
    expect(monthlyMoney('-0.05')).toBe('-0.05');
    expect(signedMonthlyCents('10.001')).toBeNull();
    expect(signedMonthlyCents('1e5')).toBeNull();
  });
  it('固定事项必须明确单边金额、真实科目、借贷平衡，0.10和0.20精确相加', () => {
    const lines = [
      { account_code: 'a', debit: '0.10', credit: '0.00' },
      { account_code: 'b', debit: '0.20', credit: '0.00' },
      { account_code: 'c', debit: '0.00', credit: '0.30' },
    ];
    expect(recurringLinesIssue(lines)).toBe('');
    expect(recurringLinesIssue([...lines.slice(0, 2), { ...lines[2], credit: '0.31' }])).toContain(
      '不平',
    );
    expect(recurringLinesIssue([{ ...lines[0], credit: '0.10' }, lines[2]])).toContain('一方');
    expect(recurringLinesIssue([{ ...lines[0], account_code: '' }, lines[2]])).toContain('真实');
  });
  it('下钻只带当前行的标识，不借用其他账套或越过能力提示', () => {
    const row = {
      company_id: 'c2',
      book_id: 'b2',
      period: '2026-11',
      materials: { business_pending: null },
      exceptions: { open: 1 },
      delivery: { unverified: 1 },
      permissions: { review: false },
    } as MonthlyClientRow;
    expect(monthlyRowDestination(row, 'business')).toBeNull();
    expect(monthlyRowDestination(row, 'review')).toBeNull();
    expect(monthlyRowDestination(row, 'cases')).toEqual({
      view: 'cases',
      companyId: 'c2',
      bookId: 'b2',
      period: '2026-11',
    });
    expect(monthlyRowDestination({ ...row, book_id: null }, 'balances')).toBeNull();
    expect(
      monthlyRowDestination(
        { ...row, closing: { state: 'recheck', blockers: null, snapshot_id: null } },
        'briefing',
      ),
    ).toBeNull();
    expect(
      monthlyRowDestination(
        {
          ...row,
          closing: { state: 'closed', blockers: 0, snapshot_id: 'snapshot' },
          permissions: { edit: false, review: false, export: false, manage: false },
        },
        'briefing',
      ),
    ).toEqual({
      view: 'briefing',
      companyId: 'c2',
      bookId: 'b2',
      period: '2026-11',
    });
  });
  it('月份只接受规范的月度范围', () => {
    expect(validMonthlyPeriod('2026-10')).toBe(true);
    expect(validMonthlyPeriod('2026-13')).toBe(false);
  });
  it('外部余额文件不接受错误月份、币种、未过账范围或JSON数字金额', () => {
    const file = {
      period: '2026-09',
      currency: 'CNY',
      scope: 'posted',
      lines: [{ account_code: '1002', balance: '-0.05', auxiliary_values: {} }],
    };
    expect(monthlyImportSummary(JSON.stringify(file), 'json', '2026-09')).toEqual({
      rows: 1,
      period: '2026-09',
    });
    expect(() => monthlyImportSummary(JSON.stringify(file), 'json', '2026-10')).toThrow('正确月份');
    expect(() =>
      monthlyImportSummary(JSON.stringify({ ...file, scope: 'all' }), 'json', '2026-09'),
    ).toThrow('过账');
    expect(() =>
      monthlyImportSummary(
        JSON.stringify({ ...file, lines: [{ account_code: '1002', balance: 0.05 }] }),
        'json',
        '2026-09',
      ),
    ).toThrow('文本金额');
    expect(
      monthlyImportSummary(
        '\uFEFFaccount_code,balance,auxiliary_values,period,currency,scope\n1002,-0.05,{},2026-09,CNY,posted\n',
        'csv',
        '2026-09',
      ).rows,
    ).toBe(1);
    expect(previousMonthlyPeriod('2026-01')).toBe('2025-12');
  });
});
