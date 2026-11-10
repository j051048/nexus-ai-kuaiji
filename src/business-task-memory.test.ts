import { describe, expect, it } from 'vitest';
import { taskMemoryKey, readTaskMemory, taskCheckpoint } from './business-task-memory';

const identity = { user_id: 'alice', firm_id: 'firm-a' };
const now = 1794300000000;
const checkpoint = () =>
  taskCheckpoint(
    identity,
    {
      id: 'event-a',
      company_id: 'company-a',
      version: 4,
      payload: { occurred_on: '2026-10-12', amount: 'private', description: 'private' },
    },
    { scope: 'company', status: 'department_approved', offset: 50 },
    'differences',
    now,
  );
describe('reference-only task recovery', () => {
  it('remembers location but never financial facts, credentials or approval', () => {
    const text = JSON.stringify(checkpoint());
    expect(text).not.toContain('private');
    expect(text).not.toContain('token');
    expect(readTaskMemory(text, identity, now)).toEqual(checkpoint());
  });
  it('isolates people and firms and rejects stale or malformed references', () => {
    expect(taskMemoryKey(identity)).not.toBe(taskMemoryKey({ ...identity, user_id: 'bob' }));
    expect(
      readTaskMemory(JSON.stringify(checkpoint()), { ...identity, firm_id: 'b' }, now),
    ).toBeNull();
    for (const changes of [
      { savedAt: now - 8 * 86400000 },
      { version: true },
      { focus: 'https://evil' },
      { offset: -1 },
      { period: '2026-13' },
      { companyId: '' },
    ])
      expect(
        readTaskMemory(JSON.stringify({ ...checkpoint(), ...changes }), identity, now),
      ).toBeNull();
  });
});
