import { expect, test } from '@playwright/test';
import { choosePersona } from './ui-navigation';

test.setTimeout(90_000);

test('R1A 导航唯一高亮、菜单目标与能力过滤', async ({ page }) => {
  await page.goto('/', { waitUntil: 'domcontentloaded' });
  await choosePersona(page, 'accountant');
  const nav = page.getByRole('navigation', { name: '主导航' });
  const primary = nav.locator(':scope > button');
  expect(await primary.count()).toBeLessThanOrEqual(6);
  const review = nav.getByRole('button', { name: '跨企业批量复核', exact: true });
  await expect(review).toBeVisible();
  await review.click();
  await expect(page).toHaveURL(/\/workspace\/review/);
  await expect(page.getByRole('heading', { name: '集中复核，把时间留给判断。' })).toBeVisible({
    timeout: 30_000,
  });
  await expect(nav.locator('[aria-current="page"]')).toHaveCount(1);

  await page.goto('/workspace/cases', { waitUntil: 'domcontentloaded' });
  await expect(nav.locator('[aria-current="page"]')).toHaveCount(1);
  await expect(nav.locator('[aria-current="page"]')).toHaveAccessibleName('异常与补件');
});

test('R1A 负责人根入口落到全所概览，显式深链接不被吞掉', async ({ page }) => {
  await page.goto('/', { waitUntil: 'domcontentloaded' });
  await choosePersona(page, 'accountant');
  await expect(page).toHaveURL(/\/workspace\/firm/);
  await expect(page.getByRole('heading', { name: '事务所运营驾驶舱' })).toBeVisible({
    timeout: 30_000,
  });

  await page.goto('/workspace/home', { waitUntil: 'domcontentloaded' });
  await expect(page).toHaveURL(/\/workspace\/home/);
  await expect(page.getByRole('heading', { name: '会计工作台' })).toBeVisible({
    timeout: 30_000,
  });

  await page.goto('/workspace/cases', { waitUntil: 'domcontentloaded' });
  await expect(page).toHaveURL(/\/workspace\/cases/);
  await expect(page.getByRole('heading', { name: '异常与补件' })).toBeVisible({
    timeout: 30_000,
  });
});

test('R1A 客户角色默认首页和主导航不出现会计入口', async ({ page }) => {
  await page.goto('/', { waitUntil: 'domcontentloaded' });
  await choosePersona(page, 'employee');
  await expect(page.locator('.client-portal')).toBeVisible();
  await expect(page.getByRole('navigation', { name: '客户服务' })).toBeVisible();
  await expect(page.getByRole('button', { name: '提交', exact: true })).toBeVisible();
  await expect(page.getByRole('navigation', { name: '主导航' })).toHaveCount(0);
});
