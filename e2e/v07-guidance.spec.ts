import { expect, test, type APIRequestContext, type Page, type Request } from '@playwright/test';
import { readFileSync } from 'node:fs';
import type { BusinessEvent } from '../src/business-types';
import { openBusiness } from './ui-navigation';

test.setTimeout(60_000);
const output = `tmp_test/v07/${process.env.V07_SCREENSHOT_PHASE ?? 'green'}`;
const image = readFileSync(new URL('./fixtures/v03-receipt.png', import.meta.url));

async function identity(request: APIRequestContext, persona: string) {
  const response = await request.get(`/api/session?persona=${persona}`);
  expect(response.ok()).toBe(true);
  return { Authorization: `Bearer ${(await response.json()).token}` };
}

async function bindPersona(page: Page, persona: 'employee' | 'finance') {
  // Only bind the startup identity. Business data and all authorization checks
  // continue through the real local API; this is not a production login test.
  await page.route('**/api/session', async (route) => {
    const url = new URL(route.request().url());
    url.searchParams.set('persona', persona);
    await route.fulfill({ response: await route.fetch({ url: url.toString() }) });
  });
}

async function submittedFixture(request: APIRequestContext) {
  const owner = await identity(request, 'accountant');
  const employee = await identity(request, 'employee');
  const finance = await identity(request, 'finance');
  const tag = `V07岗位引导虚构-${crypto.randomUUID().slice(0, 8)}`;
  const companyResponse = await request.post('/api/companies', {
    headers: owner,
    data: { name: tag, tax_id: '' },
  });
  expect(companyResponse.status()).toBe(201);
  const company: { id: string; name: string } = await companyResponse.json();
  const departmentResponse = await request.post(`/api/companies/${company.id}/departments`, {
    headers: owner,
    data: { name: '虚构业务部门' },
  });
  expect(departmentResponse.status()).toBe(201);
  const department: { id: string } = await departmentResponse.json();
  for (const [email, role] of [
    ['employee@demo.example.invalid', 'employee'],
    ['department@demo.example.invalid', 'employee'],
    ['finance@demo.example.invalid', 'finance'],
  ] as const) {
    expect(
      (
        await request.put(`/api/companies/${company.id}/members`, {
          headers: owner,
          data: { email, role, department_id: department.id },
        })
      ).status(),
    ).toBe(200);
  }
  expect(
    (
      await request.put(`/api/companies/${company.id}/departments/${department.id}/approvers`, {
        headers: owner,
        data: { user_id: 'local-business-department' },
      })
    ).status(),
  ).toBe(200);
  const created = await request.post('/api/business/events', {
    headers: employee,
    data: {
      company_id: company.id,
      department_id: department.id,
      template_id: 'reimbursement',
      payload: {
        occurred_on: '2026-10-12',
        description: `${tag} 待部门审批`,
        counterparty: '虚构文具供应商',
        amount: '38.20',
        direction: 'expense',
        payment_status: 'unpaid',
        category: 'office',
        purpose: '仅本地岗位引导和安全恢复验证',
      },
    },
  });
  expect(created.status()).toBe(201);
  let event: BusinessEvent = await created.json();
  const upload = await request.post(
    `/api/business/events/${event.id}/attachments?expected_version=${event.version}`,
    {
      headers: employee,
      multipart: { file: { name: 'V07虚构原件.png', mimeType: 'image/png', buffer: image } },
    },
  );
  expect(upload.status()).toBe(200);
  event = await upload.json();
  const submitted = await request.post(`/api/business/events/${event.id}/submit`, {
    headers: employee,
    data: { expected_version: event.version },
  });
  expect(submitted.status()).toBe(200);
  event = await submitted.json();
  expect(event.status).toBe('submitted');
  return { owner, employee, finance, tag, company, event };
}

function observeWrites(page: Page) {
  const writes: string[] = [];
  page.on('request', (request) => {
    const path = new URL(request.url()).pathname;
    if (path.startsWith('/api/') && !['GET', 'HEAD'].includes(request.method())) writes.push(path);
  });
  return writes;
}

async function noOverflow(page: Page) {
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(
    true,
  );
}

for (const [device, viewport] of [
  ['desktop', { width: 1366, height: 900 }],
  ['mobile', { width: 375, height: 812 }],
] as const) {
  test(`${device} V07 待办空态可清搜索和类型，保留企业及等待分组且不写业务`, async ({
    page,
    request,
  }) => {
    const f = await submittedFixture(request);
    await bindPersona(page, 'employee');
    await page.setViewportSize(viewport);
    await page.goto(`/workspace/home?company=${f.company.id}`, { waitUntil: 'domcontentloaded' });
    await expect(page.getByRole('heading', { name: '我的工作台', exact: true })).toBeVisible();
    await page.getByRole('button', { name: '显示列表选项', exact: true }).click();
    const waiting = page
      .getByRole('region', { name: '我的工作概览', exact: true })
      .getByRole('button', { name: /我的办理进度/ });
    await waiting.click();
    await page.getByLabel('筛选待办类型', { exact: true }).selectOption('case');
    const noMatch = `V07不存在-${crypto.randomUUID()}`;
    const filtered = page.waitForResponse((response) => {
      const url = new URL(response.url());
      return url.pathname === '/api/workspace/tasks' && url.searchParams.get('q') === noMatch;
    });
    await page.getByLabel('搜索工作事项', { exact: true }).fill(noMatch);
    expect((await filtered).ok()).toBe(true);
    await expect(page.getByText('当前筛选下暂无等待事项', { exact: true })).toBeVisible();
    const writes = observeWrites(page);
    const reads: URL[] = [];
    const observe = (req: Request) => {
      const url = new URL(req.url());
      if (url.pathname === '/api/workspace/tasks') reads.push(url);
    };
    page.on('request', observe);
    const clear = page.getByRole('button', { name: '清除搜索和类型', exact: true });
    await expect(clear).toBeVisible();
    const cleared = page.waitForResponse((response) => {
      const url = new URL(response.url());
      return (
        url.pathname === '/api/workspace/tasks' &&
        !url.searchParams.has('q') &&
        !url.searchParams.has('kind')
      );
    });
    await clear.click();
    expect((await cleared).ok()).toBe(true);
    await expect(page.getByLabel('搜索工作事项', { exact: true })).toHaveValue('');
    await expect(page.getByLabel('筛选待办类型', { exact: true })).toHaveValue('');
    await expect(waiting).toHaveAttribute('aria-pressed', 'true');
    // With an explicit deep-link ID the picker deliberately waits until opened
    // before looking up a label. URL and outgoing request below verify the ID.
    await expect(page.getByRole('button', { name: '筛选待办企业', exact: true })).toContainText(
      '指定企业',
    );
    await expect(page.locator(`[data-task-key="business:${f.event.id}"]`)).toBeVisible();
    expect(new URL(page.url()).searchParams.get('company')).toBe(f.company.id);
    await page.screenshot({ path: `${output}/clear-filter-${device}.png`, fullPage: true });
    page.off('request', observe);
    expect(reads).toHaveLength(1);
    expect(reads[0].searchParams.get('company_id')).toBe(f.company.id);
    expect(reads[0].searchParams.get('bucket')).toBe('waiting');
    expect(writes).toEqual([]);
    await noOverflow(page);
  });

  test(`${device} V07 财务默认空态解释上游审批，查看全部保留企业及真实授权`, async ({
    page,
    request,
  }) => {
    const f = await submittedFixture(request);
    const direct = await request.get(
      `/api/business/events?scope=company&company_id=${f.company.id}&status=department_approved`,
      { headers: f.finance },
    );
    expect(direct.ok()).toBe(true);
    expect((await direct.json()).total).toBe(0);
    await bindPersona(page, 'finance');
    await page.setViewportSize(viewport);
    await page.goto('/workspace/home', { waitUntil: 'domcontentloaded' });
    await expect(page.getByRole('heading', { name: '财务核验', exact: true })).toBeVisible();
    await openBusiness(page);
    await page.getByRole('tab', { name: '企业财务', exact: true }).click();
    await page.getByLabel('筛选业务事项企业', { exact: true }).selectOption(f.company.id);
    await expect(page.getByLabel('筛选业务事项状态', { exact: true })).toHaveValue(
      'department_approved',
    );
    await expect(page.getByText(/仅显示待你核验/)).toBeVisible();
    await expect(page.getByText(/部门审批.*(不在|尚未|进入)/)).toBeVisible();
    const writes = observeWrites(page);
    await page.getByRole('button', { name: '查看全部状态', exact: true }).click();
    await expect(page.getByLabel('筛选业务事项状态', { exact: true })).toHaveValue('');
    await expect(page.getByLabel('筛选业务事项企业', { exact: true })).toHaveValue(f.company.id);
    await expect(
      page
        .getByRole('button')
        .filter({ has: page.getByText(f.event.payload.description, { exact: true }) }),
    ).toBeVisible();
    await expect(page.locator('.client-portal')).toBeVisible();
    await expect(page.getByRole('navigation', { name: '主导航', exact: true })).toHaveCount(0);
    expect(writes).toEqual([]);
    await page.screenshot({ path: `${output}/finance-upstream-${device}.png`, fullPage: true });
    await noOverflow(page);
    const employeeRead = await request.get(`/api/business/events/${f.event.id}`, {
      headers: f.employee,
    });
    expect(employeeRead.ok()).toBe(true);
    expect((await employeeRead.json()).permissions.finance_approve).toBe(false);
  });

  test(`${device} V07 就地术语可用键盘展开，字段与提交状态不变且不新增网络请求`, async ({
    page,
  }) => {
    await bindPersona(page, 'employee');
    await page.setViewportSize(viewport);
    await page.goto('/workspace/home', { waitUntil: 'domcontentloaded' });
    const loaded = page.waitForResponse(
      (response) => new URL(response.url()).pathname === '/api/intake/drafts',
    );
    await page
      .getByRole('region', { name: '提交与进度', exact: true })
      .getByRole('button', { name: /提交报销/ })
      .click();
    expect((await loaded).ok()).toBe(true);
    const dialog = page.getByRole('dialog');
    const counterparty = dialog.getByLabel('交易对方', { exact: true });
    const amount = dialog.getByLabel('事项金额', { exact: true });
    const payment = dialog.getByLabel('企业付款状态', { exact: true });
    const initial = [
      await counterparty.inputValue(),
      await amount.inputValue(),
      await payment.inputValue(),
    ];
    const requests: string[] = [];
    page.on('request', (req) => {
      if (new URL(req.url()).pathname.startsWith('/api/')) requests.push(req.url());
    });
    const term = dialog.locator('details.term-help').filter({ hasText: '交易对方是什么？' });
    await expect(term).toHaveCount(1);
    await expect(term).not.toHaveAttribute('open', '');
    const opener = term.locator('summary');
    await opener.focus();
    await page.keyboard.press('Enter');
    await expect(term).toHaveAttribute('open', '');
    await expect(term).toContainText(/(示例|例如|比如)/);
    await expect(opener).toBeFocused();
    await page.keyboard.press('Enter');
    await expect(term).not.toHaveAttribute('open', '');
    const paymentTerm = dialog
      .locator('details.term-help')
      .filter({ hasText: '企业付款状态是什么？' });
    await paymentTerm.locator('summary').click();
    await expect(paymentTerm).toHaveAttribute('open', '');
    await expect(paymentTerm).toContainText(/(不会|不代表|不触发)/);
    expect([
      await counterparty.inputValue(),
      await amount.inputValue(),
      await payment.inputValue(),
    ]).toEqual(initial);
    await expect(dialog.getByRole('button', { name: '提交审批', exact: true })).toBeDisabled();
    await expect(
      dialog
        .getByText('我已核对原件，确认日期、金额、交易对方与本次业务一致。', { exact: true })
        .locator('..')
        .locator('input'),
    ).not.toBeChecked();
    await paymentTerm.scrollIntoViewIfNeeded();
    await page.screenshot({ path: `${output}/term-help-${device}.png`, fullPage: false });
    expect(await dialog.evaluate((node) => node.scrollWidth <= node.clientWidth + 1)).toBe(true);
    expect(requests).toEqual([]);
  });
}

for (const status of [503, 403]) {
  test(`V07 待办读取 ${status} 明确恢复方式，不伪装清空或重新发送业务`, async ({ page }) => {
    await bindPersona(page, 'employee');
    await page.setViewportSize({ width: 1366, height: 900 });
    await page.goto('/workspace/home', { waitUntil: 'domcontentloaded' });
    await expect(page.getByRole('heading', { name: '我的工作台', exact: true })).toBeVisible();
    const writes = observeWrites(page);
    let attempts = 0;
    await page.route('**/api/workspace/tasks?**', async (route) => {
      attempts++;
      await route.fulfill({
        status,
        json: { detail: `V07合成待办读取${status}，请核对连接或当前权限。` },
      });
    });
    await page.getByRole('button', { name: '刷新工作台', exact: true }).click();
    const error = page.getByRole('alert').filter({ hasText: `V07合成待办读取${status}` });
    await expect(error).toBeVisible();
    await expect(page.getByText('当前筛选下没有待处理事项', { exact: true })).toHaveCount(0);
    await expect(page.getByRole('heading', { name: /登录/ })).toHaveCount(0);
    if (status === 503) {
      await expect(page.locator('.client-portal')).toBeVisible();
      const retry = page.getByRole('button', { name: '重新读取待办', exact: true });
      await expect(retry).toBeVisible();
      const before = attempts;
      await page.unroute('**/api/workspace/tasks?**');
      const recovered = page.waitForResponse(
        (response) => new URL(response.url()).pathname === '/api/workspace/tasks',
      );
      await retry.click();
      expect((await recovered).ok()).toBe(true);
      await expect(error).toHaveCount(0);
      expect(before).toBeGreaterThanOrEqual(1);
    } else {
      // A forbidden read purges capability-derived presentation until the
      // authorization refresh. The safe read-only surface is not a logout.
      await expect(page.getByRole('heading', { name: '只读工作台', exact: true })).toBeVisible();
      await expect(page.locator('.workspace-task')).toHaveCount(0);
      await expect(page.getByRole('button', { name: '重新读取权限', exact: true })).toBeVisible();
    }
    expect(writes).toEqual([]);
    await page.screenshot({ path: `${output}/read-recovery-${status}.png`, fullPage: false });
  });
}
