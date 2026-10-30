import { test, expect, type Page } from '@playwright/test';
const content = {
  balance_sheet: { assets: 0, liabilities: 0, equity: 0, unclosed_profit: 0, items: [] },
  profit_statement: { revenue_cents: 0, expense_cents: 0, profit_cents: 0, items: [] },
  cash_flow: {
    opening_cash_cents: 0,
    closing_cash_cents: 0,
    net_change_cents: 0,
    operating_cents: 0,
    investing_cents: 0,
    financing_cents: 0,
    unclassified_cents: 0,
  },
  checks: { balance_sheet_passed: true, profit_statement_passed: true, cash_flow_passed: true },
};
const report = {
  id: 'report-a',
  book_id: 'book-a',
  period: '2026-09',
  version: 1,
  status: 'draft',
  created_by: 'reviewer',
  content_hash: 'a'.repeat(64),
  content_json: JSON.stringify(content),
};
async function mount(page: Page, mode: 'read-403' | 'late-write') {
  let reads = 0;
  let release: (() => void) | undefined;
  let arrive: (() => void) | undefined;
  const arrived = new Promise<void>((resolve) => {
    arrive = resolve;
  });
  await page.route('**/api/**', async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (route.request().method() === 'POST') {
      arrive?.();
      await new Promise<void>((resolve) => {
        release = resolve;
      });
      await route.fulfill({ json: { report } });
      return;
    }
    reads++;
    if (mode === 'read-403' && path === '/api/reports')
      return route.fulfill({ status: 403, json: { detail: 'no scope' } });
    if (path === '/api/reports') return route.fulfill({ json: { reports: [] } });
    if (path === '/api/b04-close') return route.fulfill({ json: { runs: [] } });
    if (path.endsWith('/bank-coverages')) return route.fulfill({ json: { coverages: [] } });
    return route.fulfill({ json: { passed: true, rows: [] } });
  });
  await page.goto('/e2e/fixtures/p02-close-harness.html');
  return { arrived, release: () => release?.(), reads: () => reads };
}
test('P02 initial 403 clears loading and permits explicit reread', async ({ page }) => {
  await mount(page, 'read-403');
  await expect(page.getByRole('alert')).toContainText('授权已变化');
  await expect(page.getByText('正在读取月结证据…', { exact: true })).toHaveCount(0);
  await expect(page.getByRole('button', { name: '重新读取月结进度' })).toBeEnabled();
});
test('P02 revoked late write cannot restore data or success', async ({ page }) => {
  const run = await mount(page, 'late-write');
  await page.getByRole('button', { name: '生成正式报表', exact: true }).click();
  await run.arrived;
  const before = run.reads();
  await page.evaluate(() => window.dispatchEvent(new CustomEvent('accounting:forbidden')));
  run.release();
  await expect(page.getByRole('button', { name: '重新读取月结进度' })).toBeEnabled();
  await expect(page.getByRole('alert')).toContainText('授权已变化');
  await expect(page.getByText('报表已生成，尚需核验与独立复核。', { exact: true })).toHaveCount(0);
  await expect(page.getByLabel('本次报表版本')).toHaveCount(0);
  expect(run.reads()).toBe(before);
});
