import { describe, expect, it } from 'vitest';
import { monthlyStages } from './monthly-stage-guide';
import type { MonthlyClientRow } from './monthly-workbench-types';

const base: MonthlyClientRow = {
  row_key: 'book-1',
  company_id: 'company-1',
  company_name: '合成企业',
  book_id: 'book-1',
  book_name: '合成账套',
  ledger_id: null,
  period: '2026-10',
  status: 'open',
  materials: { sources: 2, business_pending: 1, intake_pending: 0, missing: 0 },
  drafts: { pending: 1, approved: 0, exported: 0 },
  exceptions: { open: 0, awaiting_info: 0, overdue: 0 },
  delivery: { unverified: 0, unknown: 0, differences: 0, verified: 0 },
  closing: { state: 'recheck', blockers: 1, snapshot_id: null },
  efficiency: { reviews: 0, manual_actions: 0, handling_seconds: null },
  permissions: { edit: true, review: true, export: true, manage: true },
  service_plan: {
    version: 3,
    lead_user_id: 'u1',
    reviewer_user_id: 'u2',
    backup_user_id: null,
    contact_user_id: null,
    due_on: '2026-11-10',
  },
  material_requests: null,
  warnings: [],
  work: { owner: '会计', due_on: null, reason: '待复核', target: 'review', label: '会计复核' },
};

describe('monthlyStages', () => {
  it('projects six stages without inventing a second business state', () => {
    const opened: string[] = [];
    const stages = monthlyStages(base, (destination) => opened.push(destination.view));
    expect(stages.map((stage) => stage.key)).toEqual([
      'intake',
      'reconcile',
      'prepare',
      'review',
      'delivery',
      'archive',
    ]);
    expect(stages.filter((stage) => stage.current).map((stage) => stage.key)).toEqual(['review']);
    expect(stages.find((stage) => stage.key === 'review')?.primary).toBe('1 待审');
    stages.find((stage) => stage.key === 'review')?.onOpen?.();
    expect(opened).toEqual(['review']);
  });

  it('uses attention order when the server has no next target', () => {
    const stages = monthlyStages(
      { ...base, work: undefined, exceptions: { ...base.exceptions, open: 2 } },
      () => {},
    );
    expect(stages.find((stage) => stage.current)?.key).toBe('reconcile');
    expect(stages.find((stage) => stage.key === 'reconcile')?.attention).toBe(true);
  });

  it('does not provide a destination when the server withholds that scope', () => {
    const stages = monthlyStages(
      {
        ...base,
        permissions: { ...base.permissions, review: false },
        work: undefined,
      },
      () => {},
    );
    expect(stages.find((stage) => stage.key === 'review')?.onOpen).toBeUndefined();
    expect(stages.find((stage) => stage.key === 'archive')?.onOpen).toBeUndefined();
  });
});
