import { expect, test, type APIRequestContext, type Page } from '@playwright/test';
import { choosePersona } from './ui-navigation';

test.setTimeout(60_000);
async function fixture(request: APIRequestContext) {
  const session = await (await request.get('/api/session?persona=accountant')).json();
  const headers = { Authorization: `Bearer ${session.token}` };
  const created = await request.post('/api/books', {
    headers,
    data: { name: `记录-${crypto.randomUUID()}`, period: '2026-10' },
  });
  expect(created.status()).toBe(201);
  const book = await created.json();
  const conversation = (
    await (
      await request.post('/api/assistant/conversations', {
        headers,
        data: {
          company_id: book.company_id,
          book_ids: [book.id],
          title: `执行记录-${crypto.randomUUID()}`,
          idempotency_key: crypto.randomUUID(),
        },
      })
    ).json()
  ).conversation;
  const path = `/api/assistant/conversations/${conversation.id}`;
  const workspace = await (await request.get(`${path}/workspace`, { headers })).json();
  workspace.detail.turns = [
    {
      id: 'synthetic-events',
      events_available: true,
      status: 'queued',
      question: '虚构企业为何无法交付',
      created_at: '2026-10-31T00:00:00Z',
    },
  ];
  return { book, conversation, path, workspace };
}
async function open(page: Page, value: Awaited<ReturnType<typeof fixture>>) {
  await page.goto('/');
  await choosePersona(page, 'accountant');
  await page.getByRole('button', { name: '问会计助手', exact: true }).click();
  const drawer = page.getByRole('dialog', { name: '会计助手', exact: true });
  await drawer.getByLabel('助手所属企业', { exact: true }).selectOption(value.book.company_id);
  await drawer.getByRole('button', { name: '查看我的会话', exact: true }).click();
  await drawer.locator('.ga-history-row').filter({ hasText: value.conversation.title }).click();
  await expect(drawer.locator('.ga-context')).toContainText(value.conversation.title);
  return drawer;
}
const created = {
  seq: 1,
  kind: 'created',
  at: '2026-10-31T00:00:00Z',
  message: '任务已保存，等待处理。',
  accounting_approved: false,
};
const sent = {
  ...created,
  seq: 2,
  kind: 'send_marked',
  message: '已登记请求发送标记；供应商结果尚未确认。',
};
const finished = {
  ...created,
  seq: 3,
  kind: 'finished',
  message: '请求结果未知，请联系管理员核对执行与费用记录；不会自动重发。',
  status: 'interrupted',
  outcome_unknown: true,
  automatic_retry: false,
  next_action: 'inspect_execution',
};
function response(
  value: Awaited<ReturnType<typeof fixture>>,
  events: unknown[],
  cursor = 'first',
  status = 'running',
) {
  return {
    schema: 'assistant-turn-events-v1',
    company_id: value.book.company_id,
    conversation_id: value.conversation.id,
    turn_id: 'synthetic-events',
    scope_version: 1,
    events,
    cursor,
    status,
    terminal: status === 'interrupted',
    has_more: false,
  };
}
for (const width of [1440, 390]) {
  test(`增量记录续读与断线恢复，未知结果不重发 ${width}`, async ({ page, request }) => {
    await page.setViewportSize({ width, height: 900 });
    const value = await fixture(request);
    let reads = 0,
      phase = 0,
      writes = 0;
    const cursors: (string | null)[] = [];
    page.on('request', (req) => {
      if (req.method() === 'POST' && req.url().includes('/turns')) writes++;
    });
    await page.route(new RegExp(`${value.path}/workspace\\?`), async (route) => {
      reads++;
      await route.fulfill({ json: value.workspace });
    });
    await page.route(`**${value.path}/turns/synthetic-events/events?*`, async (route) => {
      const cursor = new URL(route.request().url()).searchParams.get('after');
      cursors.push(cursor);
      if (phase === 1)
        return route.fulfill({ status: 503, json: { detail: 'synthetic unavailable' } });
      await route.fulfill({
        json: response(
          value,
          phase === 2 ? [finished] : cursor ? [] : [created, sent],
          phase === 2 ? 'final' : 'first',
          phase === 2 ? 'interrupted' : 'running',
        ),
      });
    });
    await page.clock.install();
    const drawer = await open(page, value);
    await expect(drawer.getByRole('status', { name: '助手执行进度' })).toContainText('尚未确认');
    const initialReads = reads;
    phase = 1;
    await page.clock.runFor(1100);
    await expect(
      drawer.getByText('进度暂时无法读取，将从上次位置继续；不会重新发送模型请求。'),
    ).toBeVisible();
    expect(reads).toBe(initialReads);
    phase = 2;
    value.workspace.detail.turns[0].status = 'interrupted';
    await page.clock.runFor(1100);
    await expect(drawer.getByRole('status', { name: '助手执行进度' })).toContainText('结果未知');
    await expect.poll(() => reads).toBe(initialReads + 1);
    expect(cursors).toContain('first');
    expect(writes).toBe(0);
    await drawer.getByText('查看处理过程', { exact: true }).click();
    await expect(drawer.locator('.ga-turn-progress li')).toHaveCount(3);
    await page.screenshot({ path: `artifacts/assistant-events-${width}.png`, fullPage: true });
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(
      true,
    );
  });
}
for (const fault of ['revoked', 'scope', 'cursor'] as const) {
  test(`执行记录拒绝 ${fault}，不触发模型请求`, async ({ page, request }) => {
    const value = await fixture(request);
    let invalid = false;
    const cursors: (string | null)[] = [];
    await page.route(new RegExp(`${value.path}/workspace\\?`), async (route) =>
      route.fulfill({ json: value.workspace }),
    );
    await page.route(`**${value.path}/turns/synthetic-events/events?*`, async (route) => {
      cursors.push(new URL(route.request().url()).searchParams.get('after'));
      if (invalid && fault !== 'scope')
        return route.fulfill({
          status: fault === 'cursor' ? 409 : 403,
          json: { detail: 'synthetic denied' },
        });
      const body = response(value, [created, sent]);
      if (invalid) body.company_id = 'other-enterprise';
      return route.fulfill({ json: body });
    });
    await page.clock.install();
    const drawer = await open(page, value);
    await expect(drawer.getByRole('status', { name: '助手执行进度' })).toContainText('尚未确认');
    invalid = true;
    await page.clock.runFor(1100);
    if (fault === 'revoked') await expect(drawer.locator('.ga-context')).toHaveCount(0);
    else {
      await expect(drawer.getByRole('status', { name: '助手执行进度' })).toHaveCount(0);
      invalid = false;
      await page.clock.runFor(1100);
      await expect(drawer.getByRole('status', { name: '助手执行进度' })).toContainText('尚未确认');
      expect(cursors.at(-1)).toBeNull();
    }
  });
}

test('切换企业后拒绝迟到的旧执行记录，重新打开会话可从头重放', async ({ page, request }) => {
  const a = await fixture(request),
    b = await fixture(request);
  expect(a.book.company_id).not.toBe(b.book.company_id);
  let release = () => {},
    pending = false;
  const held = new Promise<void>((resolve) => {
    release = resolve;
  });
  await page.route(new RegExp(`${a.path}/workspace\\?`), (route) =>
    route.fulfill({ json: a.workspace }),
  );
  await page.route(new RegExp(`${b.path}/workspace\\?`), (route) =>
    route.fulfill({ json: b.workspace }),
  );
  await page.route(`**${a.path}/turns/synthetic-events/events?*`, async (route) => {
    pending = true;
    await held;
    await route.fulfill({ json: response(a, [created, { ...sent, message: 'A企业专属旧记录' }]) });
  });
  const bCursors: (string | null)[] = [];
  await page.route(`**${b.path}/turns/synthetic-events/events?*`, async (route) => {
    bCursors.push(new URL(route.request().url()).searchParams.get('after'));
    await route.fulfill({ json: response(b, [created, { ...sent, message: 'B企业当前记录' }]) });
  });
  const drawer = await open(page, a);
  await expect.poll(() => pending).toBe(true);
  await drawer.getByLabel('助手所属企业', { exact: true }).selectOption(b.book.company_id);
  release();
  await drawer.getByRole('button', { name: '查看我的会话', exact: true }).click();
  await drawer.locator('.ga-history-row').filter({ hasText: b.conversation.title }).click();
  await expect(drawer.getByRole('status', { name: '助手执行进度' })).toContainText('B企业当前记录');
  await expect(drawer.getByText('A企业专属旧记录')).toHaveCount(0);
  expect(bCursors[0]).toBeNull();
  await page.reload();
  // Session persists, but private progress is fetched afresh rather than cached.
  await page.getByRole('button', { name: '问会计助手', exact: true }).click();
  await drawer.getByLabel('助手所属企业', { exact: true }).selectOption(b.book.company_id);
  await drawer.getByRole('button', { name: '查看我的会话', exact: true }).click();
  await drawer.locator('.ga-history-row').filter({ hasText: b.conversation.title }).click();
  await expect(drawer.getByRole('status', { name: '助手执行进度' })).toContainText('B企业当前记录');
  expect(bCursors.filter((cursor) => cursor === null).length).toBeGreaterThanOrEqual(2);
});

test('旧服务缺少记录路由时退回现有进度接口，资源404不适用此回退', async ({ page, request }) => {
  const value = await fixture(request);
  let legacyReads = 0,
    eventReads = 0;
  await page.route(new RegExp(`${value.path}/workspace\\?`), (route) =>
    route.fulfill({ json: value.workspace }),
  );
  await page.route(`**${value.path}/turns/synthetic-events/events?*`, (route) => {
    eventReads++;
    return route.fulfill({ status: 404, json: { detail: 'Not Found' } });
  });
  await page.route(new RegExp(`${value.path}/progress\\?`), (route) => {
    legacyReads++;
    return route.fulfill({
      json: {
        company_id: value.book.company_id,
        conversation_id: value.conversation.id,
        scope_version: 1,
        revision: 'legacy',
        active: true,
      },
    });
  });
  await page.clock.install();
  const drawer = await open(page, value);
  await expect.poll(() => eventReads).toBe(1);
  await page.clock.runFor(1100);
  await expect.poll(() => legacyReads).toBeGreaterThan(0);
  await expect(drawer.locator('.ga-context')).toContainText(value.conversation.title);
  expect(eventReads).toBe(1);
});
