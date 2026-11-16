import { expect, test } from '@playwright/test';

for (const viewport of [
  { width: 1366, height: 900 },
  { width: 390, height: 844 },
]) {
  test(`firm insights overview is readable and traceable ${viewport.width}`, async ({ page }) => {
    await page.setViewportSize(viewport);
    if (viewport.width > 640) {
      await page.goto('/workspace/accounting');
      await page.getByRole('button', { name: '经营看板' }).click();
      await expect(page).toHaveURL(/\/workspace\/insights/);
    } else {
      await page.goto('/workspace/insights');
    }
    await expect(page.getByRole('heading', { name: '经营看板', exact: true })).toBeVisible();
    if (viewport.width > 640) await expect(page.getByRole('button', { name: /经营看板/ }).first()).toBeVisible();
    const main = page.locator('main[aria-label="经营看板"]');
    await expect(main.getByText('服务企业', { exact: true })).toBeVisible();
    await expect(main.getByText('待复核凭证', { exact: true })).toBeVisible();
    await expect(main.getByText('财务已覆盖', { exact: true })).toBeVisible();
    await expect(main.getByText('交付阶段', { exact: true })).toBeVisible();
    await expect(main.getByText('近六个月趋势', { exact: true })).toBeVisible();
    await main.getByRole('button', { name: '财务' }).click();
    await expect(main.getByText('无已复核报表的月份不补 0', { exact: true })).toBeVisible();
    await expect(main.getByRole('button', { name: '财务' })).toHaveAttribute('aria-pressed', 'true');
    await page.screenshot({ path: `tmp_test/firm-insights-finance-${viewport.width}.png`, fullPage: false });
    await main.getByRole('button', { name: '运营' }).click();
    await expect(main.getByText('风险与逾期', { exact: true })).toBeVisible();
    await expect(main.getByText('人员负载', { exact: true })).toBeVisible();
    await expect(main.locator('.firm-insights-note')).toContainText('只读');
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1)).toBe(true);
    await page.screenshot({ path: `tmp_test/firm-insights-${viewport.width}.png`, fullPage: true });
  });
}

test('firm insights phase two and three views are reachable 1366', async ({ page }) => {
  await page.setViewportSize({ width: 1366, height: 900 });
  await page.goto('/workspace/insights');
  const main = page.locator('main[aria-label="经营看板"]');
  await expect(main.getByRole('heading', { name: '经营看板', exact: true })).toBeVisible();

  await main.getByRole('button', { name: '企业画像', exact: true }).click();
  await expect(main.getByRole('heading', { name: /虚构|企业|账套/ }).first()).toBeVisible();
  await expect(main.getByText('资产负债结构', { exact: true })).toBeVisible();
  await expect(main.getByText('账龄', { exact: true })).toBeVisible();
  await page.screenshot({ path: 'tmp_test/firm-insights-company-1366.png', fullPage: true });

  await main.getByRole('button', { name: '企业对比', exact: true }).click();
  await expect(main.getByRole('heading', { name: '企业对比', exact: true })).toBeVisible();
  await expect(main.getByRole('button', { name: '导出 CSV' })).toBeEnabled();
  await expect(main.locator('.firm-insights-compare tbody tr').first()).toBeVisible();
  const download = page.waitForEvent('download');
  await main.getByRole('button', { name: '导出 CSV' }).click();
  expect((await download).suggestedFilename()).toMatch(/firm-comparison-\d{4}-\d{2}\.csv/);
  await page.screenshot({ path: 'tmp_test/firm-insights-compare-1366.png', fullPage: true });

  await main.getByRole('button', { name: '交付时间线', exact: true }).click();
  await expect(main.getByRole('heading', { name: '交付时间线', exact: true })).toBeVisible();
  await expect(main.locator('.firm-insights-gantt-row').first()).toBeVisible();
  await page.screenshot({ path: 'tmp_test/firm-insights-timeline-1366.png', fullPage: true });
});

test('firm insights phase two and three views are readable 390', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/workspace/insights');
  const main = page.locator('main[aria-label="经营看板"]');
  await main.getByRole('button', { name: '企业画像', exact: true }).click();
  await expect(main.getByText('资产负债结构', { exact: true })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1)).toBe(true);
  await page.screenshot({ path: 'tmp_test/firm-insights-company-390.png', fullPage: true });
  await main.getByRole('button', { name: '企业对比', exact: true }).click();
  await expect(main.getByRole('heading', { name: '企业对比', exact: true })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1)).toBe(true);
  await main.getByRole('button', { name: '交付时间线', exact: true }).click();
  await expect(main.locator('.firm-insights-gantt-row').first()).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1)).toBe(true);
});
