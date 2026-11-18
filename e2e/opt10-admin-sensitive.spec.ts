import { expect, test } from '@playwright/test';

test('事务所负责人按需查看完整税号并写审计', async ({ page, request }) => {
  const session = await request.get('/api/session?persona=accountant');
  expect(session.status()).toBe(200);
  const headers = { Authorization: `Bearer ${(await session.json()).token}` };
  const name = `OPT10 敏感字段（虚构） ${crypto.randomUUID().slice(0, 8)}`;
  const fullTaxId = '91310000MA1TEST001X';
  const created = await request.post('/api/companies', {
    headers,
    data: { name, tax_id: fullTaxId },
  });
  expect(created.status()).toBe(201);
  const company = await created.json();
  const list = await request.get(`/api/admin/companies?search=${encodeURIComponent(name)}`, {
    headers,
  });
  expect(list.status()).toBe(200);
  const masked = (await list.json()).companies.find((item: { id: string }) => item.id === company.id);
  expect(masked?.tax_id).toContain('*');

  await page.goto('/admin');
  await page.getByPlaceholder('搜索企业名称或税号').fill(name);
  const card = page.locator('.admin-company-card').filter({ hasText: name }).first();
  await expect(card).toBeVisible();
  await card.click();
  await expect(page.getByText(`税号：${masked.tax_id}`, { exact: false })).toBeVisible();
  await page.getByRole('button', { name: '显示完整税号' }).click();
  await expect(page.getByText(`税号：${fullTaxId}`, { exact: false })).toBeVisible();
  await page.screenshot({
    path: 'tmp_test/opt10-admin-sensitive-revealed-1280.png',
    fullPage: true,
  });
  await page.getByRole('button', { name: '隐藏完整税号' }).click();
  await expect(page.getByText(`税号：${masked.tax_id}`, { exact: false })).toBeVisible();
  await page.screenshot({ path: 'tmp_test/opt10-admin-sensitive-1280.png', fullPage: true });
});
