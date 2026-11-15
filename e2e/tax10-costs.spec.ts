import { expect, test } from '@playwright/test';
import { checked } from './formal-fixtures';
import { openTax, taxFullFixture } from './n04-tax-fixtures';

type FormalFixture = Awaited<ReturnType<typeof taxFullFixture>>;

async function addSource(
  request: Parameters<typeof taxFullFixture>[0],
  f: FormalFixture,
  kind: 'insurance' | 'oa',
  payload: Record<string, unknown>,
) {
  await checked(
    await request.post(`/api/tax/books/${f.book.id}/cost-sources`, {
      headers: f.headers,
      data: {
        kind,
        source_key: kind === 'oa' ? 'OA-2026-10-1' : 'P-1',
        source_version: kind === 'oa' ? 3 : 1,
        period: '2026-10',
        subject_label: kind === 'oa' ? '车队费用' : '京A1',
        original_ref: kind === 'oa' ? 'OA/审批快照.json' : '原件/保单.pdf',
        sha256: kind === 'oa' ? 'c'.repeat(64) : 'b'.repeat(64),
        amount: '100.00',
        payload,
      },
    }),
    201,
  );
}

async function reviewLatest(
  request: Parameters<typeof taxFullFixture>[0],
  f: FormalFixture,
  kind: string,
) {
  const batches = await checked(
    await request.get(`/api/tax/books/${f.book.id}/cost-batches`, { headers: f.headers }),
    200,
  );
  const batch = batches.items.find((item: { kind: string; status: string }) => item.kind === kind);
  await checked(
    await request.post(`/api/tax/books/${f.book.id}/cost-batches/${batch.id}/review`, {
      headers: f.reviewer,
      data: { expected_version: batch.version, review_reason: '浏览器验收独立复核' },
    }),
    200,
  );
}

for (const viewport of [
  { width: 1280, height: 1000 },
  { width: 390, height: 844 },
]) {
  test(`TAX10 repair, insurance and OA cost batches are generated in the browser and frozen by a reviewer ${viewport.width}`, async ({
    page,
    request,
  }) => {
    test.setTimeout(180_000);
    const f = await taxFullFixture(request);
    const sourceProbe = await request.get(`/api/tax/books/${f.book.id}/cost-sources`, {
      headers: f.headers,
    });
    const batchProbe = await request.get(`/api/tax/books/${f.book.id}/cost-batches`, {
      headers: f.headers,
    });
    expect(sourceProbe.status(), await sourceProbe.text()).toBe(200);
    expect(batchProbe.status(), await batchProbe.text()).toBe(200);
    await addSource(request, f, 'insurance', {
      policy_no: 'P-2026-01',
      premium_cents: 120000,
      start_period: '2026-01',
      end_period: '2026-12',
      total_months: 12,
      algorithm: 'straight_line',
      vehicle_external_id: 'V1',
      allocation_basis: 'customer_confirmed_policy_terms',
    });
    await addSource(request, f, 'oa', {
      oa_event_id: 'OA-2026-10-1',
      oa_version: 3,
      approved_by: '部门负责人',
      approved_period: '2026-10',
      lines: [
        { account_code: '6602.03', amount_cents: 6000 },
        { account_code: '6602.04', amount_cents: 4000 },
      ],
    });
    await page.setViewportSize(viewport);
    await openTax(page, f);
    const panel = page.locator('section[aria-labelledby="tax-cost-title"]');
    await panel.scrollIntoViewIfNeeded();
    await panel.getByRole('button', { name: '登记并采用来源' }).click();
    await expect(panel.getByText(/来源已登记并采用/)).toBeVisible();
    const after = await request.get(`/api/tax/books/${f.book.id}/cost-sources`, {
      headers: f.headers,
    });
    expect(after.status()).toBe(200);
    await panel.getByLabel(/京A1 · v1/).check();
    await panel.getByRole('button', { name: '生成成本批次草稿' }).click();
    await expect(panel.getByText('成本批次草稿已生成；须由另一名有复核权的会计独立复核。')).toBeVisible();
    await expect(panel.getByText('¥100.00', { exact: true }).first()).toBeVisible();
    await panel.getByRole('button', { name: '独立复核并冻结' }).click();
    await expect(panel.getByRole('alert')).toBeVisible();
    await reviewLatest(request, f, 'repair');

    await panel.getByLabel('类别').selectOption('insurance');
    await panel.getByLabel(/京A1 · v1/).check();
    await panel.getByRole('button', { name: '生成成本批次草稿' }).click();
    await expect(panel.getByText('成本批次草稿已生成；须由另一名有复核权的会计独立复核。')).toBeVisible();
    await reviewLatest(request, f, 'insurance');

    await panel.getByLabel('类别').selectOption('oa');
    await panel.getByLabel(/车队费用 · v3/).check();
    await panel.getByRole('button', { name: '生成成本批次草稿' }).click();
    await expect(panel.getByText('成本批次草稿已生成；须由另一名有复核权的会计独立复核。')).toBeVisible();
    await reviewLatest(request, f, 'oa');

    await panel.getByRole('button', { name: '刷新' }).click();
    await panel.getByText(/历史批次/).click();
    const reviewedOa = panel.getByRole('button', { name: /2026-10 · OA\/工资 · v2 · 已复核/ });
    await expect(reviewedOa).toBeVisible();
    await reviewedOa.click();
    await expect(panel.locator('.tax-cost-summary').getByText('已复核', { exact: true })).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(
      true,
    );
    await page.screenshot({ path: `tmp_test/tax10-costs-${viewport.width}.png`, fullPage: true });
  });
}
