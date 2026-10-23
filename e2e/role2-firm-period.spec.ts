import { expect, test } from '@playwright/test';

test('全所工作台切换月份后，客户进度与客户开通保留所选月份', async ({ page }) => {
  await page.goto('/workspace/firm', { waitUntil: 'domcontentloaded' });
  await expect(page.getByRole('heading', { name: '全所工作台', exact: true })).toBeVisible();
  await page.getByLabel('服务月份', { exact: true }).fill('2025-12');
  await expect(page.getByRole('button', { name: '客户进度', exact: true })).toBeVisible();
  await page.getByRole('button', { name: '客户进度', exact: true }).click();
  await expect(page).toHaveURL(/\/workspace\/monthly/);
  await expect(page.getByLabel('客户进度月份', { exact: true })).toHaveValue('2025-12');
  await page.goto('/workspace/firm', { waitUntil: 'domcontentloaded' });
  await page.getByLabel('服务月份', { exact: true }).fill('2025-11');
  await expect(page.getByRole('button', { name: '客户进度', exact: true })).toBeVisible();
  await page.getByRole('button', { name: '新增客户', exact: true }).first().click();
  await expect(page.getByRole('heading', { name: '客户标准化开通', exact: true })).toBeVisible();
  await expect(page.getByLabel('开通月份', { exact: true })).toHaveValue('2025-11');
});
