import { expect, test } from '@playwright/test';
import { choosePersona, openAccounting } from './ui-navigation';

test('本地演示身份自带独立复核人，可直接切换并进入客户企业', async ({ page }) => {
  await page.goto('/');
  await choosePersona(page, 'reviewer');
  await expect(page.getByText('示例复核会计 小吴').first()).toBeVisible();
  await openAccounting(page);
  await expect(page.getByLabel('搜索企业名称').first()).toBeVisible();
  await expect(page.getByRole('navigation', { name: '主导航', exact: true })).toBeVisible();
  await page.screenshot({ path: 'artifacts/demo-reviewer.png', fullPage: true });
});
