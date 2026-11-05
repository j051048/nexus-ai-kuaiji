import { expect, it } from 'vitest';
import { firmTaskTarget, parseFirmInbox } from './firm-inbox-contract';

const fixture = () => ({
  scope: { firm_id: 'a', user_id: 'reviewer', bucket: 'all' },
  counts: { today: 0, overdue: 0, blocked: 0, review: 1, failed: 0 },
  pagination: { total: 1, offset: 0, limit: 20, has_more: false },
  items: [
    {
      kind: 'review',
      ref: 'draft-a',
      company_id: 'company-a',
      company_name: '虚构企业',
      book_id: 'book-a',
      event_id: null,
      draft_id: 'draft-a',
      batch_id: null,
      title: '虚构凭证',
      state: 'draft',
      responsible: '会计复核',
      action_label: '同屏复核',
      due_on: '',
      updated_at: '2026-11-04',
      assignee_id: null,
      assignee_name: null,
      assignment_version: 0,
      assignment_note: '',
      bucket: 'review',
      task_bucket: 'action',
      permissions: { assign: false, remind: false, claim: true },
    },
  ],
  assignees: [],
  note: '指派不授予权限',
});
const parse = (v: unknown) => parseFirmInbox(v, 'a', 'reviewer', 'all', 0);
it('原事项入口保留企业、账套、草稿及办理岗位', () => {
  const value = parse(fixture());
  expect(value).not.toBeNull();
  expect(firmTaskTarget(value!.items[0])).toMatchObject({
    company_id: 'company-a',
    book_id: 'book-a',
    draft_id: 'draft-a',
    bucket: 'action',
    responsible: '会计复核',
  });
});
it.each(['firm_id', 'user_id', 'bucket'] as const)('拒绝错 %s 的迟到响应', (field) => {
  const value = fixture();
  value.scope[field] = 'wrong';
  expect(parse(value)).toBeNull();
});
it.each([true, -1, 0.5, '1', null])('拒绝非法计数 %s', (total) => {
  const v = fixture();
  expect(parse({ ...v, pagination: { ...v.pagination, total } })).toBeNull();
});
it('拒绝缺少权限、重复事项与错页', () => {
  const v = fixture();
  expect(parse({ ...v, items: [{ ...v.items[0], permissions: undefined }] })).toBeNull();
  expect(
    parse({ ...v, items: [...v.items, ...v.items], pagination: { ...v.pagination, total: 2 } }),
  ).toBeNull();
  expect(parse({ ...v, pagination: { ...v.pagination, offset: 20 } })).toBeNull();
});
