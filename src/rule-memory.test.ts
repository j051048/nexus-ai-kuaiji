import { describe, expect, it } from 'vitest';
import { readRuleMemory } from './rule-memory';

const hash = 'a'.repeat(64);
const scope = { firm_id: 'firm', company_id: 'company', book_id: 'book', period: '2026-10' };
const expected = { ...scope, rule_id: 'rule', version: 1, state_version: 2 };
const value = () => ({
  schema: 'enterprise-rule-memory-v1',
  scope,
  rule_id: 'rule',
  version: 1,
  state_version: 2,
  state: 'candidate',
  auto_apply: false,
  sources: [
    {
      sample_id: 'sample',
      draft_id: 'draft',
      reviewed_version: 3,
      reviewed_by: 'reviewer',
      source_fingerprint: hash,
      original_hashes: [hash],
    },
  ],
  unavailable: [],
  policy_fingerprint: hash,
  master_fingerprint: hash,
  catalog_fingerprint: hash,
  definition_fingerprint: hash,
  fingerprint: hash,
  boundary: '历史金额不作为当前事实',
  next_action: '选择独立反例进行评测',
});
describe('企业规则记忆是当前范围内的模板依据', () => {
  it('接纳当前版本并保留候选及人工评测边界', () => {
    expect(readRuleMemory(value(), expected).auto_apply).toBe(false);
  });
  it.each(['firm_id', 'company_id', 'book_id', 'period'] as const)('拒绝错 %s', (key) => {
    expect(() =>
      readRuleMemory({ ...value(), scope: { ...scope, [key]: 'other' } }, expected),
    ).toThrow();
  });
  it.each([
    { version: 2 },
    { state_version: true },
    { rule_id: 'other' },
    { auto_apply: true },
    { fingerprint: '' },
    { state: 'candidate', unavailable: ['已失效'] },
    { sources: [] },
  ])('拒绝不一致投影 %j', (patch) => {
    expect(() => readRuleMemory({ ...value(), ...patch }, expected)).toThrow();
  });
  it('失效候选可解释，但不能伪装为生效规则', () => {
    expect(
      readRuleMemory(
        { ...value(), state: 'blocked', sources: [], unavailable: ['复核已失效'] },
        expected,
      ).state,
    ).toBe('blocked');
  });
});
