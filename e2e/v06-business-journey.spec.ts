import { expect, test, type APIRequestContext, type Page } from '@playwright/test';
import { readFileSync } from 'node:fs';
import type { BusinessContext, BusinessEvent } from '../src/business-types';
import type { WorkspaceTask } from '../src/workspace-types';
import type { MonthlyClientRow } from '../src/monthly-workbench-types';

test.setTimeout(60_000);
const output = `tmp_test/v06/${process.env.V06_SCREENSHOT_PHASE ?? 'green'}`;
const image = readFileSync(new URL('./fixtures/v03-receipt.png', import.meta.url));

async function identity(request: APIRequestContext, persona: string) {
  const response = await request.get(`/api/session?persona=${persona}`);
  expect(response.ok()).toBe(true);
  return { Authorization: `Bearer ${(await response.json()).token}` };
}

async function submittedEvent(request: APIRequestContext) {
  const headers = await identity(request, 'employee');
  const context: BusinessContext = await (
    await request.get('/api/business/context', { headers })
  ).json();
  const company = context.companies.find((row) => row.can_submit && row.department_id)!;
  expect(company).toBeTruthy();
  const tag = `V06虚构业务-${crypto.randomUUID().slice(0, 8)}`;
  const response = await request.post('/api/business/events', {
    headers,
    data: {
      company_id: company.id,
      department_id: company.department_id,
      template_id: 'reimbursement',
      payload: {
        occurred_on: '2026-10-12',
        description: tag,
        counterparty: '虚构供方',
        amount: '38.20',
        direction: 'expense',
        payment_status: 'unpaid',
        category: 'office',
        purpose: '仅本地办理路径验证',
      },
    },
  });
  expect(response.status()).toBe(201);
  let event: BusinessEvent = await response.json();
  const upload = await request.post(
    `/api/business/events/${event.id}/attachments?expected_version=${event.version}`,
    {
      headers,
      multipart: { file: { name: 'V06虚构票据.png', mimeType: 'image/png', buffer: image } },
    },
  );
  expect(upload.status()).toBe(200);
  event = await upload.json();
  const submit = await request.post(`/api/business/events/${event.id}/submit`, {
    headers,
    data: { expected_version: event.version },
  });
  expect(submit.status()).toBe(200);
  event = await submit.json();
  return { headers, company, event, tag };
}

async function bindEmployee(page: Page) {
  await page.route('**/api/session', async (route) => {
    const url = new URL(route.request().url());
    url.searchParams.set('persona', 'employee');
    await route.fulfill({ response: await route.fetch({ url: url.toString() }) });
  });
}

/** Pagination is synthetic read-only presentation. Opening the target still uses a real task endpoint. */
async function taskPages(page: Page, event: BusinessEvent, tag: string, companyName: string) {
  const paths: string[] = [];
  await page.route(/\/api\/workspace\/tasks\?/, async (route) => {
    const response = await route.fetch();
    if (!response.ok()) return route.fulfill({ response });
    const url = new URL(route.request().url());
    paths.push(url.pathname + url.search);
    const bucket = url.searchParams.get('bucket') ?? 'action';
    const offset = Number(url.searchParams.get('offset') ?? 0);
    const base: WorkspaceTask = {
      key: `business:${event.id}`,
      kind: 'business',
      ref: event.id,
      company_id: event.company_id,
      company_name: companyName,
      book_id: null,
      event_id: event.id,
      draft_id: null,
      batch_id: null,
      title: tag,
      state: event.status,
      priority: 2,
      due_on: null,
      updated_at: '2026-10-24T00:00:00Z',
      bucket: 'waiting',
      action_label: '查看进度',
      responsible: '部门审批人',
      overdue: false,
    };
    const rows =
      bucket !== 'waiting'
        ? []
        : offset === 20
          ? [base]
          : Array.from({ length: 20 }, (_, i) => ({
              ...base,
              key: `business:v06-display-${i}`,
              ref: `v06-display-${i}`,
              title: `${tag} · 分页展示 ${i + 1}`,
            }));
    await route.fulfill({
      response,
      json: {
        tasks: rows,
        counts: { action: 0, waiting: 21 },
        pagination: {
          total: bucket === 'waiting' ? 21 : 0,
          limit: 20,
          offset,
          has_more: bucket === 'waiting' && offset === 0,
        },
      },
    });
  });
  return paths;
}

async function monthlyFixture(request: APIRequestContext) {
  const headers = await identity(request, 'accountant');
  const tag = `V06虚构月度-${crypto.randomUUID().slice(0, 8)}`;
  const companyResponse = await request.post('/api/companies', {
    headers,
    data: { name: tag, tax_id: '' },
  });
  expect(companyResponse.status()).toBe(201);
  const company = await companyResponse.json();
  const bookResponse = await request.post('/api/books', {
    headers,
    data: { company_id: company.id, name: `${tag}-账套`, period: '2026-10' },
  });
  expect(bookResponse.status()).toBe(201);
  const book = await bookResponse.json();
  return { headers, tag, company, book };
}

async function handedOffEvent(request: APIRequestContext) {
  const f = await submittedEvent(request);
  const owner = await identity(request, 'accountant');
  const department = await identity(request, 'department');
  const finance = await identity(request, 'finance');
  const created = await request.post('/api/books', {
    headers: owner,
    data: { name: `${f.tag}-会计账套`, company_id: f.company.id, period: '2026-10' },
  });
  expect(created.status()).toBe(201);
  const book = await created.json();
  expect(
    (
      await request.put(`/api/books/${book.id}/members/local-business-finance`, {
        headers: owner,
        data: { role: 'editor' },
      })
    ).status(),
  ).toBe(200);
  let event = f.event;
  for (const [command, headers] of [
    ['department-approve', department],
    ['finance-approve', finance],
  ] as const) {
    const response = await request.post(`/api/business/events/${event.id}/${command}`, {
      headers,
      data: { expected_version: event.version },
    });
    expect(response.status()).toBe(200);
    event = await response.json();
  }
  const response = await request.post(`/api/business/events/${event.id}/handoff`, {
    headers: finance,
    data: {
      expected_version: event.version,
      book_id: book.id,
      idempotency_key: `v06-handoff-${event.id}`,
    },
  });
  expect(response.status()).toBe(200);
  const mapping = await response.json();
  expect(mapping.draft_id).toBeTruthy();
  event = await (await request.get(`/api/business/events/${event.id}`, { headers: owner })).json();
  return { ...f, event, book, owner, draftId: mapping.draft_id as string };
}

/** Only list pagination is projected; the chosen service plan is read from its actual authorized book. */
async function monthlyPages(page: Page, bookId: string, tag: string) {
  const paths: string[] = [];
  await page.route(/\/api\/monthly\/workbench\?/, async (route) => {
    const response = await route.fetch();
    if (!response.ok()) return route.fulfill({ response });
    const body = await response.json();
    const url = new URL(route.request().url());
    paths.push(url.pathname + url.search);
    let target: MonthlyClientRow | undefined = body.rows.find(
      (row: MonthlyClientRow) => row.book_id === bookId,
    );
    if (!target) {
      const one = await route.fetch({
        url: `${url.origin}/api/monthly/workbench?period=2026-10&search=${encodeURIComponent(tag)}&limit=20&offset=0`,
      });
      target = (await one.json()).rows.find((row: MonthlyClientRow) => row.book_id === bookId);
    }
    expect(target).toBeTruthy();
    const offset = Number(url.searchParams.get('offset') ?? 0);
    const rows =
      offset === 20
        ? [target]
        : Array.from({ length: 20 }, (_, i) => ({
            ...target,
            row_key: `v06-display-${i}`,
            book_id: `v06-display-${i}`,
            book_name: `${tag} · 展示账套 ${i + 1}`,
          }));
    await route.fulfill({
      response,
      json: { ...body, rows, pagination: { total: 21, limit: 20, offset, has_more: offset === 0 } },
    });
  });
  return paths;
}

async function noOverflow(page: Page) {
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(
    true,
  );
}

test('V06 修订提交成功后可以直接返回，已保存的申请不再冒充未提交编辑', async ({
  page,
  request,
}) => {
  const f = await handedOffEvent(request);
  await page.goto(`/workspace/business?company=${f.company.id}&period=2026-10&event=${f.event.id}`);
  const detail = page.getByRole('complementary', { name: '业务事项详情', exact: true });
  await detail.getByRole('button', { name: '申请事实修订', exact: true }).click();
  await page.getByLabel('修订金额', { exact: true }).fill('39.20');
  await page
    .getByLabel('事实修订依据', { exact: true })
    .fill('本地虚构修订：对照原件申请金额更正，等待另一位会计复核');
  const response = page.waitForResponse(
    (response) =>
      new URL(response.url()).pathname === `/api/fact-revisions/events/${f.event.id}` &&
      response.request().method() === 'POST',
  );
  await page.getByRole('button', { name: '提交独立复核', exact: true }).click();
  const saved = await response;
  expect(saved.status()).toBe(200);
  const revision = await saved.json();
  expect(revision.state).toBe('pending');
  const confirmations: string[] = [];
  page.on('dialog', async (dialog) => {
    confirmations.push(dialog.message());
    await dialog.accept();
  });
  await page.getByRole('button', { name: '返回原业务事项', exact: true }).click();
  await expect(detail).toContainText(f.tag);
  expect(confirmations).toEqual([]);
  const event = await (
    await request.get(`/api/business/events/${f.event.id}`, { headers: f.owner })
  ).json();
  expect(event.payload.amount).toBe('38.20');
  expect(
    (await (await request.get(`/api/fact-revisions/${revision.id}`, { headers: f.owner })).json())
      .state,
  ).toBe('pending');
});

for (const viewport of [
  { width: 1366, height: 900 },
  { width: 375, height: 812 },
]) {
  const device = viewport.width < 640 ? 'mobile' : 'desktop';

  test(`${device} V06 待办第二页直达真实事项，返回保留类型搜索排序与页码`, async ({
    page,
    request,
  }) => {
    const f = await submittedEvent(request);
    await bindEmployee(page);
    const reads = await taskPages(page, f.event, f.tag, f.company.name);
    await page.setViewportSize(viewport);
    await page.goto('/workspace/home', { waitUntil: 'domcontentloaded' });
    await expect(page.getByRole('heading', { name: '我的工作台', exact: true })).toBeVisible();
    await page.getByRole('button', { name: '显示列表选项', exact: true }).click();
    await page.getByLabel('筛选待办类型').selectOption('business');
    await page.getByLabel('搜索工作事项').fill(f.tag);
    await page.getByLabel('待办排序').selectOption('updated');
    await page
      .getByRole('region', { name: '我的工作概览', exact: true })
      .getByRole('button', { name: /我的办理进度/ })
      .click();
    await expect
      .poll(() => {
        const query = new URL(reads.at(-1) ?? '/', 'http://127.0.0.1').searchParams;
        return (
          query.get('q') === f.tag &&
          query.get('kind') === 'business' &&
          query.get('sort') === 'updated' &&
          query.get('bucket') === 'waiting'
        );
      })
      .toBe(true);
    await expect(page.locator('.workspace-task')).toHaveCount(20);
    await page.getByRole('button', { name: '下一页待办', exact: true }).click();
    const row = page.locator(`[data-task-key="business:${f.event.id}"]`);
    await expect(row).toBeVisible();
    await expect(page.locator('.workspace-task')).toHaveCount(1);
    // Assert the unique task action rather than depending on V05 wording.
    await expect(row.getByRole('button')).toHaveCount(1);
    await row.getByRole('button').click();
    await expect(
      page.getByRole('complementary', { name: '业务事项详情', exact: true }),
    ).toContainText(f.tag);
    await expect(page).toHaveURL(new RegExp(`event=${f.event.id}`));
    await page.screenshot({ path: `${output}/task-detail-${device}.png`, fullPage: false });
    await page.goBack();
    await expect(page.getByRole('heading', { name: '我的工作台', exact: true })).toBeVisible();
    await expect(page.getByLabel('搜索工作事项')).toHaveValue(f.tag);
    await expect(page.getByLabel('筛选待办类型')).toHaveValue('business');
    await expect(page.getByLabel('待办排序')).toHaveValue('updated');
    await expect(row).toBeVisible();
    await expect(page.locator('.workspace-task')).toHaveCount(1);
    expect(new URL(reads.at(-1)!, 'http://127.0.0.1').searchParams.get('offset')).toBe('20');
    await page.screenshot({ path: `${output}/task-return-${device}.png`, fullPage: true });
    await noOverflow(page);
  });

  test(`${device} V06 月度第二页进入真实服务单，返回保留月份搜索和页码`, async ({
    page,
    request,
  }) => {
    const f = await monthlyFixture(request);
    const reads = await monthlyPages(page, f.book.id, f.tag);
    await page.setViewportSize(viewport);
    await page.goto('/workspace/monthly?period=2026-10', { waitUntil: 'domcontentloaded' });
    await page.getByLabel('搜索月度客户').fill(f.tag);
    await page.getByRole('button', { name: '查找', exact: true }).click();
    await expect(page.locator('.monthly-matrix-row')).toHaveCount(20);
    await page.getByRole('button', { name: '下一页客户', exact: true }).click();
    const row = page.locator(`[data-book="${f.book.id}"]`);
    await expect(row).toBeVisible();
    await expect(page.locator('.monthly-matrix-row')).toHaveCount(1);
    await row.getByRole('button', { name: '本月服务单与资料', exact: true }).click();
    await expect(
      page.getByRole('heading', { name: `${f.company.name} · 月度服务单`, exact: true }),
    ).toBeVisible();
    await expect(page).toHaveURL(new RegExp(`book=${f.book.id}`));
    await page.screenshot({ path: `${output}/monthly-service-${device}.png`, fullPage: true });
    await page.getByRole('button', { name: '返回客户月度进度', exact: true }).click();
    await expect(page.getByLabel('客户进度月份')).toHaveValue('2026-10');
    await expect(page.getByLabel('搜索月度客户')).toHaveValue(f.tag);
    await expect(row).toBeVisible();
    await expect(page.locator('.monthly-matrix-row')).toHaveCount(1);
    expect(new URL(reads.at(-1)!, 'http://127.0.0.1').searchParams.get('offset')).toBe('20');
    await page.screenshot({ path: `${output}/monthly-return-${device}.png`, fullPage: true });
    await noOverflow(page);
  });

  test(`${device} V06 原事项直接预览原件、修订并返回，同屏复核保留原记录`, async ({
    page,
    request,
  }) => {
    const f = await handedOffEvent(request);
    const writes: string[] = [];
    page.on('request', (request) => {
      const path = new URL(request.url()).pathname;
      if (path.startsWith('/api/') && !['GET', 'HEAD'].includes(request.method()))
        writes.push(path);
    });
    await page.setViewportSize(viewport);
    await page.goto(`/workspace/business?company=${f.company.id}&period=2026-10`, {
      waitUntil: 'domcontentloaded',
    });
    await page.locator('.business-event-row').filter({ hasText: f.tag }).click();
    const detail = page.getByRole('complementary', { name: '业务事项详情', exact: true });
    await expect(detail).toContainText(f.tag);
    const linked = detail.getByRole('region', { name: '原事项关联凭证与纠错', exact: true });
    await expect(linked.getByRole('button', { name: '申请事实修订', exact: true })).toBeVisible();
    await expect(detail.getByRole('button', { name: '申请事实修订', exact: true })).toHaveCount(1);
    await detail.getByRole('button', { name: '预览原件', exact: true }).click();
    const preview = detail.getByRole('region', { name: '同屏原件预览', exact: true });
    const original = preview.getByRole('img', { name: '原始票据 V06虚构票据.png', exact: true });
    await expect(original).toBeVisible();
    await expect
      .poll(() =>
        original.evaluate(
          (node) => node instanceof HTMLImageElement && node.complete && node.naturalWidth > 0,
        ),
      )
      .toBe(true);
    await noOverflow(page);
    expect((await original.boundingBox())!.width).toBeLessThanOrEqual(viewport.width);
    await original.scrollIntoViewIfNeeded();
    await page.screenshot({ path: `${output}/business-original-${device}.png`, fullPage: false });
    await linked.getByRole('button', { name: '申请事实修订', exact: true }).click();
    await expect(page.getByRole('heading', { name: '业务事实纠错', exact: true })).toBeVisible();
    await expect(page.getByLabel('待修订事项编号', { exact: true })).toHaveValue(f.event.id);
    await page.getByLabel('修订金额', { exact: true }).fill('39.20');
    page.once('dialog', (dialog) => dialog.dismiss());
    await page.getByRole('button', { name: '返回原业务事项', exact: true }).click();
    await expect(page.getByLabel('修订金额', { exact: true })).toHaveValue('39.20');
    page.once('dialog', (dialog) => dialog.accept());
    await page.getByRole('button', { name: '返回原业务事项', exact: true }).click();
    await expect(detail).toContainText(f.tag);
    await expect(detail).toContainText('38.20');
    await expect(page).toHaveURL(new RegExp(`event=${f.event.id}`));
    await linked.getByRole('button', { name: '打开会计复核', exact: true }).click();
    const drawer = page.getByRole('dialog', { name: '原件与凭证，一起核对', exact: true });
    await expect(drawer.getByLabel('同屏凭证摘要', { exact: true })).toHaveValue(f.tag);
    await expect(drawer.getByRole('button', { name: '确认审核', exact: true })).toBeDisabled();
    await page.screenshot({ path: `${output}/business-inline-${device}.png`, fullPage: false });
    await drawer.getByRole('button', { name: '关闭同屏复核', exact: true }).click();
    await expect(detail).toContainText(f.tag);
    await expect(page).toHaveURL(new RegExp(`event=${f.event.id}`));
    const serverEvent: BusinessEvent = await (
      await request.get(`/api/business/events/${f.event.id}`, { headers: f.owner })
    ).json();
    expect(serverEvent.payload.amount).toBe('38.20');
    expect(
      (await (await request.get(`/api/books/${f.book.id}/exports`, { headers: f.owner })).json())
        .exports,
    ).toEqual([]);
    expect(writes).toEqual([]);
    await noOverflow(page);
  });

  test(`${device} V06 服务单三阶段精确下钻同账套，返回保留服务单范围且不自动执行`, async ({
    page,
    request,
  }) => {
    const f = await monthlyFixture(request);
    const writes: string[] = [];
    page.on('request', (request) => {
      const path = new URL(request.url()).pathname;
      if (path.startsWith('/api/') && !['GET', 'HEAD'].includes(request.method()))
        writes.push(path);
    });
    await page.setViewportSize(viewport);
    await page.goto(
      `/workspace/service?book=${f.book.id}&company=${f.company.id}&period=2026-10&material=stale-material-a&event=stale-event-a`,
      {
        waitUntil: 'domcontentloaded',
      },
    );
    for (const [action, view, heading] of [
      ['打开本期资料', 'materials', '本月资料清单'],
      ['打开会计复核', 'review', '集中复核，把时间留给判断。'],
      ['核验交付回执', 'delivery', '交付与回执核验'],
    ] as const) {
      await expect(
        page.getByRole('heading', { name: `${f.company.name} · 月度服务单`, exact: true }),
      ).toBeVisible();
      await page.getByRole('button', { name: action, exact: true }).click();
      await expect(page.getByRole('heading', { name: heading, exact: true })).toBeVisible();
      await expect.poll(() => new URL(page.url()).pathname).toBe(`/workspace/${view}`);
      const query = new URL(page.url()).searchParams;
      expect(query.get('book')).toBe(f.book.id);
      expect(query.get('company')).toBe(f.company.id);
      expect(query.get('period')).toBe('2026-10');
      expect(query.has('material')).toBe(false);
      expect(query.has('event')).toBe(false);
      await page.screenshot({ path: `${output}/service-${view}-${device}.png`, fullPage: false });
      await page.getByRole('button', { name: '返回本月服务单', exact: true }).click();
      await expect(
        page.getByRole('heading', { name: `${f.company.name} · 月度服务单`, exact: true }),
      ).toBeVisible();
      expect(new URL(page.url()).searchParams.get('book')).toBe(f.book.id);
    }
    const journey = page.getByRole('navigation', { name: '本月办理路径', exact: true });
    await expect(journey).toBeVisible();
    const titleBox = await journey
      .getByRole('heading', { name: '沿着这三步继续办理' })
      .boundingBox();
    const firstStepBox = await journey.locator('li').first().boundingBox();
    expect(firstStepBox!.y).toBeGreaterThanOrEqual(titleBox!.y + titleBox!.height);
    if (device === 'mobile') {
      const listBox = await journey.locator('ol').boundingBox();
      expect(listBox!.width).toBeGreaterThan(viewport.width * 0.65);
    }
    await page.screenshot({ path: `${output}/service-return-${device}.png`, fullPage: true });
    expect(writes).toEqual([]);
    expect(
      (await (await request.get(`/api/books/${f.book.id}/exports`, { headers: f.headers })).json())
        .exports,
    ).toEqual([]);
    await noOverflow(page);
  });
}
