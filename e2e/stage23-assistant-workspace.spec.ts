import { expect, test, type APIRequestContext, type Page } from '@playwright/test';
import { choosePersona } from './ui-navigation';

test.setTimeout(60_000);

// Keep the rolling-upgrade/slow full-read contract. Stage25 separately checks
// the new progress cursor; an older API has only the workspace endpoint.
test.beforeEach(async ({ page }) => {
  await page.route(/\/api\/assistant\/conversations\/[^/]+\/progress\?/, async (route) => {
    await route.fulfill({ status: 404, json: { detail: 'Not Found' } });
  });
});

function deferred() {
  let resolve!: () => void;
  const promise = new Promise<void>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

async function fixture(request: APIRequestContext, label: string) {
  const session = await (await request.get('/api/session?persona=accountant')).json();
  const headers = { Authorization: `Bearer ${session.token}` };
  const tag = `${label}-${crypto.randomUUID().slice(0, 8)}`;
  const response = await request.post('/api/books', {
    headers,
    data: { name: tag, period: '2026-10' },
  });
  expect(response.status()).toBe(201);
  const book = await response.json();
  const created = await request.post('/api/assistant/conversations', {
    headers,
    data: {
      company_id: book.company_id,
      book_ids: [book.id],
      event_ids: [],
      title: tag,
      idempotency_key: crypto.randomUUID(),
    },
  });
  expect(created.status()).toBe(201);
  const conversation = (await created.json()).conversation;
  const path = `/api/assistant/conversations/${conversation.id}`;
  const result = await request.get(`${path}/workspace`, { headers });
  expect(result.status()).toBe(200);
  return { book, conversation, path, workspace: await result.json() };
}

async function openDrawer(page: Page, company: string) {
  await page.goto('/');
  await choosePersona(page, 'accountant');
  await page.getByRole('button', { name: '问会计助手', exact: true }).click();
  const drawer = page.getByRole('dialog', { name: '会计助手', exact: true });
  await drawer.getByLabel('助手所属企业', { exact: true }).selectOption(company);
  return drawer;
}

async function openHistory(page: Page, title: string) {
  const drawer = page.getByRole('dialog', { name: '会计助手', exact: true });
  await drawer.getByRole('button', { name: '查看我的会话', exact: true }).click();
  await drawer.locator('.ga-history-row').filter({ hasText: title }).click();
}

async function controlledReads(page: Page, path: string, snapshot: () => unknown) {
  let count = 0;
  const holds = new Map<
    number,
    { arrived: ReturnType<typeof deferred>; released: ReturnType<typeof deferred> }
  >();
  await page.route(new RegExp(`${path}/workspace\\?`), async (route) => {
    const body = JSON.stringify(snapshot()),
      hold = holds.get(++count);
    if (hold) {
      hold.arrived.resolve();
      await hold.released.promise;
    }
    await route.fulfill({ status: 200, contentType: 'application/json', body }).catch(() => {});
  });
  return {
    count: () => count,
    holdNext() {
      const hold = { arrived: deferred(), released: deferred() };
      holds.set(count + 1, hold);
      return { arrived: hold.arrived.promise, release: hold.released.resolve };
    },
  };
}

test('真实聚合接口一次读取会话，隐藏历史不预取，打开历史只读取一次', async ({ page, request }) => {
  const value = await fixture(request, '聚合真实读取');
  let history = 0,
    workspace = 0,
    legacy = 0;
  page.on('request', (request) => {
    const path = new URL(request.url()).pathname;
    if (path === '/api/assistant/conversations') history++;
    if (path === `${value.path}/workspace`) workspace++;
    if ([value.path, `${value.path}/messages`, `${value.path}/actions`].includes(path)) legacy++;
  });
  const drawer = await openDrawer(page, value.book.company_id);
  await expect(drawer.getByRole('button', { name: /创建会话 ·/ })).toBeVisible();
  expect(history).toBe(0);
  await openHistory(page, value.conversation.title);
  await expect(drawer.locator('.ga-context')).toContainText(value.conversation.title);
  expect(history).toBe(1);
  expect(workspace).toBe(1);
  expect(legacy).toBe(0);
});

test('聚合慢读取跨轮询与focus仍只进行一次，完成后恢复轮询', async ({ page, request }) => {
  const value = await fixture(request, '聚合慢读取');
  const reads = await controlledReads(page, value.path, () => value.workspace);
  const held = reads.holdNext();
  const drawer = await openDrawer(page, value.book.company_id);
  await page.clock.install();
  await openHistory(page, value.conversation.title);
  await held.arrived;
  await page.clock.runFor(31_000);
  await page.evaluate(() => window.dispatchEvent(new Event('focus')));
  expect(reads.count()).toBe(1);
  held.release();
  await expect(drawer.locator('.ga-context')).toContainText(value.conversation.title);
  await page.clock.runFor(15_001);
  await expect.poll(reads.count).toBe(2);
});

test('切换企业丢弃迟到聚合响应，关闭后不继续轮询', async ({ page, request }) => {
  const a = await fixture(request, '聚合企业A'),
    b = await fixture(request, '聚合企业B');
  const reads = await controlledReads(page, a.path, () => a.workspace);
  const held = reads.holdNext();
  const drawer = await openDrawer(page, a.book.company_id);
  await page.clock.install();
  await openHistory(page, a.conversation.title);
  await held.arrived;
  await drawer.getByLabel('助手所属企业', { exact: true }).selectOption(b.book.company_id);
  await openHistory(page, b.conversation.title);
  await expect(drawer.locator('.ga-context')).toContainText(b.conversation.title);
  held.release();
  await expect(drawer.locator('.ga-context')).not.toContainText(a.conversation.title);
  const beforeClose = reads.count();
  await drawer.getByRole('button', { name: '关闭全局会计助手', exact: true }).click();
  await page.clock.runFor(45_000);
  await expect(drawer).toHaveCount(0);
  expect(reads.count()).toBe(beforeClose);
});

for (const status of [403, 404]) {
  test(`聚合${status}拒绝不回退旧接口，清除敏感资料并停读`, async ({ page, request }) => {
    const value = await fixture(request, `聚合拒绝${status}`);
    let denied = false,
      count = 0,
      legacy = 0;
    page.on('request', (request) => {
      const path = new URL(request.url()).pathname;
      if ([value.path, `${value.path}/messages`, `${value.path}/actions`].includes(path)) legacy++;
    });
    await page.route(new RegExp(`${value.path}/workspace\\?`), async (route) => {
      count++;
      await route.fulfill(
        denied
          ? { status, json: { detail: '当前会话不存在或无权查看' } }
          : { json: value.workspace },
      );
    });
    const drawer = await openDrawer(page, value.book.company_id);
    await page.clock.install();
    await openHistory(page, value.conversation.title);
    await expect(drawer.locator('.ga-context')).toContainText(value.conversation.title);
    denied = true;
    await page.clock.runFor(15_001);
    await expect(drawer.locator('.ga-context')).toHaveCount(0);
    const after = count;
    await page.clock.runFor(45_000);
    expect(count).toBe(after);
    expect(legacy).toBe(0);
  });
}

test('聚合消息分页读取新offset并丢弃旧页迟到响应', async ({ page, request }) => {
  const value = await fixture(request, '聚合分页');
  const offsets: string[] = [];
  let count = 0;
  const arrived = deferred(),
    released = deferred();
  await page.route(new RegExp(`${value.path}/workspace\\?`), async (route) => {
    const offset = new URL(route.request().url()).searchParams.get('message_offset') ?? '0';
    offsets.push(offset);
    const body = {
      ...value.workspace,
      messages: {
        messages: [
          {
            id: `message-${offset}`,
            turn_id: `turn-${offset}`,
            role: 'user',
            content: { question: `聚合消息页${offset}` },
            created_at: '2026-10-15T00:00:00Z',
          },
        ],
        pagination: { total: 101, limit: 100, offset: Number(offset), has_more: offset === '0' },
      },
    };
    if (++count === 2) {
      arrived.resolve();
      await released.promise;
    }
    await route.fulfill({ json: body }).catch(() => {});
  });
  const drawer = await openDrawer(page, value.book.company_id);
  await page.clock.install();
  await openHistory(page, value.conversation.title);
  await expect(drawer.locator('.ga-message')).toContainText('聚合消息页0');
  await page.clock.runFor(15_001);
  await arrived.promise;
  await drawer.getByRole('button', { name: '查看更早的 100 条记录', exact: true }).click();
  await expect(drawer.locator('.ga-message')).toContainText('聚合消息页100');
  released.resolve();
  await expect(drawer.locator('.ga-message')).not.toContainText('聚合消息页0');
  expect(offsets).toEqual(['0', '0', '100']);
});
