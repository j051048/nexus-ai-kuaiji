import { describe, expect, it } from 'vitest';
import { accountChoices, isLeafAccount, nearestChoiceIndex } from './account-picker';

const accounts = [
  { code: '1001', name: '库存现金' },
  { code: '1002', name: '银行存款' },
  { code: '2221', name: '应交税费' },
  { code: '222101', name: '应交增值税' },
  { code: '6602', name: '管理费用' },
  { code: '6602.01', name: '管理费用—工资' },
  { code: '6602.02', name: '管理费用—办公费' },
];

describe('isLeafAccount', () => {
  it('treats parent codes as groups and four/six/dot children as leaves', () => {
    expect(isLeafAccount('1002', accounts)).toBe(true);
    expect(isLeafAccount('2221', accounts)).toBe(false);
    expect(isLeafAccount('222101', accounts)).toBe(true);
    expect(isLeafAccount('6602', accounts)).toBe(false);
    expect(isLeafAccount('6602.01', accounts)).toBe(true);
  });
});

describe('accountChoices', () => {
  it('filters by code or name and keeps parents as disabled groups', () => {
    const rows = accountChoices(accounts, '增值税');
    expect(rows.map((row) => row.code)).toEqual(['2221', '222101']);
    expect(rows[0].selectable).toBe(false);
    expect(rows[1].selectable).toBe(true);
  });

  it('keeps a matched parent above its children so it reads as a group', () => {
    const rows = accountChoices(accounts, '6602');
    expect(rows.map((row) => row.code)).toEqual(['6602', '6602.01', '6602.02']);
    expect(rows[0].selectable).toBe(false);
  });

  it('returns no rows for an unknown query', () => {
    expect(accountChoices(accounts, '9999')).toEqual([]);
  });
});

describe('nearestChoiceIndex', () => {
  it('skips disabled group rows in both directions', () => {
    const rows = accountChoices(accounts, '');
    const first = nearestChoiceIndex(rows, -1, 1);
    expect(rows[first].selectable).toBe(true);
    const parent = rows.findIndex((row) => row.code === '2221');
    expect(rows[nearestChoiceIndex(rows, parent, 1)].code).not.toBe('2221');
    const last = nearestChoiceIndex(rows, -1, -1);
    expect(rows[last].selectable).toBe(true);
  });
});
