import { describe, expect, it } from 'vitest';
import { reconciliationCents, reconciliationMatchItem, reconciliationSelectionIssue, reconciliationSelectionTotal } from './reconciliation-helpers';
import type { BankCandidate, ReconciliationEvent } from './reconciliation-types';
const event = { book_id: 'book', event_id: 'event', event_version: 7, fact_version: 3, business_draft_version: 2, business_source_fingerprint: 'source-hash', amount: '0.30', permissions: { confirm: true } } as ReconciliationEvent;
const bank = (id: string, amount: string): BankCandidate => ({ source_id: id, source_fingerprint: `hash-${id}`, draft_version: 1, date: '2026-10-03', description: '合成回单', counterparty: '供应商', amount, external_id: id, stable_transaction_id: id, reasons: [], risks: [], selectable: true });
describe('精确金额与流水核对交互', () => {
  it('0.10加0.20以整数分计算，支持大额而不损失小数', () => { expect(reconciliationSelectionTotal([bank('a', '0.10'), bank('b', '0.20')])).toBe('0.30'); expect(reconciliationCents('999999999999.99')).toBe(99999999999999n); });
  it('金额不等、无权限、未生成草稿都不能确认', () => { expect(reconciliationSelectionIssue(event, [bank('a', '0.10')], '核对')).toContain('不一致'); expect(reconciliationSelectionIssue({ ...event, permissions: { confirm: false } }, [bank('a', '0.30')], '核对')).toBeTruthy(); expect(reconciliationSelectionIssue(event, [{ ...bank('a', '0.30'), draft_version: null }], '核对')).toBeTruthy(); });
  it('确认携带业务和银行版本与来源指纹，银行选择顺序固定', () => { const item = reconciliationMatchItem(event, [bank('b', '0.20'), bank('a', '0.10')]); expect(item.fact_version).toBe(3); expect(item.business_draft_version).toBe(2); expect(item.banks.map(row => row.source_id)).toEqual(['a', 'b']); expect(item.banks[0].source_fingerprint).toBe('hash-a'); });
  it('未填核对依据或重复选择均不可提交', () => { expect(reconciliationSelectionIssue(event, [bank('a', '0.30')], ' ')).toBeTruthy(); expect(reconciliationSelectionIssue(event, [bank('a', '0.10'), bank('a', '0.20')], '核对')).toContain('重复'); });
});
