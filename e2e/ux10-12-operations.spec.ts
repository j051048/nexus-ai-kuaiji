import { test, expect } from '@playwright/test';
import { checked, identity } from './formal-books-fixtures';

test.setTimeout(120_000);
for (const width of [1280, 390]) {
  test(`客户分页、范围拒绝与撤权清空 ${width}`, async ({ page, request }) => {
    await page.setViewportSize({ width, height: 900 });
    const headers = await identity(request, 'accountant');
    const tag = `分页虚构-${crypto.randomUUID().slice(0, 8)}`;
    for (let n = 0; n < 22; n++) {
      await checked(
        await request.post('/api/companies', {
          headers,
          data: { name: `${tag}-${String(n).padStart(2, '0')}`, tax_id: '' },
        }),
      );
    }
    const responses: {
      bytes: number;
      offset: string | null;
      search: string | null;
      rows: number;
    }[] = [];
    page.on('response', async (response) => {
      if (response.url().includes('/api/firm/cockpit?') && response.ok()) {
        const body = await response.body();
        responses.push({
          bytes: body.length,
          offset: new URL(response.url()).searchParams.get('offset'),
          search: new URL(response.url()).searchParams.get('search'),
          rows: JSON.parse(body.toString()).companies.length,
        });
      }
    });
    await page.goto('/workspace/firm');
    await expect(page.getByRole('heading', { name: '全所工作台' })).toBeVisible();
    await page.getByLabel('搜索服务客户').fill(tag);
    await page.getByRole('button', { name: '查找', exact: true }).click();
    await expect(page.locator('.cockpit-client-details')).toContainText('共 22 家');
    await expect(page.locator('.cockpit-client-details')).toHaveAttribute('open', '');
    const detail = page.locator('.cockpit-client-details');
    await expect(detail.locator('.cockpit-client')).toHaveCount(20);
    const summary = await page.locator('.cockpit-summary-values').innerText();
    await page.getByRole('button', { name: '下一页', exact: true }).click();
    await expect(detail.locator('.cockpit-client')).toHaveCount(2);
    await expect(detail).toHaveAttribute('open', '');
    await expect(detail).toContainText(`${tag}-21`);
    expect(await page.locator('.cockpit-summary-values').innerText()).toBe(summary);
    await page.screenshot({
      path: `tmp_test/ux10-12-20261104/clients-${width}.png`,
      fullPage: true,
    });
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(
      true,
    );
    expect(responses.every((r) => r.rows <= 20 && r.bytes < 100_000)).toBe(true);
    await test.info().attach('customer-response-size', {
      body: JSON.stringify(responses),
      contentType: 'application/json',
    });
    await page.evaluate(() => window.dispatchEvent(new CustomEvent('accounting:forbidden')));
    await expect(page.locator('.cockpit-client')).toHaveCount(0);
    await expect(page.getByRole('alert')).toContainText('当前授权已更新');
    await page.route('**/api/firm/cockpit?**', async (route) => {
      const response = await route.fetch();
      const value = await response.json();
      value.scope.firm_id = 'foreign-firm';
      await route.fulfill({ response, json: value });
    });
    await page.getByRole('button', { name: '重新加载', exact: true }).click();
    await expect(page.getByRole('alert')).toContainText('返回的企业范围或数据格式不符');
    await expect(page.locator('.cockpit-client')).toHaveCount(0);
  });

  test(`实际待办可打开原凭证且错误不冒充空列表 ${width}`, async ({ page, request }) => {
    await page.setViewportSize({ width, height: 900 });
    const headers = await identity(request, 'accountant');
    const tag = `待办虚构-${crypto.randomUUID().slice(0, 8)}`;
    const book = await checked(
      await request.post('/api/books', { headers, data: { name: tag, period: '2026-10' } }),
    );
    await checked(
      await request.post(`/api/books/${book.id}/imports`, {
        headers,
        data: {
          source_name: tag,
          rows: [
            {
              external_id: tag,
              date: '2026-10-05',
              description: '办公用品',
              counterparty: tag,
              amount: '13.00',
              direction: 'expense',
              evidence_ref: '本地虚构票据',
            },
          ],
        },
      }),
      200,
    );
    await checked(await request.post(`/api/books/${book.id}/suggestions`, { headers }), 200);
    await page.goto('/workspace/inbox');
    await expect(page.getByRole('heading', { name: '待处理与人员分工' })).toBeVisible();
    const row = page.locator('.firm-inbox-row').filter({ hasText: tag });
    await expect(row).toHaveCount(1);
    await expect(row.getByRole('button', { name: '同屏复核', exact: true })).toBeVisible();
    await page.screenshot({ path: `tmp_test/ux10-12-20261104/inbox-${width}.png`, fullPage: true });
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(
      true,
    );
    await row.getByRole('button', { name: '同屏复核', exact: true }).click();
    await expect(page).toHaveURL(new RegExp(`book=${book.id}`));
    await page.route('**/api/firm/inbox?**', (route) =>
      route.fulfill({ status: 503, json: { detail: '本地故障注入' } }),
    );
    await page.goto('/workspace/inbox');
    await expect(page.getByRole('alert')).toBeVisible();
    await expect(page.locator('.firm-inbox-row')).toHaveCount(0);
    await expect(page.getByText('当前分类没有待办', { exact: true })).toHaveCount(0);
  });
}
