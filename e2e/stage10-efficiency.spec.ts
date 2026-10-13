import { openAccounting, openBookTools } from './ui-navigation';
import { expect, test } from '@playwright/test';

test('人工工作须明确开启计时，返回复核页仍可管理，完成后保留服务器记录', async ({ page, request }) => {
  const session = await (await request.get('/api/session?persona=accountant')).json(), headers = { Authorization: `Bearer ${session.token}` };
  const name = `合成人工效率 ${crypto.randomUUID().slice(0, 8)}`;
  const created = await request.post('/api/books', { headers, data: { name, period: '2026-10' } }); expect(created.status()).toBe(201); const book = await created.json();
  await page.goto('/'); await openAccounting(page); await page.getByLabel('搜索企业名称').fill(name); await page.getByRole('listitem').filter({ hasText: name }).click();
  await openBookTools(page); await page.getByRole('button', { name: '人工效率', exact: true }).click();
  await expect(page.getByRole('heading', { name: '用真实处理时间，衡量自动化。', exact: true })).toBeVisible();
  await page.getByRole('button', { name: '开始计时', exact: true }).click();
  await expect(page.getByRole('button', { name: '暂停', exact: true })).toBeEnabled();
  await page.getByRole('button', { name: '返回工作台', exact: true }).click();
  await expect(page.getByLabel('已开启的人工工作计时')).toContainText('当前账套有操作时记录');
  await page.getByRole('button', { name: '管理计时', exact: true }).click();
  await page.getByRole('button', { name: '暂停', exact: true }).click();
  await expect(page.getByRole('button', { name: '继续计时', exact: true })).toBeEnabled();
  await page.getByRole('button', { name: '完成此次工作', exact: true }).click();
  await expect(page.getByRole('button', { name: '开始计时', exact: true })).toBeEnabled();
  const own = await (await request.get('/api/efficiency/sessions', { headers })).json();
  const completed = own.sessions.find((row: { book_id: string }) => row.book_id === book.id);
  expect(completed.state).toBe('completed'); expect(completed.credited_seconds).toBeLessThanOrEqual(60);
  const employee = await (await request.get('/api/session?persona=employee')).json();
  expect((await request.get(`/api/books/${book.id}/efficiency`, { headers: { Authorization: `Bearer ${employee.token}` } })).status()).toBe(404);
  await page.screenshot({ path: 'artifacts/stage10-efficiency.png', fullPage: true });
});
