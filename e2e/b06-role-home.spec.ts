import { expect, test } from '@playwright/test';
import { choosePersona } from './ui-navigation';

test.setTimeout(90_000);

test('B06 角色首页显示服务端授权能力，员工切换后不保留会计入口', async ({ page }) => {
  await page.goto('/workspace/home', { waitUntil: 'domcontentloaded' });
  const guide = page.getByRole('region', { name: '当前职责与授权范围', exact: true });
  await expect(guide).toBeVisible({ timeout: 30_000 });
  await expect(guide).toContainText('当前工作台');
  await expect(guide).toContainText('凭证复核与审核后导出');
  await expect(guide).toContainText('服务端逐次校验');
  await page.screenshot({ path: 'artifacts/b06-role-home-desktop.png', fullPage: false });

  await choosePersona(page, 'employee');
  await expect(page.locator('.client-portal')).toBeVisible({ timeout: 30_000 });
  await expect(page.getByRole('heading', { name: '我的工作台', exact: true })).toBeVisible();
  await expect(page.getByRole('region', { name: '当前职责与授权范围', exact: true })).toContainText(
    '提交本人事项与补件',
  );
  await expect(page.getByRole('navigation', { name: '主导航', exact: true })).toHaveCount(0);
  await expect(page.getByRole('button', { name: '客户企业', exact: true })).toHaveCount(0);
});
