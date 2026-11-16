import { expect, test } from '@playwright/test';
import { taxFullFixture } from './n04-tax-fixtures';

test('K05 employee sees OA material request without manager actions', async ({ page, request }) => {
  const f = await taxFullFixture(request);
  const emp = await (await request.get('/api/session?persona=employee')).json();
  const me = await (await request.get('/api/me', { headers: { Authorization: `Bearer ${emp.token}` } })).json();
  const companies = new Set(me.company_memberships.map((item: { id: string }) => item.id));
  const workbench = await (await request.get('/api/workbench?limit=100', { headers: f.headers })).json();
  const shared = workbench.books.find((book: { company_id: string }) => companies.has(book.company_id));
  expect(shared).toBeTruthy();
  const book = shared as { id: string; period: string };
  const snapshot = await request.post(`/api/books/${book.id}/oa-snapshots`, {
    headers: f.headers,
    data: {
      oa_event_id: 'OA-EMP-1', oa_version: 1, period: book.period,
      title: '员工差旅补件', approved_by: '虚构 OA 审批人',
      approved_at: '2026-10-15T09:00:00+08:00',
      lines: [{ account_code: '6602.02', amount_cents: 12850, description: '差旅' }],
    },
  });
  expect(snapshot.status()).toBe(201);
  const id = (await snapshot.json()).id;
  const created = await request.post(`/api/books/${book.id}/oa-snapshots/material-requests`, {
    headers: f.headers,
    data: { snapshot_id: id, title: '员工差旅补件', reason: '补交审批依据与原始附件' },
  });
  expect(created.status()).toBe(201);

  await page.goto('/');
  const sessionLoaded = page.waitForResponse((response) => response.url().includes('/api/session?persona=employee'));
  await page.getByLabel('切换验证身份').selectOption('employee');
  await sessionLoaded;
  await expect(page.getByRole('heading', { name: '我的工作台' })).toBeVisible();
  const panel = page.locator('section[aria-label="补件材料请求"]');
  await expect(panel).toBeVisible();
  await expect(panel).toContainText('员工差旅补件');
  await expect(panel).toContainText('补交审批依据与原始附件');
  await expect(panel.getByRole('button')).toHaveCount(0);
  await panel.scrollIntoViewIfNeeded();
  await page.screenshot({ path: 'tmp_test/tax-k-oa-employee.png', fullPage: false });
});
