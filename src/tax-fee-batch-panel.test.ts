import { describe, expect, it } from 'vitest';
import { canPostBatch } from './TaxFeeBatchPanel';

const reviewed = {
  id: 'batch-1',
  book_id: 'book-1',
  period: '2026-10',
  status: 'reviewed',
  version: 2,
  draft_id: 'draft-1',
  draft: {
    id: 'draft-1',
    status: 'approved',
    version: 3,
    posted_voucher_id: null,
    voucher_no: null,
  },
  total_cents: 22_500,
  row_count: 3,
  skipped_count: 0,
  policy: { version: 'fee-v1', driver_receivable_account: '1122', vehicle_revenue_account: '6001' },
  policy_fingerprint: 'p',
  source_fingerprint: 's',
  batch_fingerprint: 'b',
  stale: false,
  stale_reason: '',
  source_files: [],
  rows: [],
  exceptions: [],
  by_driver: [],
  by_vehicle: [],
};

describe('fee batch posting gate', () => {
  it('allows posting only after the independent review approved the draft', () => {
    expect(canPostBatch(reviewed)).toBe(true);
  });

  it('blocks an unreviewed batch even when the draft looks ready', () => {
    expect(canPostBatch({ ...reviewed, status: 'draft' })).toBe(false);
  });

  it('blocks a reviewed batch whose source moved on', () => {
    expect(canPostBatch({ ...reviewed, stale: true })).toBe(false);
  });

  it('blocks a draft that is not approved yet', () => {
    expect(canPostBatch({ ...reviewed, draft: { ...reviewed.draft, status: 'draft' } })).toBe(
      false,
    );
  });

  it('blocks a second posting of the same draft', () => {
    expect(
      canPostBatch({
        ...reviewed,
        draft: { ...reviewed.draft, posted_voucher_id: 'voucher-1', voucher_no: 1 },
      }),
    ).toBe(false);
  });

  it('blocks an invalidated batch and a missing draft', () => {
    expect(canPostBatch({ ...reviewed, status: 'invalidated' })).toBe(false);
    expect(canPostBatch({ ...reviewed, draft: null })).toBe(false);
    expect(canPostBatch(null)).toBe(false);
  });
});
