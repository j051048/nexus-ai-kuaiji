import { expect, test, type APIRequestContext, type Page } from '@playwright/test';
import { readFileSync } from 'node:fs';
import type { BusinessContext, BusinessEvent } from '../src/business-types';
import { choosePersona } from './ui-navigation';

test.setTimeout(60_000);
const output = `tmp_test/v06/${process.env.V06_SCREENSHOT_PHASE ?? 'green'}`;
const image = readFileSync(new URL('./fixtures/v03-receipt.png', import.meta.url));

async function identity(request: APIRequestContext, persona: string) {
  const response = await request.get(`/api/session?persona=${persona}`);
  expect(response.ok()).toBe(true);
  return { Authorization: `Bearer ${(await response.json()).token}` };
}

async function eventWithOriginal(request: APIRequestContext, persona = 'employee') {
  const headers = await identity(request, persona);
  const context: BusinessContext = await (
    await request.get('/api/business/context', { headers })
  ).json();
  const company = context.companies.find((row) => row.can_submit && row.department_id)!;
  expect(company).toBeTruthy();
  const tag = `V06权限边界虚构事项-${crypto.randomUUID().slice(0, 8)}`;
  const response = await request.post('/api/business/events', {
    headers,
    data: {
      company_id: company.id,
      department_id: company.department_id,
      template_id: 'reimbursement',
      payload: {
        occurred_on: '2026-10-12',
        description: tag,
        counterparty: '虚构边界验证供方',
        amount: '18.20',
        direction: 'expense',
        payment_status: 'unpaid',
        category: 'office',
        purpose: '仅本地隔离与返回进度验证',
      },
    },
  });
  expect(response.status()).toBe(201);
  let event: BusinessEvent = await response.json();
  const upload = await request.post(
    `/api/business/events/${event.id}/attachments?expected_version=${event.version}`,
    {
      headers,
      multipart: { file: { name: 'V06权限验证原件.png', mimeType: 'image/png', buffer: image } },
    },
  );
  expect(upload.status()).toBe(200);
  event = await upload.json();
  return { headers, company, event, tag, attachment: event.attachments[0] };
}

async function bindEmployee(page: Page) {
  // A page reload normally starts a fresh owner in local demo mode. Bind only
  // the unqualified startup request; explicit finance identity changes remain real.
  await page.route('**/api/session', async (route) => {
    const url = new URL(route.request().url());
    url.searchParams.set('persona', 'employee');
    await route.fulfill({ response: await route.fetch({ url: url.toString() }) });
  });
}

test('手机 V06 身份切换清空前账号办理进度，不继承搜索、任务和企业筛选', async ({
  page,
  request,
}) => {
  const f = await eventWithOriginal(request);
  await bindEmployee(page);
  await page.setViewportSize({ width: 375, height: 812 });
  await page.goto('/workspace/home', { waitUntil: 'domcontentloaded' });
  await expect(page.getByRole('heading', { name: '我的工作台', exact: true })).toBeVisible();
  await page.getByRole('button', { name: '显示列表选项', exact: true }).click();
  await page.getByLabel('筛选待办类型').selectOption('business');
  await page.getByLabel('搜索工作事项').fill(f.tag);
  await page.getByLabel('待办排序').selectOption('updated');
  await page.getByRole('button', { name: '筛选待办企业', exact: true }).click();
  await page
    .getByRole('region', { name: '企业快搜', exact: true })
    .getByRole('button')
    .filter({ hasText: f.company.name })
    .click();
  const previous = page.locator(`[data-task-key="business:${f.event.id}"]`);
  await expect(previous).toBeVisible();
  await expect(page).toHaveURL(new RegExp(`company=${f.company.id}`));
  await choosePersona(page, 'finance');
  await expect(page.getByRole('heading', { name: '财务核验', exact: true })).toBeVisible();
  await expect(page.getByLabel('搜索工作事项')).toHaveValue('');
  await expect(page.getByLabel('筛选待办类型')).toHaveValue('');
  await expect(previous).toHaveCount(0);
  await expect(page.getByRole('button', { name: '筛选待办企业', exact: true })).toContainText(
    '全部授权企业',
  );
  expect(new URL(page.url()).searchParams.get('company')).toBeNull();
  await page.screenshot({ path: `${output}/scope-switch-mobile.png`, fullPage: false });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(
    true,
  );
});

test('桌面 V06 原件权限拒绝撤下已加载 blob，不自动写入，并拒绝读取他人的原件', async ({
  page,
  request,
}) => {
  const own = await eventWithOriginal(request);
  const other = await eventWithOriginal(request, 'department');
  // Real local authorization evidence: same-company employee still cannot read
  // a different applicant's private draft or its original attachment.
  expect([403, 404]).toContain(
    (
      await request.get(`/api/business/events/${other.event.id}`, { headers: own.headers })
    ).status(),
  );
  expect([403, 404]).toContain(
    (
      await request.get(`/api/business/attachments/${other.attachment.id}/content`, {
        headers: own.headers,
      })
    ).status(),
  );
  expect(
    (
      await request.get(`/api/business/attachments/${own.attachment.id}/content`, {
        headers: own.headers,
      })
    ).status(),
  ).toBe(200);
  await bindEmployee(page);
  const writes: string[] = [];
  page.on('request', (request) => {
    const path = new URL(request.url()).pathname;
    if (path.startsWith('/api/') && !['GET', 'HEAD'].includes(request.method())) writes.push(path);
  });
  await page.setViewportSize({ width: 1366, height: 900 });
  await page.goto(
    `/workspace/business?company=${own.company.id}&period=2026-10&event=${own.event.id}`,
    {
      waitUntil: 'domcontentloaded',
    },
  );
  const detail = page.getByRole('complementary', { name: '业务事项详情', exact: true });
  await expect(detail).toContainText(own.tag);
  await detail.getByRole('button', { name: '预览原件', exact: true }).click();
  const preview = detail.getByRole('region', { name: '同屏原件预览', exact: true });
  const original = preview.getByRole('img', { name: '原始票据 V06权限验证原件.png', exact: true });
  await expect(original).toBeVisible();
  await expect
    .poll(() =>
      original.evaluate(
        (node) => node instanceof HTMLImageElement && node.complete && node.naturalWidth > 0,
      ),
    )
    .toBe(true);
  const previousBlob = await original.getAttribute('src');
  expect(previousBlob).toMatch(/^blob:/);
  // Explicit transport fault injection exercises the existing API denial event
  // and blob cleanup. It is not a claim that PostgreSQL RLS was tested here.
  let deniedReads = 0;
  await page.route(`**/api/business/attachments/${own.attachment.id}/content`, async (route) => {
    deniedReads++;
    await route.fulfill({
      status: 403,
      json: { detail: '合成权限变更：原件暂不可读取，请重新核对授权。' },
    });
  });
  await detail.getByRole('button', { name: '下载附件 V06权限验证原件.png', exact: true }).click();
  await expect(preview.locator('img')).toHaveCount(0);
  await expect(preview.getByRole('alert')).toContainText(/授权|权限/);
  await expect(preview.getByRole('alert')).toContainText(/重新读取|重新核对/);
  await expect(page.locator('.client-portal')).toBeVisible();
  await expect(page.getByRole('button', { name: '重新读取权限', exact: true })).toBeVisible();
  expect(
    await page.evaluate(async (url) => {
      try {
        await fetch(url!);
        return true;
      } catch {
        return false;
      }
    }, previousBlob),
  ).toBe(false);
  expect(deniedReads).toBe(1);
  expect(writes).toEqual([]);
  await preview.scrollIntoViewIfNeeded();
  await page.screenshot({
    path: `${output}/original-permission-denied-desktop.png`,
    fullPage: false,
  });
});

test('手机 V06 员工从补件待办直达本人请求，不因客户门户而退回首页', async ({ page, request }) => {
  const f = await eventWithOriginal(request);
  const finance = await identity(request, 'finance');
  // Company finance can publish a request for the employee's business facts
  // before handoff. This grants no accounting-book access to the employee.
  const created = await request.post('/api/cases', {
    headers: finance,
    data: {
      event_id: f.event.id,
      kind: 'supplement',
      title: `${f.tag} 补充用途`,
      reason: '请补充本次办公采购用途和清晰原件，仅本地虚构补件验证',
      required_fields: ['业务用途'],
      attachment_required: true,
      priority: 'urgent',
      idempotency_key: crypto.randomUUID(),
    },
  });
  expect(created.status()).toBe(201);
  const task = await created.json();
  const detailResponse = await request.get(`/api/cases/${task.id}`, { headers: f.headers });
  expect(detailResponse.status()).toBe(200);
  const ownDetail = await detailResponse.json();
  expect(ownDetail.permissions.respond).toBe(true);
  expect(ownDetail.permissions.manage).toBe(false);
  expect(ownDetail).not.toHaveProperty('book_id');
  const writes: string[] = [];
  page.on('request', (request) => {
    const path = new URL(request.url()).pathname;
    if (path.startsWith('/api/') && !['GET', 'HEAD'].includes(request.method())) writes.push(path);
  });
  await bindEmployee(page);
  await page.setViewportSize({ width: 375, height: 812 });
  await page.goto('/workspace/home', { waitUntil: 'domcontentloaded' });
  await expect(page.getByRole('heading', { name: '我的工作台', exact: true })).toBeVisible();
  await page.getByRole('button', { name: '显示列表选项', exact: true }).click();
  await page.getByLabel('筛选待办类型').selectOption('case');
  await page.getByLabel('待办排序').selectOption('updated');
  const row = page.locator(`[data-task-key="case:${task.id}"]`);
  await expect(row).toBeVisible();
  await row.getByRole('button', { name: '打开处理任务', exact: true }).click();
  await expect(page.getByRole('heading', { name: `${f.tag} 补充用途`, exact: true })).toBeVisible();
  await expect(page.getByLabel('补件字段 业务用途', { exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: '提交补件核验', exact: true })).toBeDisabled();
  await expect(page.locator('.client-portal')).toBeVisible();
  await expect(page.getByRole('navigation', { name: '主导航', exact: true })).toHaveCount(0);
  await expect(
    page
      .getByRole('navigation', { name: '客户服务', exact: true })
      .getByRole('button', { name: '异常与补件', exact: true }),
  ).toHaveCount(0);
  await expect.poll(() => new URL(page.url()).searchParams.get('case')).toBe(task.id);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(
    true,
  );
  await page.screenshot({ path: `${output}/employee-supplement-task-mobile.png`, fullPage: false });
  await page.getByRole('button', { name: '返回待办', exact: true }).click();
  await expect(page.getByRole('heading', { name: '我的工作台', exact: true })).toBeVisible();
  await expect(page.getByLabel('筛选待办类型')).toHaveValue('case');
  await expect(row).toBeVisible();
  expect(writes).toEqual([]);
});
