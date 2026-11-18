import { expect, test, type APIRequestContext } from '@playwright/test';

const period = '2026-11';

async function owner(request: APIRequestContext) {
  const response = await request.get('/api/session?persona=accountant');
  expect(response.status()).toBe(200);
  return { Authorization: `Bearer ${(await response.json()).token}` };
}

test('五家企业 × 12 月矩阵保留未建账月份并可回到月度清单', async ({ page, request }) => {
  const headers = await owner(request);
  const marker = crypto.randomUUID().slice(0, 8);
  const names: string[] = [];
  for (let index = 1; index <= 5; index += 1) {
    const name = `000 OPT09 年度矩阵 ${index}（虚构） ${marker}`;
    const created = await request.post('/api/books', {
      headers,
      data: { name, period },
    });
    expect(created.status()).toBe(201);
    names.push(name);
  }
  await page.goto(`/workspace/monthly?period=${period}`);
  await page.getByRole('button', { name: '年度矩阵', exact: true }).click();
  await expect(page.getByRole('heading', { name: `${period.slice(0, 4)} 年度矩阵` })).toBeVisible();
  await expect(page.getByRole('columnheader')).toHaveCount(13);
  await expect(page.getByRole('columnheader', { name: '12 月' })).toBeVisible();
  const december = await page.getByRole('columnheader', { name: '12 月' }).boundingBox();
  const matrix = await page.locator('.annual-matrix-scroll').boundingBox();
  expect(december).not.toBeNull();
  expect(matrix).not.toBeNull();
  expect(december!.x + december!.width).toBeLessThanOrEqual(matrix!.x + matrix!.width + 1);
  const rows = page.locator('.annual-matrix-table tbody tr[data-company]');
  await expect.poll(async () => rows.count()).toBeGreaterThanOrEqual(5);
  const target = page
    .getByRole('row', { name: new RegExp(names[0].replace(/[.*+?^${}()|[\]\\]/g, '\\$&')) })
    .first();
  await expect(target).toBeVisible();
  const current = target.getByRole('button', { name: /11 月，可办理/ });
  await expect(current).toBeVisible();
  await expect(current).toHaveAttribute('aria-label', /阻断 0 项/);
  await page.screenshot({ path: 'tmp_test/opt09-annual-matrix-1280.png', fullPage: true });

  await page.setViewportSize({ width: 390, height: 844 });
  const wrap = page.locator('.annual-matrix-scroll');
  expect(await wrap.evaluate((node) => node.scrollWidth > node.clientWidth)).toBe(true);
  await wrap.evaluate((node) => {
    node.scrollLeft = node.scrollWidth;
  });
  await page.screenshot({ path: 'tmp_test/opt09-annual-matrix-390.png', fullPage: true });

  await page.setViewportSize({ width: 1280, height: 800 });
  await current.click();
  await expect(page.getByRole('button', { name: '按月清单', exact: true })).toHaveAttribute(
    'aria-pressed',
    'true',
  );
  await expect(page.getByLabel('客户进度月份')).toHaveValue(period);
});
