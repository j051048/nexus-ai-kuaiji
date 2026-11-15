import { test, expect } from '@playwright/test';
import { checked } from './formal-books-fixtures';

for (const width of [1280, 390]) {
test(`待办跨页选择保留稳定 ID 并提交完整集合 ${width}`, async ({ page, request }) => {
  await page.setViewportSize({ width, height: 900 });
  const session = await checked(await request.get('/api/session?persona=accountant'), 200);
  const me = await checked(
    await request.get('/api/me', { headers: { Authorization: `Bearer ${session.token}` } }),
    200,
  );
  const rows = Array.from({ length: 21 }, (_, index) => ({
    kind: 'review', ref: `draft-${index + 1}`, company_id: 'company-1',
    company_name: '跨页虚构企业', title: `跨页待复核 ${index + 1}`, state: '待复核',
    responsible: '会计复核员', action_label: '同屏复核', due_on: '',
    updated_at: '2026-11-12T00:00:00+00:00', assignment_note: '',
    book_id: 'book-1', event_id: null, draft_id: `draft-${index + 1}`,
    batch_id: null, assignee_id: null, assignee_name: null, assignment_version: 1,
    bucket: 'review', task_bucket: 'action', permissions: { assign: true, remind: true, claim: false },
  }));
  await page.route('**/api/firm/inbox**', async (route) => {
    const url = new URL(route.request().url());
    const offset = Number(url.searchParams.get('offset') || 0);
    const bucket = url.searchParams.get('bucket') || 'all';
    const body = {
      scope: { firm_id: me.current_firm_id, user_id: me.user.id, bucket },
      counts: { today: 0, overdue: 0, blocked: 0, review: 21, failed: 0 },
      items: rows.slice(offset, offset + 20),
      assignees: [{ id: 'member-1', name: '接单同事', source: 'firm' }],
      note: '本地虚构',
      pagination: { total: 21, limit: 20, offset, has_more: offset + 20 < 21 },
    };
    await route.fulfill({ status: 200, json: body });
  });
  let submitted: { tasks: { kind: string; ref: string }[] } | null = null;
  await page.route('**/api/firm/tasks/assign', async (route) => {
    submitted = route.request().postDataJSON();
    await route.fulfill({ status: 200, json: { assigned: 2, failures: [] } });
  });
  await page.goto('/workspace/inbox');
  await page.getByRole('heading', { name: '待处理与人员分工' }).waitFor();
  await page.getByRole('checkbox', { name: '选择 跨页待复核 1', exact: true }).check();
  await expect(page.getByText('已选 1 项（跨页保留）')).toBeVisible();
  await page.getByRole('button', { name: '下一页', exact: true }).click();
  await expect(page.getByText('跨页待复核 21')).toBeVisible();
  await expect(page.getByText('已选 1 项（跨页保留）')).toBeVisible();
  await page.getByRole('checkbox', { name: '选择 跨页待复核 21', exact: true }).check();
  await page.getByLabel('指派给').selectOption('member-1');
  await page.getByRole('button', { name: '批量分派' }).click();
  await expect.poll(() => submitted?.tasks.length).toBe(2);
  expect(submitted!.tasks).toEqual([{ kind: 'review', ref: 'draft-1' }, { kind: 'review', ref: 'draft-21' }]);
});
}
