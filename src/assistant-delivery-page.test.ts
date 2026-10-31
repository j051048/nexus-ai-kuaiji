import { describe, expect, it } from 'vitest';
import { parseDeliveryPage, type DeliveryPageScope } from './assistant-delivery-page';

const scope: DeliveryPageScope = {
  company_id: 'a',
  book_id: 'book',
  period: '2026-10',
  inputs_fingerprint: 'a'.repeat(64),
  conversation_id: 'conv',
  turn_id: 'turn',
  kind: 'bank',
  limit: 10,
  start: 0,
};
const page = () => ({
  ...scope,
  schema: 'assistant-delivery-page-v1',
  snapshot_fingerprint: 'b'.repeat(64),
  page_fingerprint: 'c'.repeat(64),
  end: 1,
  total: 1,
  has_more: false,
  cursor: 'next',
  accounting_approved: false,
  rows: [
    {
      key: 'd'.repeat(64),
      label: '银行 A',
      passed: false,
      issues: [
        {
          code: 'bank_coverage_missing',
          reason: '缺少完整对账单',
          owner_role: '账套会计',
          next_action: '补充原件后由独立核验人确认',
          evidence_ids: ['book:book'],
        },
      ],
    },
  ],
});

describe('诊断分页整页接纳与身份契约', () => {
  it('只接纳同一诊断的已校验页，不把阅读当复核', () => {
    expect(parseDeliveryPage(page(), scope).rows).toHaveLength(1);
  });
  it.each([
    { company_id: 'b' },
    { book_id: 'b' },
    { period: '2026-11' },
    { conversation_id: 'other' },
    { turn_id: 'other' },
    { kind: 'control' },
    { inputs_fingerprint: 'f'.repeat(64) },
    { snapshot_fingerprint: '' },
    { page_fingerprint: '' },
    { limit: 20 },
    { start: 10 },
    { end: 2 },
    { total: -1 },
    { total: true },
    { has_more: true },
    { cursor: '' },
    { accounting_approved: true },
    { rows: null },
    { schema: 'other' },
  ])('整页拒绝错误范围/形状 %o', (change) => {
    expect(() => parseDeliveryPage({ ...page(), ...change }, scope)).toThrow();
  });
  it('拒绝重复行、他账套引用、缺少责任人和伪造通过状态', () => {
    const p = page();
    for (const rows of [
      [p.rows[0], p.rows[0]],
      [{ ...p.rows[0], passed: 'yes' }],
      [{ ...p.rows[0], issues: [{ ...p.rows[0].issues[0], owner_role: '' }] }],
      [{ ...p.rows[0], issues: [{ ...p.rows[0].issues[0], evidence_ids: ['book:other'] }] }],
    ])
      expect(() => parseDeliveryPage({ ...p, rows }, scope)).toThrow();
  });
  it('空页合法，后续页必须匹配明确起点和快照', () => {
    expect(parseDeliveryPage({ ...page(), rows: [], end: 0, total: 0 }, scope).total).toBe(0);
    expect(() =>
      parseDeliveryPage(page(), { ...scope, snapshot_fingerprint: 'f'.repeat(64) }),
    ).toThrow();
  });
});
