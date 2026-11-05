import { readFileSync } from 'node:fs';
import {
  expect,
  test,
  type APIRequestContext,
  type APIResponse,
  type Page,
} from '@playwright/test';
import { choosePersona } from './ui-navigation';

test.setTimeout(90_000);
async function body(response: APIResponse, status = 200) {
  expect(response.status(), await response.text()).toBe(status);
  return response.json();
}
async function auth(request: APIRequestContext, persona = 'employee') {
  const value = await body(await request.get(`/api/session?persona=${persona}`));
  return { Authorization: `Bearer ${value.token}` };
}
async function returned(request: APIRequestContext) {
  const headers = await auth(request),
    approver = await auth(request, 'department');
  const context = await body(await request.get('/api/business/context', { headers }));
  const company = context.companies.find((c: { can_submit: boolean }) => c.can_submit);
  let event = await body(
    await request.post('/api/business/events', {
      headers,
      data: {
        company_id: company.id,
        department_id: company.department_id,
        template_id: 'reimbursement',
        payload: {
          occurred_on: '2026-10-03',
          description: `返还补件-${crypto.randomUUID()}`,
          counterparty: '虚构商户',
          amount: '128.50',
          direction: 'expense',
          payment_status: 'unpaid',
          category: 'travel',
          purpose: '原用途',
        },
      },
    }),
    201,
  );
  event = await body(
    await request.post(
      `/api/business/events/${event.id}/attachments?expected_version=${event.version}`,
      {
        headers,
        multipart: {
          file: {
            name: '原始票据.pdf',
            mimeType: 'application/pdf',
            buffer: Buffer.from('%PDF-1.4\n% synthetic only\n%%EOF\n'),
          },
        },
      },
    ),
  );
  event = await body(
    await request.post(`/api/business/events/${event.id}/submit`, {
      headers,
      data: { expected_version: event.version },
    }),
  );
  event = await body(
    await request.post(`/api/business/events/${event.id}/return`, {
      headers: approver,
      data: { expected_version: event.version, reason: '请补充本次差旅用途和行程原件' },
    }),
  );
  return { event, headers, company };
}
async function openReturned(page: Page, title: string) {
  await page.goto('/workspace/home');
  await choosePersona(page, 'employee');
  const expand = page.getByRole('button', { name: /展开本页全部/ });
  if (await expand.count()) await expand.click();
  await page.locator('.workspace-task').filter({ hasText: title }).getByRole('button').click();
  await page
    .getByLabel('业务事项详情', { exact: true })
    .getByRole('button', { name: '修改并重新提交', exact: true })
    .click();
  return page.getByRole('dialog', { name: '修改业务事项', exact: true });
}
for (const width of [1280, 390]) {
  test(`首屏新建与清单展开 ${width}`, async ({ page, request }) => {
    await page.setViewportSize({ width, height: 844 });
    for (let i = 0; i < 4; i++) await returned(request);
    await page.goto('/workspace/home');
    await choosePersona(page, 'employee');
    const entry = page.getByRole('button', { name: /^提交报销/ });
    await expect(entry).toBeInViewport();
    await expect(page.locator('.workspace-task')).toHaveCount(3);
    await expect(page.locator('.workspace-task').first().getByRole('button')).toBeInViewport({
      ratio: 0.8,
    });
    await page.screenshot({
      path: `tmp_test/employee-flow-20261105/home-${width}.png`,
      fullPage: true,
    });
    await page.getByRole('button', { name: /展开本页全部/ }).click();
    await expect.poll(() => page.locator('.workspace-task').count()).toBeGreaterThan(3);
    await expect(page.getByRole('button', { name: '收起清单', exact: true })).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(
      true,
    );
  });
  test(`票据预填后先确认字段，依据按需展开 ${width}`, async ({ page, request }) => {
    await page.setViewportSize({ width, height: 844 });
    const headers = await auth(request),
      manager = await auth(request, 'accountant');
    const context = await body(await request.get('/api/business/context', { headers }));
    const company = context.companies.find((c: { can_submit: boolean }) => c.can_submit);
    await body(
      await request.put(`/api/companies/${company.id}/intelligence/settings`, {
        headers: manager,
        data: { enabled: true },
      }),
    );
    await page.goto('/workspace/home');
    await choosePersona(page, 'employee');
    await page.getByRole('button', { name: /^提交报销/ }).click();
    const dialog = page.getByRole('dialog', { name: '录入业务事项', exact: true });
    const xml = readFileSync(
      'nexus_backend/tests/fixtures/stage16_SYNTHETIC_einvoice_0_3.xml',
      'utf8',
    ).replace('第一客户企业', company.name);
    await dialog.getByLabel('一体化录入上传附件').setInputFiles({
      name: '离线合成票.xml',
      mimeType: 'application/xml',
      buffer: Buffer.from(xml),
    });
    await dialog.getByRole('button', { name: '解析 XML 并预填', exact: true }).click();
    await expect(dialog.getByText('识别建议已就绪', { exact: true })).toBeVisible();
    await expect(dialog.getByLabel('发生日期', { exact: true })).toHaveValue('2026-10-03');
    await expect(dialog.getByLabel('交易对方', { exact: true })).toHaveValue('耗材商店');
    await expect(dialog.locator('.intake-candidate')).not.toBeVisible();
    await expect(dialog.getByRole('button', { name: '提交审批', exact: true })).toBeDisabled();
    await dialog.getByRole('button', { name: '核对已填写信息', exact: true }).click();
    await expect(dialog.getByLabel('事项金额', { exact: true })).toBeFocused();
    await page.screenshot({ path: `tmp_test/employee-flow-20261105/confirm-${width}.png` });
    await dialog
      .locator('summary')
      .filter({ hasText: /^查看识别依据/ })
      .click();
    await expect(dialog.locator('.intake-candidate')).toBeVisible();
    expect(await dialog.evaluate((node) => node.scrollWidth <= innerWidth + 1)).toBe(true);
  });
  test(`退回原因直达修改并在原事项重新提交 ${width}`, async ({ page, request }) => {
    await page.setViewportSize({ width, height: 844 });
    const { event, headers } = await returned(request);
    const dialog = await openReturned(page, event.payload.description);
    await expect(
      dialog.getByText('请补充本次差旅用途和行程原件', { exact: true }),
    ).toBeInViewport();
    await expect(dialog.getByLabel('事项金额', { exact: true })).toHaveValue('128.50');
    await expect(
      dialog.getByRole('button', { name: '修改后重新提交', exact: true }),
    ).toBeDisabled();
    await page.screenshot({ path: `tmp_test/employee-flow-20261105/returned-${width}.png` });
    await dialog.getByRole('button', { name: '修改信息', exact: true }).click();
    await expect(dialog.getByLabel('事项摘要', { exact: true })).toBeFocused();
    await dialog.getByLabel('业务用途', { exact: true }).fill('补充：拜访虚构客户的市内交通');
    await dialog.getByRole('button', { name: '补充附件', exact: true }).click();
    await dialog.getByLabel('一体化录入上传附件').setInputFiles({
      name: '补充行程.pdf',
      mimeType: 'application/pdf',
      buffer: Buffer.from('%PDF-1.4\n% additional synthetic evidence\n%%EOF\n'),
    });
    await expect(dialog.getByText('补充行程.pdf', { exact: true })).toBeVisible();
    await expect(dialog.getByText('原始票据.pdf', { exact: true })).toBeVisible();
    await dialog.getByRole('checkbox').check();
    await dialog.getByRole('button', { name: '修改后重新提交', exact: true }).click();
    await expect(dialog).toHaveCount(0);
    const current = await body(await request.get(`/api/business/events/${event.id}`, { headers }));
    expect(current.id).toBe(event.id);
    expect(current.company_id).toBe(event.company_id);
    expect(current.status).toBe('submitted');
    expect(current.attachments).toHaveLength(2);
    expect(current.attachments.some((a: { id: string }) => a.id === event.attachments[0].id)).toBe(
      true,
    );
    expect(current.versions).toHaveLength(2);
    expect(current.payload.purpose).toBe('补充：拜访虚构客户的市内交通');
    expect(current.actions.some((a: { decision: string }) => a.decision === 'return')).toBe(true);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(
      true,
    );
  });
}

test('修改原事项只自动保存一次，空闲时不反复增加版本', async ({ page, request }) => {
  const { event, headers } = await returned(request);
  const dialog = await openReturned(page, event.payload.description);
  const writes: string[] = [];
  page.on('request', (r) => {
    if (r.method() === 'PATCH' && new URL(r.url()).pathname === `/api/business/events/${event.id}`)
      writes.push(r.url());
  });
  await dialog.getByLabel('事项摘要', { exact: true }).fill('改一次后等待自动保存');
  await expect(dialog.locator('.intake-save-state')).toContainText('修改已保存');
  const saved = await body(await request.get(`/api/business/events/${event.id}`, { headers }));
  await page.waitForTimeout(2800); // More than two autosave intervals; no user action.
  const idle = await body(await request.get(`/api/business/events/${event.id}`, { headers }));
  expect(writes).toHaveLength(1);
  expect(idle.version).toBe(saved.version);
});

test('补件时撤权清除原件与原因并禁止改成新建提交', async ({ page, request }) => {
  const { event } = await returned(request);
  const dialog = await openReturned(page, event.payload.description);
  await page.route(`**/api/business/events/${event.id}/attachments?*`, (route) =>
    route.fulfill({ status: 403, json: { detail: '当前权限已撤销' } }),
  );
  await dialog.getByLabel('一体化录入上传附件').setInputFiles({
    name: '失权测试.pdf',
    mimeType: 'application/pdf',
    buffer: Buffer.from('%PDF-1.4\n% revoked\n%%EOF\n'),
  });
  await expect(dialog.getByRole('alert')).toContainText('当前权限已撤销');
  await expect(dialog.getByText('原始票据.pdf', { exact: true })).toHaveCount(0);
  await expect(dialog.getByText('请补充本次差旅用途和行程原件', { exact: true })).toHaveCount(0);
  await expect(dialog.getByRole('button', { name: /重新提交|提交审批/ })).toBeDisabled();
  await expect(dialog.getByLabel('事项金额', { exact: true })).toBeDisabled();
});

test('退回修改遇到版本冲突只能读取原事项，不能自动重试提交', async ({ page, request }) => {
  const { event, headers } = await returned(request);
  const dialog = await openReturned(page, event.payload.description);
  let patches = 0,
    submits = 0;
  await page.route(`**/api/business/events/${event.id}`, async (route) => {
    if (route.request().method() !== 'PATCH') return route.continue();
    patches++;
    await route.fulfill({ status: 409, json: { detail: '事项版本已变更，请重新读取' } });
  });
  page.on('request', (r) => {
    if (new URL(r.url()).pathname === `/api/business/events/${event.id}/submit`) submits++;
  });
  await dialog.getByLabel('事项金额', { exact: true }).fill('130.00');
  await expect(dialog.getByRole('alert')).toContainText('事项版本已变更');
  await expect(dialog.getByRole('button', { name: '读取最新事项', exact: true })).toBeVisible();
  await dialog.getByRole('button', { name: '读取最新事项', exact: true }).click();
  await expect(dialog.getByLabel('事项金额', { exact: true })).toHaveValue('128.50');
  expect(patches).toBe(1);
  expect(submits).toBe(0);
  expect(
    (await body(await request.get(`/api/business/events/${event.id}`, { headers }))).status,
  ).toBe('returned');
});
