import { expect, test, type Page } from '@playwright/test';
import { choosePersona } from './ui-navigation';

async function reviewFromQueue(page: Page, summary: string, viewport: { width: number; height: number }) {
  await page.setViewportSize(viewport);
  await page.goto('/workspace/home');
  await choosePersona(page, 'reviewer');
  const card = page.locator('article.workspace-task').filter({ hasText: summary }).first();
  await expect(card).toBeVisible();
  await card.scrollIntoViewIfNeeded();
  const before = await page.evaluate(() => window.scrollY);
  await card.getByRole('button', { name: '同屏复核', exact: true }).click();
  const drawer = page.getByRole('dialog', { name: '原件与凭证，一起核对', exact: true });
  await drawer.getByText('来源金额', { exact: true }).waitFor({ timeout: 15_000 });
  await drawer.getByRole('button', { name: '查看原件', exact: true }).click();
  await expect(drawer.getByText('原始附件', { exact: true })).toBeVisible();
  await drawer.getByRole('button', { name: '核对分录', exact: true }).click();
  await expect(drawer.getByText('来源金额', { exact: true })).toBeVisible();
  await drawer.getByLabel('我已核对同屏原件与分录', { exact: true }).check();
  await expect(drawer.getByRole('button', { name: '确认审核', exact: true })).toBeEnabled();
  await drawer.getByRole('button', { name: '确认审核', exact: true }).click();
  await expect(drawer.getByText('凭证已审核，后续仍由会计执行导出')).toBeVisible();
  await drawer.getByRole('button', { name: '返回列表', exact: true }).click();
  await expect(drawer).toHaveCount(0);
  await expect(page.locator('article.workspace-task').first()).toBeVisible();
  await expect(page.locator('article.workspace-task').filter({ hasText: summary })).toHaveCount(0);
  await expect(page).toHaveURL(/\/workspace\/home/);
  await expect
    .poll(() => page.evaluate(() => window.scrollY), { timeout: 3_000 })
    .toBeGreaterThanOrEqual(Math.max(0, before - 2));
  await page.screenshot({
    path: `tmp_test/plt03c-reviewer-chain-${viewport.width}.png`,
    fullPage: false,
  });
}

test('复核人从待办进入同屏复核并办理，1366 返回原队列', async ({ page }) => {
  test.setTimeout(45_000);
  await reviewFromQueue(page, '项目服务收入', { width: 1366, height: 768 });
});

test('复核人从待办进入同屏复核并办理，390 返回原队列', async ({ page }) => {
  test.setTimeout(45_000);
  await reviewFromQueue(page, '宽带通信费', { width: 390, height: 844 });
});
