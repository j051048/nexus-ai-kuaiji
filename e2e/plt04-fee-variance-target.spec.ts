import { choosePersona } from './ui-navigation';
import { expect, test } from '@playwright/test';

test('PLT04 规费差异待办定位到目标行并能返回原待办', async ({ page, request }) => {
  test.setTimeout(180_000);
  const session = await (await request.get('/api/session?persona=accountant')).json();
  const headers = { Authorization: `Bearer ${session.token}` };
  const created = await request.post('/api/books', {
    headers,
    data: { name: `PLT04 规费差异待办定位 ${Date.now()}`, period: '2026-10' },
  });
  expect(created.status()).toBe(201);
  const book = await created.json();
  const recorded = await request.post(`/api/tax/books/${book.id}/fee-variances`, {
    headers,
    data: {
      kind: 'unmatched',
      expected_cents: 0,
      actual_cents: 500,
      evidence_ref: '合成银行回单',
      idempotency_key: crypto.randomUUID(),
    },
  });
  expect(recorded.status()).toBe(201);
  const variance = await recorded.json();

  await page.goto('/workspace/home');
  await choosePersona(page, 'accountant');
  const row = page.locator(`[data-task-key="fee_variance:${variance.id}"]`);
  await expect(row).toBeVisible({ timeout: 15_000 });

  for (const viewport of [
    { width: 1280, height: 900 },
    { width: 390, height: 844 },
  ]) {
    await page.setViewportSize(viewport);
    await row.getByRole('button').last().click();
    const target = page.locator(`#fee-variance-${variance.id}`);
    await expect(target).toBeVisible({ timeout: 15_000 });
    await expect(target).toHaveClass(/is-target/);
    await expect(page.getByText('已定位到待办中的这笔差异。')).toBeVisible();
    await expect(page).toHaveURL(/period=2026-10/);
    await expect(page).toHaveURL(new RegExp(`company=[^&]+`));

    await page
      .getByRole('button', { name: /返回待办/ })
      .first()
      .click();
    await expect(page).toHaveURL(/\/workspace\/home/, { timeout: 15_000 });
    await expect(row).toBeVisible({ timeout: 15_000 });
  }
});
