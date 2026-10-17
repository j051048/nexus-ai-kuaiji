import { expect, test } from '@playwright/test';

test('账套子视图始终保留左侧账套列表和五步流水线', async ({ page, request }) => {
  test.setTimeout(120_000);
  const session = await (await request.get('/api/session?persona=accountant')).json();
  const headers = { Authorization: `Bearer ${session.token}` };
  const name = `常驻壳验证 ${crypto.randomUUID().slice(0, 8)}`;
  const response = await request.post('/api/books', {
    headers,
    data: { name, period: '2026-10' },
  });
  expect(response.status()).toBe(201);
  const book = await response.json();
  const views = [
    'recurring',
    'settlement',
    'briefing',
    'integration',
    'catalog',
    'learning',
    'ledger',
    'migration',
  ];

  for (const view of views) {
    await page.goto(`/workspace/${view}?book=${book.id}`, { waitUntil: 'domcontentloaded' });
    const pipeline = page.getByRole('navigation', { name: '做账五步流水线', exact: true });
    await expect(pipeline).toBeVisible({ timeout: 60_000 });
    await expect(page.getByRole('listitem').filter({ hasText: name })).toBeVisible();
    await expect(page.locator('.book-embedded-view')).toBeVisible();
  }
});
