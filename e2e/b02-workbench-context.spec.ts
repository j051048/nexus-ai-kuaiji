import { expect, test } from '@playwright/test';
import { openNavigation } from './ui-navigation';

/**
 * Batch 02 workbench shell: the persistent context bar must state fact only
 * (unit, identity, period, lock state) and the monthly matrix must stay a
 * one-screen overview on both desktop and phone.
 */
test.describe('Batch 02 工作台上下文', () => {
  for (const viewport of [
    { name: 'desktop', width: 1366, height: 768 },
    { name: 'mobile', width: 390, height: 844 },
  ]) {
    test(`上下文条与矩阵在 ${viewport.name} 下保持事实与可达`, async ({ page }) => {
      test.setTimeout(120_000);
      await page.setViewportSize({ width: viewport.width, height: viewport.height });
      await page.goto('/');
      await openNavigation(page);

      const bar = page.getByRole('region', { name: '当前单位、身份与期间' });
      await expect(bar).toBeVisible();
      // Labels are hidden on the phone layout, so assert the facts themselves.
      await expect(bar.getByText('示例会计事务所')).toBeVisible();
      await expect(bar.getByText('事务所负责人')).toBeVisible();
      await expect(bar.getByRole('button', { name: '切换' })).toBeVisible();
      const period = bar.getByLabel('当前会计期间', { exact: true });
      await expect(period).toBeVisible();
      await expect(period).toHaveAttribute('type', 'month');

      // The bar reports the lock state from the server; it never claims "open"
      // when the request fails, so either wording is a fact.
      await expect(bar.getByText(/已锁期|未锁期|未选择账套|锁期状态待核实/)).toBeVisible();

      // No horizontal overflow on the phone layout.
      const overflow = await page.evaluate(
        () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
      );
      expect(overflow).toBeLessThanOrEqual(1);

      await page.screenshot({
        path: `artifacts/b02-context-${viewport.name}.png`,
        fullPage: false,
      });
    });
  }

  test('月度交付矩阵一屏可达', async ({ page }) => {
    test.setTimeout(120_000);
    await page.setViewportSize({ width: 1366, height: 768 });
    await page.goto('/');
    await openNavigation(page);
    await page.getByRole('button', { name: '月度交付', exact: true }).click();

    const matrix = page.getByRole('button', { name: '矩阵总览', exact: true });
    await expect(matrix).toBeVisible();
    await matrix.click();

    const table = page.getByRole('table').first();
    await expect(table).toBeVisible();
    for (const column of ['资料收集', '差异核对', '凭证准备', '复核', '交付', '归档']) {
      await expect(table.getByRole('columnheader', { name: new RegExp(column) })).toBeVisible();
    }
    await page.screenshot({ path: 'artifacts/b02-matrix.png', fullPage: false });
  });
});
