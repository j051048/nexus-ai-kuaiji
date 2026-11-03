import { expect, test, type Page } from '@playwright/test';
import type { BusinessContext } from '../src/business-types';
import { openBusiness } from './ui-navigation';

test.setTimeout(45_000);
const output = `tmp_test/v07/${process.env.V07_SCREENSHOT_PHASE ?? 'green'}`;

async function bindFinance(page: Page) {
  await page.route('**/api/session', async (route) => {
    const url = new URL(route.request().url());
    url.searchParams.set('persona', 'finance');
    await route.fulfill({ response: await route.fetch({ url: url.toString() }) });
  });
}

for (const [device, viewport] of [
  ['desktop', { width: 1366, height: 900 }],
  ['mobile', { width: 375, height: 812 }],
] as const) {
  test(`${device} V07 业务范围首次读取503后可真正重读授权，不用刷新列表空操作代替`, async ({
    page,
    request,
  }) => {
    const session = await (await request.get('/api/session?persona=finance')).json();
    const realResponse = await request.get('/api/business/context', {
      headers: { Authorization: `Bearer ${session.token}` },
    });
    expect(realResponse.ok()).toBe(true);
    const expected: BusinessContext = await realResponse.json();
    const expectedCompanies = expected.companies.filter((company) => company.can_finance);
    expect(expectedCompanies.length).toBeGreaterThan(0);
    await bindFinance(page);
    await page.setViewportSize(viewport);
    await page.goto('/workspace/home', { waitUntil: 'domcontentloaded' });
    await expect(page.getByRole('heading', { name: '财务核验', exact: true })).toBeVisible();
    let contextReads = 0;
    const writes: string[] = [];
    page.on('request', (req) => {
      const url = new URL(req.url());
      if (url.pathname.startsWith('/api/') && !['GET', 'HEAD'].includes(req.method()))
        writes.push(url.pathname);
    });
    await page.route('**/api/business/context', async (route) => {
      contextReads++;
      if (contextReads === 1) {
        await route.fulfill({
          status: 503,
          json: { detail: 'V07合成业务范围首次读取失败，请重新读取当前授权。' },
        });
      } else {
        // Retry uses the same authenticated real endpoint. It is not replaced
        // by a successful fake context or a different account's permissions.
        await route.fulfill({ response: await route.fetch() });
      }
    });
    await openBusiness(page);
    const error = page.getByRole('alert').filter({ hasText: '服务暂时不可用' });
    await expect(error).toBeVisible();
    expect(contextReads).toBe(1);
    const retry = error.getByRole('button', { name: /^(重新读取业务范围|刷新列表)$/ });
    await expect(retry).toBeVisible();
    const retryLabel = await retry.innerText();
    await retry.click();
    // Red must fail on the missing real read, rather than only a new label.
    // The original refresh calls loadList(), which returns when context=null.
    await expect.poll(() => contextReads, { timeout: 5_000 }).toBe(2);
    await expect(error).toHaveCount(0);
    expect(retryLabel).toBe('重新读取业务范围');
    await expect(page.getByRole('tab', { name: '企业财务', exact: true })).toHaveAttribute(
      'aria-selected',
      'true',
    );
    const companies = page.getByLabel('筛选业务事项企业', { exact: true });
    const optionIds = await companies
      .locator('option')
      .evaluateAll((options) =>
        options.map((option) => (option as HTMLOptionElement).value).filter(Boolean),
      );
    expect(optionIds.sort()).toEqual(expectedCompanies.map((company) => company.id).sort());
    await expect(page.locator('.client-portal')).toBeVisible();
    await expect(page.getByRole('navigation', { name: '主导航', exact: true })).toHaveCount(0);
    await expect(page.getByRole('heading', { name: /登录/ })).toHaveCount(0);
    expect(writes).toEqual([]);
    expect(contextReads).toBe(2);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(
      true,
    );
    await page.screenshot({
      path: `${output}/business-context-recovery-${device}.png`,
      fullPage: false,
    });
  });
}
