import { expect, test } from '@playwright/test';
import { choosePersona } from './ui-navigation';

test('事务所驾驶舱、客户开通与客户极简门户可用', async ({ page }) => {
  test.setTimeout(120_000);
  await page.goto('/workspace/firm', { waitUntil: 'domcontentloaded' });
  await expect(page.getByRole('heading', { name: '事务所运营驾驶舱', exact: true })).toBeVisible({
    timeout: 60_000,
  });
  await expect(page.getByLabel('全所运营概览')).toBeVisible();

  await page.goto('/workspace/onboarding', { waitUntil: 'domcontentloaded' });
  await expect(page.getByRole('heading', { name: '客户标准化开通', exact: true })).toBeVisible({
    timeout: 60_000,
  });
  await expect(page.getByLabel('目标企业')).toBeVisible({ timeout: 30_000 });

  await page.goto('/', { waitUntil: 'domcontentloaded' });
  await choosePersona(page, 'employee');
  const portal = page.getByRole('navigation', { name: '客户服务', exact: true });
  await expect(portal).toBeVisible({ timeout: 60_000 });
  const tabLabels = await portal.getByRole('button').allTextContents();
  expect(tabLabels.map((value) => value.trim())).toEqual(['待办', '提交', '资料清单', '本月简报']);
  await expect(page.getByRole('navigation', { name: '主导航' })).toHaveCount(0);
});
