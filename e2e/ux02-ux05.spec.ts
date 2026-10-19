import { expect, test } from '@playwright/test';
import { choosePersona } from './ui-navigation';

test.setTimeout(90_000);

test('UX02/UX05 角色入口、助手范围与桌面手机截图', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.goto('/', { waitUntil: 'domcontentloaded' });
  await choosePersona(page, 'accountant');
  const nav = page.getByRole('navigation', { name: '主导航' });
  const primary = nav.locator(':scope > button');
  await expect(primary.first()).toBeVisible();
  expect(await primary.count()).toBeLessThanOrEqual(6);
  await expect(nav.getByRole('button', { name: '跨企业批量复核', exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: '问会计助手', exact: true })).toBeVisible();
  await page.screenshot({ path: 'docs/ux-screenshots/accounting-desktop.png', fullPage: true });

  await page.setViewportSize({ width: 375, height: 812 });
  await page.screenshot({ path: 'docs/ux-screenshots/accounting-mobile.png', fullPage: true });
  const body = await page.locator('body').evaluate((node) => node.scrollWidth <= node.clientWidth + 1);
  expect(body).toBe(true);

  await choosePersona(page, 'employee');
  await expect(page.getByRole('navigation', { name: '客户服务' })).toBeVisible();
  await expect(page.getByRole('button', { name: '提交', exact: true })).toBeVisible();
  await page.screenshot({ path: 'docs/ux-screenshots/client-mobile.png', fullPage: true });
});
