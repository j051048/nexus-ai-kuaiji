import { expect, test } from '@playwright/test';
import { openTax, taxFullFixture } from './n04-tax-fixtures';

for (const viewport of [{ width: 1280, height: 1000 }, { width: 390, height: 844 }]) {
test(`TAX04 uploads a synthetic transport ledger and shows row evidence ${viewport.width}`, async ({ page, request }) => {
  test.setTimeout(120_000);
  const f = await taxFullFixture(request);
  await page.setViewportSize(viewport);
  await openTax(page, f);
  const panel = page.locator('section[aria-label="客户来源文件"]');
  await panel.scrollIntoViewIfNeeded();
  await panel.getByLabel('来源系统').fill('客户运输台账');
  await panel.getByLabel('来源批次 ID').fill('S-TAX04');
  await panel.getByLabel('版本').fill('1');
  await panel.getByLabel('页签').fill('运输明细');
  await panel.getByLabel('运输台账（xlsx）').setInputFiles('tmp_test/tax04-source.xlsx');
  await panel.getByRole('button', { name: '解析并保存来源' }).click();
  await expect(panel.getByText('来源文件已解析并保存为活动版本。')).toBeVisible();
  await expect(panel.getByText(/已采用 2 行、待核对 0 行、排除 1 行/)).toBeVisible();
  await expect(panel.getByText('¥30.00 / 3.500')).toBeVisible();
  await expect(panel.getByText('L1')).toBeVisible();
  await panel.locator('tr').filter({ hasText: 'L3' }).getByRole('button', { name: '改为采用' }).click();
  await expect(panel.getByText(/已采用 3 行、待核对 0 行、排除 0 行/)).toBeVisible();
  await expect(panel.getByText('来源行核定已保存，未冻结输出需要重新核对。')).toBeVisible();
  await page.screenshot({ path: `tmp_test/tax04-source-${viewport.width}.png`, fullPage: true });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
});
}
