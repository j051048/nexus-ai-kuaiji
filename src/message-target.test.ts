import { expect, it } from 'vitest';
import { parseMessageTarget } from './message-target';
const message = { id: 'm', company_id: 'c', related_ref: 'e', related_kind: 'return' };
it('提醒仅接受本消息同企业原事项', () => {
  const target = {
    message_id: 'm',
    company_id: 'c',
    kind: 'business',
    event_id: 'e',
    period: '2026-11',
  };
  expect(parseMessageTarget(target, message)).toEqual(target);
  for (const change of [
    { company_id: 'other' },
    { message_id: 'other' },
    { event_id: 'other' },
    { kind: 'url' },
    { period: '2026-99' },
  ])
    expect(() => parseMessageTarget({ ...target, ...change }, message)).toThrow();
});
it('签收入口必须带精确包版本及摘要', () => {
  const target = {
    message_id: 'm',
    company_id: 'c',
    kind: 'monthly_package',
    package_id: 'e',
    book_id: 'b',
    period: '2026-11',
    version: 2,
    manifest_sha256: 'a'.repeat(64),
  };
  expect(parseMessageTarget(target, message)).toEqual(target);
  for (const change of [
    { version: true },
    { version: 0 },
    { manifest_sha256: '' },
    { book_id: '' },
  ])
    expect(() => parseMessageTarget({ ...target, ...change }, message)).toThrow();
});
