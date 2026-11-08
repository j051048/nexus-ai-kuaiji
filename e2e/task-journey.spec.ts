import { test, expect } from '@playwright/test';
import { mkdirSync } from 'node:fs';
import { monthlyFixture } from './formal-fixtures';

for (const [device, viewport] of [
  ['desktop', { width: 1365, height: 900 }],
  ['mobile', { width: 390, height: 844 }],
] as const) {
  test(`${device} 在交付待办中实际生成包，返回仍等待客户本人签收`, async ({ page, request }) => {
    test.setTimeout(120_000);
    const f = await monthlyFixture(request);
    await page.setViewportSize(viewport);
    await page.goto('/workspace/inbox');
    const row = page.locator('.firm-inbox-delivery-row').filter({ hasText: f.book.name });
    await expect(row).toBeVisible();
    if (device === 'mobile') {
      const description = page.locator('.firm-inbox-delivery-heading > div');
      expect((await description.boundingBox())!.width).toBeGreaterThanOrEqual(viewport.width - 80);
    }
    await row.getByRole('button', { name: '准备月度交付包', exact: true }).click();
    const panel = page.getByRole('region', { name: '交付待办办理', exact: true });
    await panel.getByLabel('交付报表版本').selectOption(f.report.report.id);
    await panel.getByLabel('交付或签收说明').fill('虚构企业复核完成后准备本月交付');
    await panel.getByLabel('我已核对本企业、本月份及本次版本').check();
    await panel.getByRole('button', { name: '生成本月交付包', exact: true }).click();
    await expect(panel.getByText('已冻结月度交付包 V1', { exact: false })).toBeVisible();
    await panel.getByRole('button', { name: '返回待办列表', exact: true }).click();
    await expect(row.getByRole('button', { name: '查看签收进度', exact: true })).toBeVisible();
    await expect(row).toContainText('客户未签收');
    await row.getByRole('button', { name: '查看签收进度', exact: true }).click();
    await expect(panel).toContainText('由客户本人确认签收');
    await expect(panel.getByRole('button', { name: '确认签收', exact: true })).toHaveCount(0);
  });
  test(`${device} 事务所待办直达月结，返回保留分类并重新读取`, async ({ page }) => {
    test.setTimeout(90_000);
    await page.setViewportSize(viewport);
    await page.goto('/workspace/inbox');
    await page.getByRole('tab', { name: /^阻塞/ }).click();
    const row = page
      .locator('.firm-inbox-delivery-row')
      .filter({ has: page.getByRole('button', { name: '去处理月结阻断', exact: true }) })
      .first();
    await expect(row).toBeVisible();
    await row.getByRole('button', { name: '去处理月结阻断', exact: true }).click();
    const panel = page.getByRole('region', { name: '交付待办办理' });
    await expect(panel).toBeVisible();
    const read = page.waitForResponse(
      (r) => r.url().includes('/firm/inbox?bucket=blocked') && r.ok(),
    );
    await panel.getByRole('button', { name: '去处理月结阻断', exact: true }).click();
    await expect(page).toHaveURL(/workspace\/closing/);
    await page.getByRole('button', { name: '返回事务所待办', exact: true }).click();
    await read;
    await expect(page.getByRole('tab', { name: /^阻塞/ })).toHaveAttribute('aria-selected', 'true');
    await expect(page.locator('.firm-inbox-delivery-row').first()).toBeVisible();
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth),
    ).toBeTruthy();
    mkdirSync('tmp_test/task-journey-screenshots', { recursive: true });
    await page.screenshot({
      path: `tmp_test/task-journey-screenshots/${device}.png`,
      fullPage: true,
    });
  });
  test(`${device} 迟到或错范围的交付待办不可办理`, async ({ page }) => {
    await page.setViewportSize(viewport);
    await page.goto('/workspace/inbox');
    await page.route('**/api/books/*/delivery/monthly-followup?*', async (route) => {
      const response = await route.fetch();
      const value = await response.json();
      await route.fulfill({ response, json: { ...value, company_id: 'other-company' } });
    });
    await page.locator('.firm-inbox-delivery-row').first().getByRole('button').click();
    await expect(page.getByRole('alert').filter({ hasText: '待办范围或来源已变化' })).toBeVisible();
    await expect(page.getByRole('region', { name: '交付待办办理' })).toHaveCount(0);
    await expect(page).toHaveURL(/workspace\/inbox/);
  });
}
