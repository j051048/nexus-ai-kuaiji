import { test, expect, type Page } from '@playwright/test';
import { choosePersona, openAccounting } from './ui-navigation';

async function openPanel(page: Page) {
  await page.goto('/');
  // Wait for the real initial profile before the mobile navigation helper.
  // This test injects late analysis results, not slow initial authentication.
  await expect(page.getByRole('region', { name: '当前登录身份' })).toBeVisible({
    timeout: 30_000,
  });
  await choosePersona(page, 'accountant');
  await openAccounting(page);
  await page.getByText('澄光科技（虚构示例）').last().click();
  await page.getByText('只读差异核对', { exact: true }).click();
}
for (const viewport of [
  { width: 1366, height: 800 },
  { width: 390, height: 844 },
]) {
  test(`IMP-R2 rejects foreign analysis atomically ${viewport.width}`, async ({ page }) => {
    await page.setViewportSize(viewport);
    await page.route('**/api/assistant/analyses', async (route) => {
      const response = await route.fetch();
      const value = await response.json();
      await route.fulfill({ json: { ...value, company_id: 'foreign', summary: 'FOREIGN_MARKER' } });
    });
    await openPanel(page);
    await page.getByRole('button', { name: '开始只读核对' }).click();
    await expect(page.locator('.analysis-evidence [role=alert]')).toContainText('范围或来源');
    await expect(page.locator('.analysis-evidence-result')).toHaveCount(0);
    await page.screenshot({ path: test.info().outputPath('foreign-rejected.png'), fullPage: true });
  });
  test(`IMP-R2 drops late result after target change ${viewport.width}`, async ({ page }) => {
    await page.setViewportSize(viewport);
    let release = () => {};
    const wait = new Promise<void>((resolve) => {
      release = resolve;
    });
    let loaded = () => {};
    const ready = new Promise<void>((resolve) => {
      loaded = resolve;
    });
    await page.route('**/api/assistant/analyses', async (route) => {
      const response = await route.fetch();
      const value = await response.json();
      loaded();
      await wait;
      await route.fulfill({ json: { ...value, summary: 'STALE_MARKER' } }).catch(() => {});
    });
    await openPanel(page);
    await page.getByRole('button', { name: '开始只读核对' }).click();
    await ready;
    await page.getByLabel('核对类型').selectOption('invoice_voucher_diff');
    release();
    await expect(page.getByRole('button', { name: '核对中…' })).toHaveCount(0);
    await expect(page.locator('.analysis-evidence-result')).toHaveCount(0);
  });
}
