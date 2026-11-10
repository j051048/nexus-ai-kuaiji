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
    test.use({ viewport });
    test('当前事项卡片直达原件、差异和办理，不自动核验', async ({ page, request }) => {
      const event = await pending(request);
      let writes = 0;
      page.on('request', (req) => {
        if (req.method() === 'POST') writes++;
      });
      await open(page, event);
      const card = page.getByRole('region', { name: '本事项办理助手' });
      await expect(card).toContainText('企业财务');
      await expect(card).toContainText('2026-10');
      await expect(card).toContainText(`v${event.version}`);
      await card.getByRole('button', { name: '核对原件' }).click();
      await expect(page.locator('.business-evidence')).toBeFocused();
      await card.getByRole('button', { name: /差异与依据/ }).click();
      await expect(page.locator('.business-checks')).toBeFocused();
      await card.getByRole('button', { name: '核对后办理' }).click();
      await expect(page.locator('.business-actions')).toBeFocused();
      await expect(page.getByRole('button', { name: '财务核验通过', exact: true })).toBeVisible();
      expect(writes).toBe(0);
      const stored = await (
        await request.get(`/api/business/events/${event.id}`, {
          headers: await identity(request, 'finance'),
        })
      ).json();
      expect(stored.status).toBe('department_approved');
      await expect(page.locator('.business-extra-guide')).not.toHaveAttribute('open');
      expect(
        await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth),
      ).toBeTruthy();
      await page.locator('.business-detail-title').scrollIntoViewIfNeeded();
      await page.screenshot({
        path: `tmp_test/review-assistant/${device}-review.png`,
        fullPage: true,
      });
    });
    test('摘要错范围或撤权不显示依据和办理按钮，重试只读', async ({ page, request }) => {
      const event = await pending(request);
      let mode = 'wrong';
      await page.route(`**/api/agent-workspace/context/events/${event.id}?**`, async (route) => {
        if (mode === 'revoked')
          return route.fulfill({ status: 403, json: { detail: '授权已变化' } });
        const response = await route.fetch();
        const value = await response.json();
        if (mode === 'wrong') value.scope.company_id = 'other-company';
        await route.fulfill({ response, json: value });
      });
      await open(page, event);
      const card = page.getByRole('region', { name: '本事项办理助手' });
      await expect(card.getByRole('alert')).toBeVisible();
      await expect(card.getByRole('button', { name: '核对后办理' })).toHaveCount(0);
      mode = 'valid';
      await card.getByRole('button', { name: '重新读取办理依据' }).click();
      await expect(card.getByRole('button', { name: '核对后办理' })).toBeVisible();
      mode = 'revoked';
      await card.getByText('查看依据范围', { exact: true }).click();
      await card.getByRole('button', { name: '重新读取办理依据' }).click();
      await expect(card.getByRole('button', { name: '核对后办理' })).toHaveCount(0);
      await expect(card).toHaveCount(0);
      await expect(
        page.getByRole('alert').filter({ hasText: '授权已变化，已清空事项资料' }),
      ).toBeVisible();
      await expect(page.locator('.business-evidence')).toHaveCount(0);
    });
    test('事项改版后清空旧依据，刷新事项再显示下一步', async ({ page, request }) => {
      const event = await pending(request);
      await open(page, event);
      const card = page.getByRole('region', { name: '本事项办理助手' });
      await expect(card.getByRole('button', { name: '核对后办理' })).toBeVisible();
      const approved = await request.post(`/api/business/events/${event.id}/finance-approve`, {
        headers: await identity(request, 'finance'),
        data: { expected_version: event.version },
      });
      expect(approved.ok()).toBeTruthy();
      const updated = await approved.json();
      await card.getByText('查看依据范围', { exact: true }).click();
      await card.getByRole('button', { name: '重新读取办理依据' }).click();
      await expect(card.getByRole('alert')).toBeVisible();
      await expect(card.getByRole('button', { name: '核对后办理' })).toHaveCount(0);
      await card.getByRole('button', { name: '刷新事项及依据' }).click();
      await expect(card).toContainText(`v${updated.version}`);
      await expect(card.getByRole('button', { name: '办理交接' })).toBeVisible();
      await expect(page.getByRole('button', { name: '生成凭证草稿', exact: true })).toBeVisible();
    });
    test('恢复只记位置，重新读当前版本和权限，不能恢复旧审批', async ({ page, request }) => {
      const event = await pending(request);
      await open(page, event);
      const card = page.getByRole('region', { name: '本事项办理助手' });
      await card.getByRole('button', { name: /差异与依据/ }).click();
      const stored = await page.evaluate(
        () =>
          Object.entries(localStorage).find(([key]) =>
            key.startsWith('nexus:business-task:v1:'),
          )?.[1],
      );
      expect(stored).toBeTruthy();
      expect(stored).not.toContain('38.20');
      expect(stored).not.toContain('虚构供方');
      const approved = await request.post(`/api/business/events/${event.id}/finance-approve`, {
        headers: await identity(request, 'finance'),
        data: { expected_version: event.version },
      });
      expect(approved.ok()).toBeTruthy();
      let writes = 0;
      page.on('request', (req) => {
        if (req.method() === 'POST') writes++;
      });
      await page.reload();
      await page.getByRole('button', { name: '继续上次办理', exact: true }).click();
      await expect(page.locator('.business-checks')).toBeFocused();
      await expect(card.getByRole('button', { name: '办理交接' })).toBeVisible();
      expect(writes).toBe(0);
      await page.route(`**/api/business/events/${event.id}`, (route) =>
        route.fulfill({ status: 404, json: { detail: '事项不存在或无权访问' } }),
      );
      await page.reload();
      await page.getByRole('button', { name: '继续上次办理', exact: true }).click();
      await expect(page.getByRole('region', { name: '本事项办理助手' })).toHaveCount(0);
      await expect(page.getByRole('button', { name: '生成凭证草稿', exact: true })).toHaveCount(0);
    });
    test('逐项差异直达办理，人工操作后重新核对，不把跳转当作完成', async ({ page, request }) => {
      const event = await pending(request);
      await open(page, event);
      const card = page.getByRole('region', { name: '本事项办理助手' });
      await card.getByText(/展开待核对事项/).click();
      const issue = card.locator('.business-context-issue').first();
      await expect(issue).toContainText('企业财务');
      await issue.getByRole('button', { name: '去核对此项', exact: true }).click();
      expect(
        await page.evaluate(
          () => !!document.activeElement?.matches('.business-checks, [data-finding-code]'),
        ),
      ).toBeTruthy();
      await card.getByRole('button', { name: '核对后办理' }).click();
      await page.getByRole('button', { name: '财务核验通过', exact: true }).click();
      await expect(card).toContainText('已重新核对');
      await expect(card).toContainText('仍需人工办理');
      await expect(card.getByRole('button', { name: '办理交接' })).toBeVisible();
      await page.screenshot({
        path: `tmp_test/task-evidence-20261110/${device}-rechecked.png`,
        fullPage: true,
      });
      await card.screenshot({ path: `tmp_test/task-evidence-20261110/${device}-card.png` });
    });
    test('同版本重新核对也更新摘要，迟到旧响应不能复活撤权资料', async ({ page, request }) => {
      const event = await pending(request);
      await open(page, event);
      const card = page.getByRole('region', { name: '本事项办理助手' });
      await expect(card.getByRole('button', { name: '核对后办理' })).toBeVisible();
      await card.getByText('查看依据范围', { exact: true }).click();
      await card.getByRole('button', { name: '重新读取办理依据' }).click();
      await expect(card).toContainText('已重新核对');
      let release!: () => void;
      const delayed = new Promise<void>((resolve) => {
        release = resolve;
      });
      let arrived!: () => void;
      const inFlight = new Promise<void>((resolve) => {
        arrived = resolve;
      });
      await page.route(`**/api/agent-workspace/context/events/${event.id}?**`, async (route) => {
        const response = await route.fetch();
        arrived();
        await delayed;
        await route.fulfill({ response }).catch(() => {});
      });
      await card.getByText('查看依据范围', { exact: true }).click();
      await card.getByRole('button', { name: '重新读取办理依据' }).click();
      await inFlight;
      await page.route(`**/api/business/events/${event.id}/intelligence`, (route) =>
        route.fulfill({ status: 403, json: { detail: '授权已撤销' } }),
      );
      await page.getByRole('button', { name: '刷新票据核对结果', exact: true }).click();
      await expect(card).toHaveCount(0);
      release();
      await expect(card).toHaveCount(0);
      await expect(page.locator('.business-evidence')).toHaveCount(0);
      expect(
        await page.evaluate(() =>
          Object.keys(localStorage).some((key) => key.startsWith('nexus:business-task:v1:')),
        ),
      ).toBeFalsy();
    });
  });
}
