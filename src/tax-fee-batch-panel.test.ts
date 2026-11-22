import { describe, expect, it } from 'vitest';
import { canPostBatch, chainSummary, segmentState } from './TaxFeeBatchPanel';
import type { FeeChain } from './TaxFeeBatchPanel';

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

describe('fee chain drawer', () => {
  const chain: FeeChain = {
    book_id: 'book-1',
    period: '2026-10',
    batch_id: 'batch-1',
    segments: [
      { key: 'batch', label: '规费批次', state: 'present', count: 1, items: [], reason: '' },
      {
        key: 'receivables',
        label: '驾驶员应收',
        state: 'present',
        count: 3,
        items: [],
        reason: '',
      },
      {
        key: 'vouchers',
        label: '正式凭证',
        state: 'missing',
        count: 0,
        items: [],
        reason: '银行来源草稿尚未记账（或尚未生成）',
      },
    ],
    gaps: ['正式凭证：银行来源草稿尚未记账（或尚未生成）'],
    pending_drafts: [{ draft_id: 'draft-1', draft_status: 'draft' }],
    readonly: true,
    note: '只读链路视图',
  };

  it('states each segment and never claims a missing one is ready', () => {
    expect(segmentState('present')).toBe('已就绪');
    expect(segmentState('missing')).toBe('缺失');
    expect(segmentState('degraded')).toBe('degraded');
  });

  it('summarises gaps with their reasons instead of hiding them in the table', () => {
    expect(chainSummary(chain)).toContain('2/3 段已就绪');
    expect(chainSummary(chain)).toContain('尚未记账');
    expect(chainSummary({ ...chain, segments: chain.segments.slice(0, 2), gaps: [] })).toBe(
      '2/2 段已就绪',
    );
    expect(chainSummary(null)).toBe('');
  });
});
