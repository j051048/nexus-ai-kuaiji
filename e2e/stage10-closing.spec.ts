import { openAccounting, openBookTools } from './ui-navigation';
import { expect, test } from '@playwright/test';

test('月结预检快照必须明确确认资料并经复核后才锁定期间', async ({ page, request }) => {
  const session = await (await request.get('/api/session?persona=accountant')).json(), headers = { Authorization: `Bearer ${session.token}` };
  const name = `第十阶段月结 ${crypto.randomUUID().slice(0, 8)}`;
  let response = await request.post('/api/books', { headers, data: { name, period: '2026-10' } }); expect(response.status()).toBe(201); const book = await response.json();
  await page.goto('/'); await page.getByLabel('切换验证身份').selectOption('accountant');
  await openAccounting(page); await page.getByLabel('搜索企业名称').fill(name); await page.getByRole('listitem').filter({ hasText: name }).click();
  await openBookTools(page); await page.getByRole('button', { name: '月结预检', exact: true }).click();
  await expect(page.getByRole('heading', { name: '先核清，再合上这个月。', exact: true })).toBeVisible();
  await page.getByRole('button', { name: '生成月结预检快照', exact: true }).click();
  await expect(page.getByRole('status')).toContainText('预检快照已保存');
  await expect(page.getByRole('button', { name: '依据复核快照锁期', exact: true })).toBeDisabled();
  await page.getByLabel('我已核对本期资料与银行流水完整性，确认当前预检没有遗漏', { exact: true }).check();
  await page.getByLabel('月结复核依据', { exact: true }).fill('虚构空期间，已核对银行账户和本期资料均完整');
  await page.getByRole('button', { name: '复核当前快照', exact: true }).click();
  await expect(page.getByRole('status')).toContainText('当前快照已明确复核');
  await page.getByRole('button', { name: '依据复核快照锁期', exact: true }).click();
  await expect(page.getByRole('status')).toContainText('本期已依据当前复核快照锁定');
  await expect(page.getByText('本期已锁定', { exact: true })).toBeVisible();
  response = await request.get(`/api/books/${book.id}/closing`, { headers }); const detail = await response.json();
  expect(detail.period_status).toBe('closed'); expect(detail.receipt.snapshot_id).toBe(detail.snapshots[0].id); expect(detail.snapshots[0].historical).toBe(true);
  response = await request.post(`/api/books/${book.id}/closing/preflights`, { headers, data: { idempotency_key: crypto.randomUUID() } }); expect(response.status()).toBe(403);
  await page.screenshot({ path: 'artifacts/stage10-month-close.png', fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 }); expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
});
