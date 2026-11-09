import { describe, expect, it } from 'vitest';
import { assertBusinessSelection, nextBusinessItem } from './business-selection';
import type { BusinessEvent } from './business-types';

const event = {
  id: 'a',
  firm_id: 'firm',
  company_id: 'company',
  version: 3,
  payload: { occurred_on: '2026-10-12' },
  status: 'department_approved',
  permissions: { finance_approve: true },
} as BusinessEvent;
const target = { id: 'a', firmId: 'firm', companyId: 'company', period: '2026-10', version: 3 };
describe('business response binding', () => {
  it('accepts current identity and newer versions', () => {
    expect(assertBusinessSelection(event, target)).toBe(event);
    expect(assertBusinessSelection({ ...event, version: 4 }, target).version).toBe(4);
  });
  for (const changed of [
    { id: 'b' },
    { firm_id: 'other' },
    { company_id: 'other' },
    { payload: { occurred_on: '2026-11-01' } },
    { version: 2 },
    { version: true },
    { version: 3.5 },
  ]) {
    it(`rejects mismatched ${JSON.stringify(changed)}`, () => {
      expect(() =>
        assertBusinessSelection({ ...event, ...changed } as BusinessEvent, target),
      ).toThrow('事项范围或版本不一致');
    });
  }
  it('next item uses the same company and server permission, not merely a pending label', () => {
    const next = { ...event, id: 'next' };
    expect(
      nextBusinessItem(
        [
          event,
          { ...next, company_id: 'other' },
          { ...next, permissions: { ...next.permissions, finance_approve: false } },
          next,
        ],
        event,
        'company',
      ),
    ).toBe(next);
    expect(nextBusinessItem([next], event, 'own')).toBeUndefined();
  });
});
