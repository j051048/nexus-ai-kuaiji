import { test, expect } from '@playwright/test';
import { choosePersona } from './ui-navigation';

test('IMP08 analysis panel runs against the real local API and never writes', async ({ page }) => {
  await page.goto('/');
  await choosePersona(page, 'accountant');
  await page.getByRole('button', { name: '客户企业' }).click();
  await page.getByText('澄光科技（虚构示例）').last().click();
  const writes: string[] = [];
  page.on('request', (request) => {
    if (
      ['POST', 'PUT', 'PATCH', 'DELETE'].includes(request.method()) &&
      !new URL(request.url()).pathname.endsWith('/assistant/analyses')
    )
      writes.push(request.method());
  });
  await page.waitForTimeout(1200);
  await page.getByText('只读差异核对').click();
  await page.getByRole('button', { name: '开始只读核对' }).click();
  const result = page.locator('.analysis-evidence-result');
  await expect(result).toContainText('6602.02');
  await expect(page.getByText(/总额 \d+ 行，当前明细 \d+ 行/)).toBeVisible();
  await page.getByText('核对依据', { exact: true }).click();
  await expect(page.getByText(/证据摘要：/)).toBeVisible();
  await page.screenshot({ path: test.info().outputPath('analysis-real.png'), fullPage: true });
  expect(writes).toEqual([]);
});
