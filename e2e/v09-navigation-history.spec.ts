import { expect, test, type APIRequestContext, type Page } from '@playwright/test';
import { readFileSync } from 'node:fs';
import type { BusinessContext, BusinessEvent } from '../src/business-types';
import type { WorkspaceProfile, WorkspaceTask } from '../src/workspace-types';
import { openNavigation } from './ui-navigation';

test.setTimeout(60_000);
const output = `tmp_test/v09/${process.env.V09_SCREENSHOT_PHASE ?? 'green'}`;
const image = readFileSync(new URL('./fixtures/v03-receipt.png', import.meta.url));

async function identity(request: APIRequestContext, persona: string) {
  const response = await request.get(`/api/session?persona=${persona}`);
  expect(response.ok()).toBe(true);
  return { Authorization: `Bearer ${(await response.json()).token}` };
}

/** Real local approval/handoff fixture. No model or approved/exported voucher is fabricated. */
async function handedOffFixture(request: APIRequestContext) {
  const employee = await identity(request, 'employee');
  const owner = await identity(request, 'accountant');
  const department = await identity(request, 'department');
  const finance = await identity(request, 'finance');
  const context: BusinessContext = await (
    await request.get('/api/business/context', { headers: employee })
  ).json();
  const company = context.companies.find((row) => row.can_submit && row.department_id)!;
  expect(company).toBeTruthy();
  const tag = `V09虚构返回路径-${crypto.randomUUID().slice(0, 8)}`;
  const created = await request.post('/api/business/events', {
    headers: employee,
    data: {
      company_id: company.id,
      department_id: company.department_id,
      template_id: 'reimbursement',
      payload: {
        occurred_on: '2026-10-12',
        description: tag,
        counterparty: '虚构导航验证供方',
        amount: '38.20',
        direction: 'expense',
        payment_status: 'unpaid',
        category: 'office',
        purpose: '仅本地导航与历史恢复验证',
      },
    },
  });
  expect(created.status()).toBe(201);
  let event: BusinessEvent = await created.json();
  const upload = await request.post(
    `/api/business/events/${event.id}/attachments?expected_version=${event.version}`,
    {
      headers: employee,
      multipart: { file: { name: 'V09虚构原件.png', mimeType: 'image/png', buffer: image } },
    },
  );
  expect(upload.status()).toBe(200);
  event = await upload.json();
  const bookResponse = await request.post('/api/books', {
    headers: owner,
    data: { name: `${tag}-账套`, company_id: company.id, period: '2026-10' },
  });
  expect(bookResponse.status()).toBe(201);
  const book = await bookResponse.json();
  expect(
    (
      await request.put(`/api/books/${book.id}/members/local-business-finance`, {
        headers: owner,
        data: { role: 'editor' },
      })
    ).status(),
  ).toBe(200);
  for (const [command, headers] of [
    ['submit', employee],
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
  const handoff = await request.post(`/api/business/events/${event.id}/handoff`, {
    headers: finance,
    data: {
      expected_version: event.version,
      book_id: book.id,
      idempotency_key: `v09-handoff-${event.id}`,
    },
  });
  expect(handoff.status()).toBe(200);
  expect((await handoff.json()).draft_id).toBeTruthy();
  event = await (await request.get(`/api/business/events/${event.id}`, { headers: owner })).json();
  expect(event.status).toBe('handed_off');
  return { owner, company, book, event, tag };
}

/**
 * Only the task presentation is synthetic: handed-off events are intentionally
 * absent from today's real business-task list. Before projecting its task row,
 * re-read the actual event with the browser's bearer. All detail/revision reads
 * and final assertions go through the real local API. This is navigation proof,
 * not a claim of new task eligibility or PostgreSQL RLS coverage.
 */
async function pagedTask(page: Page, fixture: Awaited<ReturnType<typeof handedOffFixture>>) {
  const reads: string[] = [];
  const task: WorkspaceTask = {
    key: `business:${fixture.event.id}`,
    kind: 'business',
    ref: fixture.event.id,
    company_id: fixture.company.id,
    company_name: fixture.company.name,
    book_id: null,
    event_id: fixture.event.id,
    draft_id: null,
    batch_id: null,
    title: fixture.tag,
    state: 'handed_off',
    priority: 2,
    due_on: null,
    updated_at: '2026-10-24T00:00:00Z',
    bucket: 'waiting',
    action_label: '查看进度',
    responsible: '会计复核员',
    overdue: false,
  };
  await page.route(/\/api\/workspace\/tasks\?/, async (route) => {
    const response = await route.fetch();
    if (!response.ok()) return route.fulfill({ response });
    const url = new URL(route.request().url());
    reads.push(url.pathname + url.search);
    const bucket = url.searchParams.get('bucket') ?? 'action';
    const offset = Number(url.searchParams.get('offset') ?? 0);
    const rows =
      bucket !== 'waiting'
        ? []
        : offset === 20
          ? [task]
          : Array.from({ length: 20 }, (_, i) => ({
              ...task,
              key: `business:v09-presentation-${i}`,
              ref: `v09-presentation-${i}`,
              title: `${fixture.tag} · 展示 ${i + 1}`,
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
  await page.route(`**/api/workspace/tasks/business/${fixture.event.id}`, async (route) => {
    const url = new URL(route.request().url());
    const source = await route.fetch({
      url: `${url.origin}/api/business/events/${fixture.event.id}`,
    });
    if (!source.ok()) return route.fulfill({ response: source });
    const actual: BusinessEvent = await source.json();
    expect(actual.company_id).toBe(fixture.company.id);
    expect(actual.id).toBe(task.event_id);
    await route.fulfill({ response: source, json: { ...task, state: actual.status } });
  });
  return reads;
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
  test(`${device} V09 同URL不同历史条目后退仍保护未保存修订，取消后继续保护普通后退`, async ({
    page,
    request,
  }) => {
    const fixture = await handedOffFixture(request);
    const writes: string[] = [];
    page.on('request', (request) => {
      const path = new URL(request.url()).pathname;
      if (path.startsWith('/api/') && !['GET', 'HEAD'].includes(request.method()))
        writes.push(path);
    });
    await page.setViewportSize(viewport);
    await page.goto(
      `/workspace/business?company=${fixture.company.id}&period=2026-10&event=${fixture.event.id}`,
      { waitUntil: 'domcontentloaded' },
    );
    const detail = page.getByRole('complementary', { name: '业务事项详情', exact: true });
    await expect(detail).toContainText(fixture.tag);
    await detail.getByRole('button', { name: '申请事实修订', exact: true }).click();
    const amount = page.getByLabel('修订金额', { exact: true });
    await expect(amount).toHaveValue('38.20');
    const revisionUrl = page.url();
    await page.getByRole('button', { name: '返回原业务事项', exact: true }).click();
    await expect(detail).toContainText(fixture.tag);
    await detail.getByRole('button', { name: '申请事实修订', exact: true }).click();
    await expect(amount).toHaveValue('38.20');
    await expect(page).toHaveURL(revisionUrl);
    await amount.fill('39.20');

    // These two revision entries have the same URL but different router keys.
    // A location-key change must not clear the current dirty state silently.
    await page.evaluate(() => window.history.go(-2));
    const leave = page.getByRole('dialog', { name: '当前工作尚未结束', exact: true });
    await expect(leave).toBeVisible();
    await leave.getByRole('button', { name: '继续当前工作', exact: true }).click();
    await expect(leave).toHaveCount(0);
    await expect(amount).toHaveValue('39.20');
    await expect(page).toHaveURL(revisionUrl);
    await expect(page.getByRole('button', { name: '返回原业务事项', exact: true })).toBeVisible();
    await page.screenshot({ path: `${output}/same-url-dirty-stay-${device}.png`, fullPage: false });

    await page.evaluate(() => window.history.go(-2));
    await expect(leave).toBeVisible();
    await leave.getByRole('button', { name: '放弃修改并离开', exact: true }).click();
    await expect(leave).toHaveCount(0);
    await expect(page).toHaveURL(revisionUrl);
    await expect(amount).toHaveValue('38.20');
    // Type and press native back in one browser task. An extra Playwright
    // roundtrip must not accidentally give passive effects time to arm safety.
    await amount.evaluate((node) => {
      const input = node as HTMLInputElement;
      const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!;
      setter.call(input, '39.20');
      input.dispatchEvent(new Event('input', { bubbles: true }));
      window.history.back();
    });
    await expect(leave).toBeVisible();
    await expect(amount).toHaveValue('39.20');
    await leave.getByRole('button', { name: '放弃修改并离开', exact: true }).click();
    await expect(leave).toHaveCount(0);
    await expect(detail).toContainText(fixture.tag);
    expect(new URL(page.url()).pathname).toBe('/workspace/business');
    expect(new URL(page.url()).searchParams.get('company')).toBe(fixture.company.id);
    expect(new URL(page.url()).searchParams.get('event')).toBe(fixture.event.id);
    expect(writes).toEqual([]);
    const event = await (
      await request.get(`/api/business/events/${fixture.event.id}`, { headers: fixture.owner })
    ).json();
    expect(event.payload.amount).toBe('38.20');
    expect(event.status).toBe('handed_off');
    await noOverflow(page);
    await page.screenshot({
      path: `${output}/same-url-dirty-discard-${device}.png`,
      fullPage: false,
    });
  });

  test(`${device} V09 带账套的事项修订经过后退前进后返回真实URL保留原账套`, async ({
    page,
    request,
  }) => {
    const fixture = await handedOffFixture(request);
    await page.setViewportSize(viewport);
    await page.goto(
      `/workspace/business?book=${fixture.book.id}&company=${fixture.company.id}&period=2026-10&event=${fixture.event.id}`,
      { waitUntil: 'domcontentloaded' },
    );
    const detail = page.getByRole('complementary', { name: '业务事项详情', exact: true });
    await expect(detail).toContainText(fixture.tag);
    await detail.getByRole('button', { name: '申请事实修订', exact: true }).click();
    await expect(page.getByLabel('待修订事项编号', { exact: true })).toHaveValue(fixture.event.id);
    await expect(page).toHaveURL(/\/workspace\/revision\?/);
    await page.goBack();
    await expect(detail).toContainText(fixture.tag);
    expect(new URL(page.url()).searchParams.get('book')).toBe(fixture.book.id);
    await page.goForward();
    await expect(page.getByLabel('待修订事项编号', { exact: true })).toHaveValue(fixture.event.id);
    await expect(page).toHaveURL(/\/workspace\/revision\?/);
    // State-driven navigation must also leave restoration, not just a direct router link.
    const historyLength = await page.evaluate(() => history.length);
    await openNavigation(page);
    await page
      .getByRole('navigation', { name: '主导航', exact: true })
      .getByRole('button', { name: '月度交付', exact: true })
      .click();
    await expect(page).toHaveURL(/\/workspace\/monthly(?:\?|$)/);
    await expect(page.getByRole('heading', { name: '客户月度工作', exact: true })).toBeVisible();
    expect(await page.evaluate(() => history.length)).toBe(historyLength + 1);
    await page.goBack();
    await expect(page).toHaveURL(/\/workspace\/revision\?/);
    await expect(page.getByLabel('待修订事项编号', { exact: true })).toHaveValue(fixture.event.id);
    await page.getByRole('button', { name: '返回原业务事项', exact: true }).click();
    await expect(detail).toContainText(fixture.tag);
    // A rendered detail does not prove the router finished restoring its key.
    await expect.poll(() => new URL(page.url()).pathname).toBe('/workspace/business');
    const query = new URL(page.url()).searchParams;
    expect(query.get('book')).toBe(fixture.book.id);
    expect(query.get('company')).toBe(fixture.company.id);
    expect(query.get('period')).toBe('2026-10');
    expect(query.get('event')).toBe(fixture.event.id);
    await noOverflow(page);
    await page.screenshot({ path: `${output}/book-scoped-return-${device}.png`, fullPage: false });
  });

  test(`${device} V09 多层后退再前进仍返回原事项，再返回待办保留筛选与第二页`, async ({
    page,
    request,
  }) => {
    const fixture = await handedOffFixture(request);
    const reads = await pagedTask(page, fixture);
    const writes: string[] = [];
    page.on('request', (request) => {
      const path = new URL(request.url()).pathname;
      if (path.startsWith('/api/') && !['GET', 'HEAD'].includes(request.method()))
        writes.push(path);
    });
    await page.setViewportSize(viewport);
    await page.goto('/workspace/home', { waitUntil: 'domcontentloaded' });
    await page.getByLabel('筛选待办类型').selectOption('business');
    await page.getByLabel('搜索工作事项').fill(fixture.tag);
    await page.getByLabel('待办排序').selectOption('updated');
    await page.getByRole('button', { name: /等待他人 \/ 后台/ }).click();
    await expect
      .poll(() => {
        const query = new URL(reads.at(-1) ?? '/', 'http://127.0.0.1').searchParams;
        return (
          query.get('q') === fixture.tag &&
          query.get('kind') === 'business' &&
          query.get('sort') === 'updated' &&
          query.get('bucket') === 'waiting'
        );
      })
      .toBe(true);
    await expect(page.locator('.workspace-task')).toHaveCount(20);
    await page.getByRole('button', { name: '下一页待办', exact: true }).click();
    const row = page.locator(`[data-task-key="business:${fixture.event.id}"]`);
    await row.getByRole('button', { name: '查看进度', exact: true }).click();
    const detail = page.getByRole('complementary', { name: '业务事项详情', exact: true });
    await expect(detail).toContainText(fixture.tag);
    await detail.getByRole('button', { name: '申请事实修订', exact: true }).click();
    await expect(page.getByRole('heading', { name: '业务事实纠错', exact: true })).toBeVisible();
    await expect(page.getByLabel('待修订事项编号', { exact: true })).toHaveValue(fixture.event.id);

    await page.goBack();
    await expect(detail).toContainText(fixture.tag);
    await expect(page).toHaveURL(new RegExp(`event=${fixture.event.id}`));
    await page.goBack();
    await expect(page).toHaveURL(/\/workspace\/home(?:[?#]|$)/);
    await expect(page.getByLabel('搜索工作事项')).toHaveValue(fixture.tag);
    await expect(row).toBeVisible();
    // Reconstruct both origins after the history traversal reaches its root;
    // one Back alone can leave an older in-memory origin and conceal the gap.
    await page.goForward();
    await expect(detail).toContainText(fixture.tag);
    await expect(page.getByRole('button', { name: '返回待办', exact: true })).toBeVisible();
    await page.goForward();
    await expect(page.getByRole('heading', { name: '业务事实纠错', exact: true })).toBeVisible();
    await expect(page.getByLabel('待修订事项编号', { exact: true })).toHaveValue(fixture.event.id);
    await page.screenshot({ path: `${output}/forward-revision-${device}.png`, fullPage: false });
    await page.getByRole('button', { name: '返回原业务事项', exact: true }).click();
    // Labels alone are insufficient: assert the actual origin and scope.
    await expect(detail).toContainText(fixture.tag);
    expect(new URL(page.url()).pathname).toBe('/workspace/business');
    expect(new URL(page.url()).searchParams.get('event')).toBe(fixture.event.id);
    expect(new URL(page.url()).searchParams.get('company')).toBe(fixture.company.id);
    await page.getByRole('button', { name: '返回待办', exact: true }).click();
    await expect(page).toHaveURL(/\/workspace\/home(?:[?#]|$)/);
    await expect(page.getByLabel('搜索工作事项')).toHaveValue(fixture.tag);
    await expect(page.getByLabel('筛选待办类型')).toHaveValue('business');
    await expect(page.getByLabel('待办排序')).toHaveValue('updated');
    await expect(row).toBeVisible();
    await expect(page.locator('.workspace-task')).toHaveCount(1);
    expect(new URL(reads.at(-1)!, 'http://127.0.0.1').searchParams.get('offset')).toBe('20');
    await noOverflow(page);
    await page.screenshot({ path: `${output}/nested-return-home-${device}.png`, fullPage: true });
    // Page-level return buttons create new router entries too. Returning by
    // browser history to that business entry must restore its own origin.
    await page.goBack();
    await expect(detail).toContainText(fixture.tag);
    expect(new URL(page.url()).searchParams.get('event')).toBe(fixture.event.id);
    expect(new URL(page.url()).searchParams.get('company')).toBe(fixture.company.id);
    await expect(page.getByRole('button', { name: '返回待办', exact: true })).toBeVisible();
    await page.goForward();
    await expect(page).toHaveURL(/\/workspace\/home(?:[?#]|$)/);
    await expect(page.getByLabel('搜索工作事项')).toHaveValue(fixture.tag);
    await expect(page.getByLabel('筛选待办类型')).toHaveValue('business');
    await expect(page.getByLabel('待办排序')).toHaveValue('updated');
    await expect(row).toBeVisible();
    expect(new URL(reads.at(-1)!, 'http://127.0.0.1').searchParams.get('offset')).toBe('20');
    expect(writes).toEqual([]);
    const event = await (
      await request.get(`/api/business/events/${fixture.event.id}`, { headers: fixture.owner })
    ).json();
    expect(event.payload.amount).toBe('38.20');
    expect(event.status).toBe('handed_off');
    expect(
      (
        await (
          await request.get(`/api/books/${fixture.book.id}/exports`, { headers: fixture.owner })
        ).json()
      ).exports,
    ).toEqual([]);
  });

  test(`${device} V09 子页面明确归属月度服务和客户账套且只有一个导航高亮`, async ({
    page,
    request,
  }) => {
    const fixture = await handedOffFixture(request);
    await page.setViewportSize(viewport);
    for (const [view, current] of [
      ['service', '月度交付'],
      ['catalog', '客户企业'],
      ['ledger', '客户企业'],
    ] as const) {
      await page.goto(
        `/workspace/${view}?book=${fixture.book.id}&company=${fixture.company.id}&period=2026-10`,
        { waitUntil: 'domcontentloaded' },
      );
      await expect(page.locator('.agent-sidebar')).toBeVisible();
      await openNavigation(page);
      const nav = page.getByRole('navigation', { name: '主导航', exact: true });
      await expect(nav.locator('[aria-current="page"]')).toHaveCount(1);
      await expect(nav.getByRole('button', { name: current, exact: true })).toHaveAttribute(
        'aria-current',
        'page',
      );
      expect(new URL(page.url()).searchParams.get('book')).toBe(fixture.book.id);
      await noOverflow(page);
      await page.screenshot({ path: `${output}/child-${view}-${device}.png`, fullPage: false });
    }
  });

  test(`${device} V09 管理身份缺少会计能力时不展示会计入口但保留管理入口`, async ({ page }) => {
    // A rendering contract projection, not backend authorization evidence.
    // Current real local owner identity still supplies the management grants.
    await page.route(/\/api\/workspace\/(bootstrap|profile)(\?|$)/, async (route) => {
      const response = await route.fetch();
      if (!response.ok()) return route.fulfill({ response });
      const body = await response.json();
      const profile: WorkspaceProfile = body.profile ?? body;
      profile.can_accounting = false;
      profile.can_review = false;
      profile.can_manage = true;
      profile.can_firm_operations = true;
      await route.fulfill({ response, json: body.profile ? { ...body, profile } : profile });
    });
    await page.setViewportSize(viewport);
    await page.goto('/workspace/firm', { waitUntil: 'domcontentloaded' });
    await expect(page.getByRole('heading', { name: '全所工作台', exact: true })).toBeVisible();
    await openNavigation(page);
    const nav = page.getByRole('navigation', { name: '主导航', exact: true });
    for (const name of ['客户企业', '会计工作台', '跨企业批量复核', '事实修订与调整'])
      await expect(nav.getByRole('button', { name, exact: true })).toHaveCount(0);
    await expect(nav.getByRole('button', { name: '全所概览', exact: true })).toBeVisible();
    await expect(nav.getByRole('button', { name: '月度交付', exact: true })).toBeVisible();
    const settings = nav.locator('details[aria-label="设置"]');
    await settings.locator('summary').click();
    await expect(
      settings.getByRole('button', { name: '企业与人员管理', exact: true }),
    ).toBeVisible();
    await expect(
      settings.getByRole('button', { name: '成员与账套授权', exact: true }),
    ).toBeVisible();
    await noOverflow(page);
    await page.screenshot({
      path: `${output}/management-capabilities-${device}.png`,
      fullPage: false,
    });
  });
}
