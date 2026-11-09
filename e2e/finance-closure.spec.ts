import { expect, test } from '@playwright/test';
import { mkdirSync } from 'node:fs';

for (const width of [1365, 390]) {
  test(`按需搜索 ${width} 冷启动、关闭返回与企业定位`, async ({ page, request }) => {
    const scripts: string[] = [];
    page.on('request', (request) => {
      if (request.resourceType() === 'script') scripts.push(request.url());
    });
    const session = await (await request.get('/api/session?persona=accountant')).json();
    const headers = { Authorization: `Bearer ${session.token}` };
    const tag = crypto.randomUUID().slice(0, 8);
    const name = `隔离搜索 ${tag}`;
    const response = await request.post('/api/books', {
      headers,
      data: { name, period: '2026-10' },
    });
    expect(response.status()).toBe(201);
    const book = await response.json();
    await page.setViewportSize({ width, height: 844 });
    await page.goto('/workspace/home');
    const opener = page.getByRole('button', { name: '查找企业与工作入口', exact: true });
    await expect(opener).toBeVisible();
    expect(scripts.some((url) => url.includes('AgentCommandPalette'))).toBe(false);
    await opener.click();
    const palette = page.getByRole('dialog', { name: '快速进入工作', exact: true });
    await expect(palette).toBeVisible();
    expect(scripts.some((url) => url.includes('AgentCommandPalette'))).toBe(true);
    await expect(palette.getByLabel('查找企业或工作入口', { exact: true })).toBeFocused();
    const output = `tmp_test/finance-closure/screenshots/${process.env.E2E_FRONTEND_MODE || 'dev'}`;
    mkdirSync(output, { recursive: true });
    await page.screenshot({ path: `${output}/search-${width}.png`, fullPage: true });
    await page.keyboard.press('Escape');
    await expect(palette).toHaveCount(0);
    await expect(opener).toBeFocused();
    await opener.click();
    await palette.getByLabel('查找企业或工作入口', { exact: true }).fill(tag);
    await palette.getByRole('button').filter({ hasText: name }).click();
    await expect(palette).toHaveCount(0);
    await expect(page.getByRole('listitem').filter({ hasText: name })).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(
      true,
    );
    const exported = await request.get(`/api/books/${book.id}/exports`, { headers });
    expect(exported.status()).toBe(200);
    expect((await exported.json()).exports).toEqual([]);
  });
}
