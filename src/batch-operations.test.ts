import { describe, expect, it } from 'vitest';
import {
  acceptPlan,
  itemActions,
  itemLabel,
  acceptBatch,
  acceptHandoffs,
  acceptAssignment,
  batchResultUrl,
} from './batch-operations';

const plan = {
  period: '2026-11',
  ready: 1,
  blocked: 0,
  manifest: 'a'.repeat(64),
  companies: [
    {
      company_id: 'a',
      book_id: 'book-a',
      drafts: [{ draft_id: 'd', version: 2 }],
      manifest: 'b'.repeat(64),
    },
  ],
};
describe('批量办理可信边界', () => {
  it('指派回执必须对应来源版本、指纹、账套和接手人', () => {
    const row = {
      source: { kind: 'tax_workpaper' as const, id: 'w', version: 2, fingerprint: 'c'.repeat(64) },
      scope: { book_id: 'b', period: '2026-11', company_name: '甲', book_name: '甲账' },
      maker_id: 'maker',
      role: 'reviewer',
      next_action: '复核',
      assignee_id: null,
      status: 'pending',
      due_on: null,
    };
    const reply = { ...row, assignee_id: 'reviewer', status: 'assigned' };
    expect(() => acceptAssignment(reply, row, 'reviewer')).not.toThrow();
    for (const bad of [
      { ...reply, source: { ...reply.source, version: 1 } },
      { ...reply, scope: { book_id: 'foreign' } },
      { ...reply, assignee_id: 'maker' },
    ])
      expect(() => acceptAssignment(bad, row, 'reviewer')).toThrow();
  });
  it('成功结果仅链接精确包或导出记录；未完成没有办理链接', () => {
    const row = {
      id: 'i',
      company_id: 'co',
      company_name: '甲',
      status: 'succeeded',
      result: { book_id: 'b', package_id: 'p' },
    };
    expect(batchResultUrl('package_draft', row, '2026-11')).toContain('package=p');
    expect(batchResultUrl('package_draft', { ...row, status: 'running' }, '2026-11')).toBeNull();
    expect(
      batchResultUrl('package_draft', { ...row, result: { book_id: 'b' } }, '2026-11'),
    ).toBeNull();
  });
  it('批次不能将未知状态或重复企业当作已核验结果', () => {
    const base = {
      id: 'b',
      kind: 'review_export',
      status: 'attention',
      progress: { total: 1, completed: 1, percent: 100 },
      items: [{ id: 'i', company_id: 'a', company_name: '', status: 'invented' }],
    };
    expect(() => acceptBatch(base, 'b')).toThrow();
    const repeated = {
      ...base,
      progress: { total: 2, completed: 2, percent: 100 },
      items: [
        { ...base.items[0], status: 'failed' },
        { ...base.items[0], id: 'j', status: 'failed' },
      ],
    };
    expect(() => acceptBatch(repeated, 'b')).toThrow();
  });
  it('预检绑定本次企业集合、期间与已审核版本', () => {
    expect(acceptPlan(plan, ['a'], '2026-11', 'review_export')).toEqual(plan);
    for (const bad of [
      { ...plan, period: '2026-10' },
      { ...plan, companies: [...plan.companies, ...plan.companies] },
      { ...plan, ready: 2 },
      { ...plan, manifest: '' },
      {
        ...plan,
        companies: [{ ...plan.companies[0], drafts: [{ draft_id: 'd', version: true }] }],
      },
    ]) {
      expect(() => acceptPlan(bad, ['a'], '2026-11', 'review_export')).toThrow();
    }
    expect(() => acceptPlan(plan, ['b'], '2026-11', 'review_export')).toThrow();
    expect(() => acceptPlan(plan, ['a'], '2026-11', 'package_draft')).toThrow();
  });
  it('运行中仅申请取消；未知、已完成和已取消不能重发', () => {
    expect(itemActions({ status: 'running' })).toEqual(['cancel']);
    expect(itemActions({ status: 'running', cancel_requested_at: 'now' })).toEqual([]);
    expect(itemLabel({ status: 'running', cancel_requested_at: 'now' })).toContain('取消已申请');
    expect(itemActions({ status: 'queued' })).toEqual(['cancel', 'skip']);
    for (const status of ['succeeded', 'cancelled', 'failed', 'skipped', 'unknown'])
      expect(itemActions({ status })).toEqual([]);
    expect(itemActions({ status: 'retry_wait', error_code: 'response_unknown' })).toEqual([]);
  });
  it('拒绝错任务与重复明细；接手队列拒绝伪造版本', () => {
    const b = {
      id: 'b',
      kind: 'review_export',
      status: 'attention',
      progress: { total: 0, completed: 0, percent: 100 },
      items: [],
    };
    expect(acceptBatch(b, 'b')).toEqual(b);
    expect(() => acceptBatch(b, 'other')).toThrow();
    expect(() =>
      acceptHandoffs({ items: [{ source: { kind: 'tax_workpaper', id: 'w', version: true } }] }),
    ).toThrow();
  });
});
