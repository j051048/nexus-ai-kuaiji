import { expect, test, type Locator } from '@playwright/test';
import { choosePersona } from './ui-navigation';

async function textLines(locator: Locator) {
  return locator.evaluate((element) => {
    const range = document.createRange();
    range.selectNodeContents(element);
    return range.getClientRects().length;
  });
}

test('手机财务门户的品牌、设置和导航文字不被挤成逐字竖排', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/workspace/home', { waitUntil: 'domcontentloaded' });
  await choosePersona(page, 'finance');
  await expect(page.getByRole('region', { name: '资料核验与移交' })).toBeVisible();
  expect(await textLines(page.locator('.client-portal-mark'))).toBe(1);
  expect(await textLines(page.locator('.client-portal-settings > summary'))).toBe(1);
  for (const label of await page.locator('.client-portal-tabs button > span').all()) {
    expect(await textLines(label)).toBe(1);
  }
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
});
