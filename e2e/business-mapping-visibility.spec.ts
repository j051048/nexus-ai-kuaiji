import { expect, test, type APIRequestContext, type Page } from '@playwright/test';
import { readFileSync } from 'node:fs';
import type { BusinessContext, BusinessEvent } from '../src/business-types';

const image = readFileSync(new URL('./fixtures/v03-receipt.png', import.meta.url));

test.afterEach(async ({ page }) => {
  // Keep route assertions active until their reads settle; closing the page
  // while a polling fetch is in flight would create a teardown-only failure.
  await page.unrouteAll({ behavior: 'wait' });
});

async function identity(request: APIRequestContext, persona: string) {
  const response = await request.get(`/api/session?persona=${persona}`);
  expect(response.ok()).toBe(true);
  return { Authorization: `Bearer ${(await response.json()).token}` };
}

async function bindPersona(page: Page, persona: 'employee' | 'finance') {
  await page.route('**/api/session', async (route) => {
    const url = new URL(route.request().url());
    url.searchParams.set('persona', persona);
    await route.fulfill({ response: await route.fetch({ url: url.toString() }) });
  });
}

async function fixture(request: APIRequestContext) {
  const employee = await identity(request, 'employee');
  const department = await identity(request, 'department');
  const finance = await identity(request, 'finance');
  const accountant = await identity(request, 'accountant');
  const context: BusinessContext = await (
    await request.get('/api/business/context', { headers: employee })
  ).json();
  const company = context.companies.find((item) => item.can_submit && item.department_id)!;
  expect(company).toBeTruthy();
  const tag = `关联可见性虚构-${crypto.randomUUID().slice(0, 8)}`;
  const bookResponse = await request.post('/api/books', {
    headers: accountant,
    data: { name: tag, company_id: company.id, period: '2026-10' },
  });
  expect(bookResponse.status()).toBe(201);
  const book = await bookResponse.json();
  expect(
    (
      await request.put(`/api/books/${book.id}/members/local-business-finance`, {
        headers: accountant,
        data: { role: 'editor' },
      })
    ).status(),
  ).toBe(200);
  const create = await request.post('/api/business/events', {
    headers: employee,
    data: {
      company_id: company.id,
      department_id: company.department_id,
      template_id: 'reimbursement',
      payload: {
        occurred_on: '2026-10-12',
        description: tag,
        counterparty: '虚构办公用品商店',
        amount: '213.00',
        direction: 'expense',
        payment_status: 'unpaid',
        category: 'office',
      },
    },
  });
  expect(create.status()).toBe(201);
  let event: BusinessEvent = await create.json();
  const route = `/api/business/events/${event.id}`;
  const upload = await request.post(`${route}/attachments?expected_version=${event.version}`, {
    headers: employee,
    multipart: { file: { name: '虚构原件.png', mimeType: 'image/png', buffer: image } },
  });
  expect(upload.status()).toBe(200);
  event = await upload.json();
  for (const [action, headers] of [
    ['submit', employee],
    ['department-approve', department],
    ['finance-approve', finance],
  ] as const) {
    const response = await request.post(`${route}/${action}`, {
      headers,
      data: { expected_version: event.version },
    });
    expect(response.status()).toBe(200);
    event = await response.json();
  }
  const handoff = await request.post(`${route}/handoff`, {
    headers: finance,
    data: {
      expected_version: event.version,
      book_id: book.id,
      idempotency_key: `visibility-${event.id}`,
    },
  });
  expect(handoff.status()).toBe(200);
  const receipt = await handoff.json();
  const own = await (await request.get(route, { headers: employee })).json();
  expect(own.mapping_visibility).toBe('restricted');
  expect(own.mapping).toBeNull();
  for (const key of ['book_id', 'draft_id', 'source_id']) {
    expect(JSON.stringify(own)).not.toContain(receipt[key]);
  }
  const financial = await (await request.get(route, { headers: finance })).json();
  expect(financial.mapping_visibility).toBe('visible');
  expect(financial.mapping.draft_id).toBe(receipt.draft_id);
  return { event, route, company, receipt };
}

for (const [device, viewport] of [
  ['desktop', { width: 1366, height: 900 }],
  ['mobile', { width: 375, height: 812 }],
] as const) {
  test(`${device} 已交接员工正常等待，旧接口中性，财务关联异常才提示核对`, async ({
    page,
    request,
    browser,
  }) => {
    const f = await fixture(request);
    const url = `/workspace/business?company=${f.company.id}&period=2026-10&event=${f.event.id}`;
    await bindPersona(page, 'employee');
    await page.setViewportSize(viewport);
    const writes: string[] = [];
    page.on('request', (request) => {
      if (request.url().includes('/api/business/') && request.method() !== 'GET')
        writes.push(request.url());
    });
    await page.goto(url, { waitUntil: 'domcontentloaded' });
    const guide = page.getByRole('region', { name: '事项办理引导', exact: true });
    await expect(guide).toContainText('当前账号不显示会计关联');
    await expect(guide).toContainText('等待会计处理');
    await expect(guide).not.toContainText('重新读取');
    await expect(guide).toHaveClass(/workflow-guide--neutral/);
    await expect(guide.locator('.workflow-guide-step--blocked')).toHaveCount(0);
    await expect(page.getByRole('button', { name: '生成凭证草稿', exact: true })).toHaveCount(0);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(
      true,
    );
    await guide.scrollIntoViewIfNeeded();
    await page.screenshot({ path: `tmp_test/mapping-visibility/restricted-${device}.png` });
    await page.route(`**${f.route}`, async (route) => {
      const response = await route.fetch();
      const body = await response.json();
      delete body.mapping_visibility; // Old API projection, no new permission grant.
      await route.fulfill({ response, json: body });
    });
    await page.reload({ waitUntil: 'domcontentloaded' });
    await expect(guide).toContainText('当前页面未提供会计处理进度');
    await expect(guide).toHaveClass(/workflow-guide--neutral/);
    await expect(guide.locator('.workflow-guide-step--blocked')).toHaveCount(0);
    expect(writes).toEqual([]);

    const financeContext = await browser.newContext({ viewport });
    try {
      const financePage = await financeContext.newPage();
      await bindPersona(financePage, 'finance');
      // Read-only fault injection. The real API permission/visible contract was
      // checked above; no damaged mapping is written into the local database.
      await financePage.route(`**${f.route}`, async (route) => {
        const response = await route.fetch();
        const body = await response.json();
        expect(body.mapping_visibility).toBe('visible');
        await route.fulfill({
          response,
          json: { ...body, mapping: null, mapping_visibility: 'missing' },
        });
      });
      await financePage.goto(new URL(url, page.url()).toString(), {
        waitUntil: 'domcontentloaded',
      });
      const missing = financePage.getByRole('region', { name: '事项办理引导', exact: true });
      await expect(missing).toContainText('授权会计核对交接记录');
      await expect(missing).toHaveClass(/workflow-guide--warning/);
      await expect(missing.locator('.workflow-guide-step--blocked')).toHaveCount(1);
      await expect(missing).not.toContainText('重新读取');
      expect(
        await financePage.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1),
      ).toBe(true);
      await missing.scrollIntoViewIfNeeded();
      await financePage.screenshot({ path: `tmp_test/mapping-visibility/missing-${device}.png` });
    } finally {
      for (const financePage of financeContext.pages()) {
        await financePage.unrouteAll({ behavior: 'wait' });
      }
      await financeContext.close();
    }
  });
}
