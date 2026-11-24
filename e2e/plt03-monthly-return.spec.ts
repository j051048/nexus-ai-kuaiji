import { expect, test } from '@playwright/test';

test('月度客户进入账簿后返回原月份与客户行', async ({ page }) => {
  test.setTimeout(90_000);
  await page.setViewportSize({ width: 1366, height: 900 });
  await page.goto('/workspace/monthly?period=2026-10');
  await expect(page.getByLabel('客户进度月份')).toHaveValue('2026-10');

  const rows = page.locator('.monthly-matrix-row:not(.monthly-skeleton-row)');
  await expect(rows.first()).toBeVisible({ timeout: 15_000 });
  const total = await rows.count();
  expect(total).toBeGreaterThan(0);
  const row = rows.filter({ has: page.getByRole('button', { name: /继续/ }) }).first();
  await expect(row).toBeVisible({ timeout: 15_000 });
  await row.getByRole('button', { name: /继续/ }).first().click();
  await expect(page).toHaveURL(/\/workspace\/(accounting|review|service)\?/);

  await page.getByRole('button', { name: '返回客户月度进度', exact: true }).click();
  await expect(page).toHaveURL(/\/workspace\/monthly\?/);
  await expect(page.getByLabel('客户进度月份')).toHaveValue('2026-10');
  await expect(rows.first()).toBeVisible({ timeout: 15_000 });
});
