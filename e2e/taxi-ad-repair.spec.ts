import { expect, test } from '@playwright/test';
import { openTax, taxFullFixture } from './n04-tax-fixtures';

for (const width of [1280, 390]) {
  test(`出租车规费负向调整保留符号且错账套响应不接纳 ${width}`, async ({ page, request }) => {
    test.setTimeout(120_000);
    const f = await taxFullFixture(request);
    const batch = {
      id: 'batch-signed', book_id: f.book.id, period: '2026-10', status: 'draft', version: 1,
      draft_id: 'draft-signed', total_cents: 9500, row_count: 1, skipped_count: 0,
      policy: { version: 'synthetic-v1', driver_receivable_account: '1122', vehicle_revenue_account: '6001' },
      policy_fingerprint: 'a'.repeat(64), source_fingerprint: 'b'.repeat(64), batch_fingerprint: 'c'.repeat(64),
      stale: false, stale_reason: '', source_files: [], by_driver: [], by_vehicle: [], exceptions: [],
      rows: [{ id: 'signed-row', external_id: '负向调整', source_row_number: 2, driver_id: 'd1',
        vehicle_id: 'v1', gross_cents: 10000, discount_cents: 0, adjustment_cents: -500,
        net_cents: 9500, status: 'adopted', reason: '独立核定减免', line_ordinal: 0 }],
    };
    let wrongScope = false;
    await page.route(`**/api/tax/books/${f.book.id}/fee-batches`, route => route.fulfill({
      json: { items: [{ ...batch, book_id: wrongScope ? 'other-book' : f.book.id }] },
    }));
    await page.setViewportSize({ width, height: 900 });
    await openTax(page, f);
    const panel = page.locator('section[aria-labelledby="tax-fee-title"]');
    await panel.getByText(/历史批次/).click();
    await panel.getByRole('button', { name: /2026-10 · v1/ }).click();
    await expect(panel.getByText('−¥5.00', { exact: true })).toBeVisible();
    await expect(panel.getByText('¥95.00', { exact: true }).first()).toBeVisible();
    await page.screenshot({ path: `tmp_test/taxi-ad-repair-${width}.png`, fullPage: true });
    wrongScope = true;
    await panel.getByRole('button', { name: '刷新', exact: true }).click();
    await expect(panel.getByRole('alert')).toContainText('范围');
    await expect(panel.getByText('负向调整', { exact: false })).toHaveCount(0);
  });
}
