import { describe, expect, it } from 'vitest';
import { parseReviewPolicy } from './review-policy';
import { parseNotificationReceipt } from './NotificationReceipts';
import type { NotificationItem } from './delivery-notification-state';

const policy = {
  book_id: 'b',
  ledger_id: 'l',
  can_manage: true,
  policy: {
    version: 0,
    mode: 'independent',
    label: '独立复核',
    reason: '默认',
    confirmed_by: null,
    created_at: null,
  },
};
describe('04–06 scoped projections', () => {
  const item = {
    id: 'n',
    attempts: 1,
    recipient: { user_id: 'u' },
    package: { id: 'p', version: 2, manifest_sha256: 'a'.repeat(64) },
  } as NotificationItem;
  const receipt = {
    notification_id: 'n',
    book_id: 'b',
    company_id: 'c',
    package_id: 'p',
    package_version: 2,
    manifest_sha256: 'a'.repeat(64),
    attempts: 1,
    recipient_user_id: 'u',
    accepted: true,
    delivered: true,
    viewed: true,
    bounced: true,
    signed: false,
    customer_opened: false,
    receipt_conflict: true,
    can_retry: false,
  };
  it('keeps channel view separate from customer opening and signature', () => {
    const r = parseNotificationReceipt(receipt, 'b', item);
    expect(r.viewed).toBe(true);
    expect(r.customer_opened).toBe(false);
    expect(r.signed).toBe(false);
  });
  it.each([
    'book_id',
    'notification_id',
    'package_id',
    'package_version',
    'manifest_sha256',
    'attempts',
    'recipient_user_id',
    'can_retry',
    'receipt_conflict',
  ])('rejects substituted receipt %s', (key) => {
    expect(() => parseNotificationReceipt({ ...receipt, [key]: 'wrong' }, 'b', item)).toThrow();
  });
  it('accepts a fixed denominator including unverified history', () => {
    expect(
      parseReviewPolicy(
        {
          ...policy,
          review_summary: {
            total_reviewed: 4,
            independent: 1,
            single_operator: 1,
            unverified: 2,
            single_operator_ratio: '0.2500',
          },
        },
        'b',
      ).review_summary?.unverified,
    ).toBe(2);
  });
  it.each([
    {
      total_reviewed: 2,
      independent: 2,
      single_operator: 1,
      unverified: 0,
      single_operator_ratio: '0.5000',
    },
    {
      total_reviewed: 0,
      independent: 0,
      single_operator: 0,
      unverified: 0,
      single_operator_ratio: '0.0000',
    },
    {
      total_reviewed: true,
      independent: 0,
      single_operator: 0,
      unverified: 0,
      single_operator_ratio: null,
    },
  ])('rejects invented or inconsistent review denominator', (review_summary) => {
    expect(() => parseReviewPolicy({ ...policy, review_summary }, 'b')).toThrow();
  });
});
