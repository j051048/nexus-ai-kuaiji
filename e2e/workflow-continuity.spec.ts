import { expect, test, type APIRequestContext, type Page } from '@playwright/test';
import { readFileSync } from 'node:fs';
import type { BusinessContext, BusinessEvent } from '../src/business-types';

test.setTimeout(60_000);
const image = readFileSync(new URL('./fixtures/v03-receipt.png', import.meta.url));
async function identity(request: APIRequestContext, persona: string) {
  const response = await request.get(`/api/session?persona=${persona}`);
  expect(response.ok()).toBeTruthy();
  return { Authorization: `Bearer ${(await response.json()).token}` };
}
async function pending(request: APIRequestContext) {
  const employee = await identity(request, 'employee');
  const context: BusinessContext = await (
    await request.get('/api/business/context', { headers: employee })
  ).json();
  const company = context.companies.find((row) => row.can_submit && row.department_id)!;
  const created = await request.post('/api/business/events', {
    headers: employee,
    data: {
      company_id: company.id,
      department_id: company.department_id,
      template_id: 'reimbursement',
      payload: {
        occurred_on: '2026-10-12',
        description: `连续办理-${crypto.randomUUID()}`,
        counterparty: '虚构供方',
        amount: '38.20',
        direction: 'expense',
        payment_status: 'unpaid',
        category: 'office',
      },
    },
  });
  expect(created.status()).toBe(201);
  let event: BusinessEvent = await created.json();
  const upload = await request.post(
    `/api/business/events/${event.id}/attachments?expected_version=${event.version}`,
    {
      headers: employee,
      multipart: { file: { name: '办理原件.png', mimeType: 'image/png', buffer: image } },
    },
  );
  expect(upload.ok()).toBeTruthy();
  event = await upload.json();
  for (const [command, persona] of [
    ['submit', 'employee'],
    ['department-approve', 'department'],
  ]) {
    const response = await request.post(`/api/business/events/${event.id}/${command}`, {
      headers: await identity(request, persona),
      data: { expected_version: event.version },
    });
    expect(response.ok()).toBeTruthy();
    event = await response.json();
  }
  return event;
}
async function open(page: Page, event: BusinessEvent) {
  await page.route('**/api/session', async (route) => {
    const url = new URL(route.request().url());
    url.searchParams.set('persona', 'finance');
    await route.fulfill({ response: await route.fetch({ url: url.toString() }) });
  });
  await page.goto(
    `/workspace/business?company=${event.company_id}&period=2026-10&event=${event.id}`,
  );
}
for (const [device, viewport] of [
  ['desktop', { width: 1366, height: 900 }],
  ['mobile', { width: 375, height: 812 }],
] as const) {
  test.describe(device, () => {
    test.use({ viewport, actionTimeout: 5_000 });
    test('列表读取失败不显示零待办或允许翻页', async ({ page, request }) => {
      const event = await pending(request);
      await page.route(/\/api\/business\/events\?/, (route) =>
        route.fulfill({ status: 503, json: { detail: 'temporary' } }),
      );
      await open(page, event);
      if (device === 'mobile') {
        await expect(page.locator('.business-detail-title h3')).toHaveText(
          event.payload.description,
        );
        await page.getByRole('button', { name: '← 返回事项列表' }).click();
      }
      await expect(page.locator('.business-pagination')).toBeVisible();
      await expect(page.locator('.business-pagination')).toContainText('事项数量暂不可用');
      await expect(page.locator('.business-pagination')).not.toContainText('0 件事项');
      await expect(
        page.locator('.business-pagination').getByRole('button', { name: '下一页' }),
      ).toBeDisabled();
    });
    test('事项读取失败可原地重试，重试不重复办理', async ({ page, request }) => {
      const event = await pending(request);
      let fail = true,
        writes = 0;
      page.on('request', (req) => {
        if (req.method() === 'POST') writes += 1;
      });
      await page.route(`**/api/business/events/${event.id}`, async (route) => {
        if (fail) return route.fulfill({ status: 503, json: { detail: 'temporary' } });
        await route.continue();
      });
      await open(page, event);
      await expect(page.getByRole('alert')).toContainText('服务暂时不可用');
      fail = false;
      await page.getByRole('button', { name: '重新读取此事项' }).click();
      await expect(page.locator('.business-detail-title h3')).toHaveText(event.payload.description);
      expect(writes).toBe(0);
    });
    test('错误企业的详情响应不能接纳', async ({ page, request }) => {
      const event = await pending(request);
      await page.route(`**/api/business/events/${event.id}`, async (route) => {
        const response = await route.fetch();
        await route.fulfill({
          response,
          json: { ...(await response.json()), company_id: 'wrong-company' },
        });
      });
      await open(page, event);
      await expect(page.getByRole('alert')).toContainText('事项范围或版本不一致');
      await expect(page.getByRole('button', { name: '财务核验通过', exact: true })).toHaveCount(0);
    });
    test('办理拒绝后清空旧详情与原件，不重复发送', async ({ page, request }) => {
      const event = await pending(request);
      let writes = 0;
      await page.route(`**/api/business/events/${event.id}/finance-approve`, async (route) => {
        writes += 1;
        await route.fulfill({ status: 403, json: { detail: '当前无权办理' } });
      });
      await open(page, event);
      await page.getByRole('button', { name: '财务核验通过', exact: true }).click();
      await expect(page.locator('.business-detail-title h3')).toHaveCount(0);
      await expect(page.getByRole('button', { name: '下载附件 办理原件.png' })).toHaveCount(0);
      expect(writes).toBe(1);
      const stored = await request.get(`/api/business/events/${event.id}`, {
        headers: await identity(request, 'finance'),
      });
      expect((await stored.json()).status).toBe('department_approved');
    });
    test('成功核验后可继续同企业待办，下一件重新核验且不沿用退回理由', async ({
      page,
      request,
    }) => {
      const first = await pending(request),
        second = await pending(request);
      await open(page, first);
      await page.getByText('有问题？退回修改', { exact: true }).click();
      await page.getByLabel('退回原因').fill('上一件的临时备注');
      await page.getByRole('button', { name: '财务核验通过', exact: true }).click();
      await expect(page.getByRole('status').filter({ hasText: '财务核验已通过' })).toBeVisible();
      await page.getByRole('button', { name: '继续处理本企业下一件' }).click();
      await expect(page.locator('.business-detail-title h3')).toHaveText(
        second.payload.description,
      );
      await expect(page).toHaveURL(new RegExp(`event=${second.id}`));
      await expect(page.getByLabel('退回原因')).toHaveValue('');
      await expect(page.getByRole('button', { name: '财务核验通过', exact: true })).toBeEnabled();
      const stored = await request.get(`/api/business/events/${second.id}`, {
        headers: await identity(request, 'finance'),
      });
      expect((await stored.json()).status).toBe('department_approved');
      await page.screenshot({
        path: `tmp_test/workflow-hardening/20261109/${device}-next.png`,
        fullPage: true,
      });
      await page.reload();
      await expect(page.locator('.business-detail-title h3')).toHaveText(
        second.payload.description,
      );
    });
  });
}
