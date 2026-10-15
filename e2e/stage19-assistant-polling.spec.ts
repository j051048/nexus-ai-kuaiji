import { expect, test, type APIRequestContext, type Page } from '@playwright/test';
import { choosePersona } from './ui-navigation';

test.setTimeout(60_000);

// Exercise rolling upgrades against an older API; new workspace reads have their own tests.
test.beforeEach(async ({ page }) => {
  await page.route(/\/api\/assistant\/conversations\/[^/]+\/workspace\?/, async (route) => {
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
  const detail = await (await request.get(path, { headers })).json();
  return { headers, book, conversation, detail, path };
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

// Capture each response before holding it, so a released old request really is stale.
async function controlledReads(page: Page, path: string, snapshot: () => unknown) {
  let count = 0;
  const holds = new Map<
    number,
    { arrived: ReturnType<typeof deferred>; released: ReturnType<typeof deferred> }
  >();
  await page.route(new RegExp(`${path}$`), async (route) => {
    const number = ++count;
    const body = JSON.stringify(snapshot());
    const hold = holds.get(number);
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

test('慢于两次轮询的会话读取仍能展示，focus不重叠，完成后继续刷新', async ({ page, request }) => {
  const value = await fixture(request, '合成慢会话');
  const reads = await controlledReads(page, value.path, () => value.detail);
  const held = reads.holdNext();
  let writes = 0;
  page.on('request', (request) => {
    if (request.url().includes('/api/assistant/') && request.method() !== 'GET') writes++;
  });
  const drawer = await openDrawer(page, value.book.company_id);
  await page.clock.install();
  await openHistory(page, value.conversation.title);
  await held.arrived;
  await page.clock.runFor(31_000);
  await page.evaluate(() => {
    window.dispatchEvent(new Event('focus'));
    window.dispatchEvent(new Event('focus'));
  });
  expect(reads.count()).toBe(1);
  held.release();
  await expect(drawer.locator('.ga-context')).toContainText(value.conversation.title);
  await page.clock.runFor(15_001);
  await expect.poll(reads.count).toBe(2);
  expect(writes).toBe(0);
});

test('切换企业及同一会话重开时不合并旧读取，迟到响应不能回灌', async ({ page, request }) => {
  const a = await fixture(request, '合成企业A'),
    b = await fixture(request, '合成企业B');
  const reads = await controlledReads(page, a.path, () => a.detail);
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
  await drawer.getByLabel('助手所属企业', { exact: true }).selectOption(a.book.company_id);
  await openHistory(page, a.conversation.title);
  await expect(drawer.locator('.ga-context')).toContainText(a.conversation.title);
  const old = reads.holdNext();
  await page.clock.runFor(15_001);
  await old.arrived;
  const before = reads.count();
  await openHistory(page, a.conversation.title);
  await expect.poll(reads.count).toBe(before + 1);
  await expect(drawer.locator('.ga-context')).toContainText(a.conversation.title);
  old.release();
  await expect(drawer.locator('.ga-context')).toContainText(a.conversation.title);
});

test('消息翻页不复用正在读取的旧页', async ({ page, request }) => {
  const value = await fixture(request, '合成分页');
  const reads = await controlledReads(page, value.path, () => value.detail);
  const offsets: string[] = [];
  await page.route(new RegExp(`${value.path}/messages\\?`), async (route) => {
    const offset = new URL(route.request().url()).searchParams.get('offset') ?? '0';
    offsets.push(offset);
    await route.fulfill({
      json: {
        messages: [
          {
            id: `message-${offset}`,
            turn_id: `turn-${offset}`,
            role: 'user',
            content: { question: `合成消息页${offset}` },
            created_at: '2026-10-15T00:00:00Z',
          },
        ],
        pagination: { total: 101, limit: 100, offset: Number(offset), has_more: offset === '0' },
      },
    });
  });
  const drawer = await openDrawer(page, value.book.company_id);
  await page.clock.install();
  await openHistory(page, value.conversation.title);
  await expect(drawer.locator('.ga-message')).toContainText('合成消息页0');
  const held = reads.holdNext();
  await page.clock.runFor(15_001);
  await held.arrived;
  const before = reads.count();
  await drawer.getByRole('button', { name: '查看更早的 100 条记录', exact: true }).click();
  await expect.poll(reads.count).toBe(before + 1);
  await expect(drawer.locator('.ga-message')).toContainText('合成消息页100');
  held.release();
  await expect(drawer.locator('.ga-message')).not.toContainText('合成消息页0');
  expect(offsets).toContain('100');
});

test('发送和取消成功后强制刷新，写前慢响应不能覆盖新任务状态', async ({ page, request }) => {
  const value = await fixture(request, '合成写后刷新');
  const detail = { ...value.detail, model_available: true, turns: [] as object[] };
  const reads = await controlledReads(page, value.path, () => detail);
  let sent = 0,
    cancelled = 0;
  const turn = {
    id: 'synthetic-ui-turn',
    conversation_id: value.conversation.id,
    status: 'queued',
    question: '合成客户端测试，不发送供应商',
    created_at: '2026-10-15T00:00:00Z',
  };
  await page.route(new RegExp(`${value.path}/turns$`), async (route) => {
    sent++;
    detail.turns = [turn];
    await route.fulfill({ status: 202, json: { turn } });
  });
  await page.route(new RegExp(`${value.path}/turns/${turn.id}/cancel$`), async (route) => {
    cancelled++;
    detail.turns = [{ ...turn, status: 'cancelled' }];
    await route.fulfill({ json: {} });
  });
  const drawer = await openDrawer(page, value.book.company_id);
  await page.clock.install();
  await openHistory(page, value.conversation.title);
  await expect(drawer.locator('.ga-context')).toBeVisible();
  const old = reads.holdNext();
  await page.clock.runFor(15_001);
  await old.arrived;
  const before = reads.count();
  await drawer.locator('textarea').fill(turn.question);
  await drawer.getByRole('button', { name: '发送会计助手问题', exact: true }).click();
  await expect.poll(reads.count).toBe(before + 1);
  await expect(drawer.locator('.ga-turn-state')).toContainText('等待模型处理');
  old.release();
  await expect(drawer.locator('.ga-turn-state')).toContainText('等待模型处理');
  const oldQueued = reads.holdNext();
  await page.clock.runFor(3_001);
  await oldQueued.arrived;
  const beforeCancel = reads.count();
  await drawer.getByRole('button', { name: '取消', exact: true }).click();
  await expect.poll(reads.count).toBe(beforeCancel + 1);
  await expect(drawer.locator('.ga-turn-state')).toContainText('已取消');
  oldQueued.release();
  await expect(drawer.locator('.ga-turn-state')).toContainText('已取消');
  expect(sent).toBe(1);
  expect(cancelled).toBe(1);
});

test('撤权清除资料并停止轮询，迟到成功不能恢复会话', async ({ page, request }) => {
  const value = await fixture(request, '合成撤权');
  const reads = await controlledReads(page, value.path, () => value.detail);
  let deny = false;
  await page.route(new RegExp(`${value.path}/messages\\?`), async (route) => {
    if (deny) await route.fulfill({ status: 403, json: { detail: '合成撤权测试' } });
    else await route.continue();
  });
  const drawer = await openDrawer(page, value.book.company_id);
  await page.clock.install();
  await openHistory(page, value.conversation.title);
  await expect(drawer.locator('.ga-context')).toBeVisible();
  const old = reads.holdNext();
  deny = true;
  await page.clock.runFor(15_001);
  await old.arrived;
  // The app closes sensitive surfaces immediately on the global 403 event.
  await expect(drawer).toHaveCount(0);
  const before = reads.count();
  old.release();
  await page.clock.runFor(45_000);
  expect(reads.count()).toBe(before);
  await expect(drawer).toHaveCount(0);
});

test('旧任务第二页创建预览回第一页，确认后不会被写前快照覆盖', async ({ page, request }) => {
  const value = await fixture(request, '合成任务分页');
  const reads = await controlledReads(page, value.path, () => value.detail);
  let created = 0,
    confirmed = 0;
  const offsetsAfterCreate: number[] = [];
  let plan: Record<string, unknown> = {
    id: 'synthetic-preview',
    kind: 'bank_drafts',
    state: 'ready',
    fingerprint: 'a'.repeat(64),
    expires_at: '2030-01-01T00:00:00Z',
    scope: { company_id: value.book.company_id, book_ids: [value.book.id], event_ids: [] },
    count: 1,
    items: [{ title: '合成新预览', book_id: value.book.id }],
    blocked: [],
    reason: '合成新预览',
    requires_confirmation: true,
  };
  await page.route(new RegExp(`${value.path}/actions(?:\\?|$)`), async (route) => {
    if (route.request().method() === 'POST') {
      created++;
      await route.fulfill({ json: plan });
      return;
    }
    const offset = Number(new URL(route.request().url()).searchParams.get('offset') ?? 0);
    if (created) offsetsAfterCreate.push(offset);
    await route.fulfill({
      json: {
        plans: created && offset === 0 ? [plan] : [],
        pagination: { total: 21, limit: 20, offset, has_more: offset === 0 },
      },
    });
  });
  await page.route(
    new RegExp(`${value.path}/actions/synthetic-preview/confirm$`),
    async (route) => {
      confirmed++;
      const receipt = {
        id: 'synthetic-receipt',
        plan_id: plan.id,
        kind: 'bank_drafts',
        count: 1,
        jobs: [],
        draft_ids: ['synthetic-draft'],
        created: 1,
        exceptions: 0,
        created_at: '2026-10-15T00:00:00Z',
      };
      plan = { ...plan, state: 'executed', receipt };
      await route.fulfill({ json: receipt });
    },
  );
  const drawer = await openDrawer(page, value.book.company_id);
  await page.clock.install();
  await openHistory(page, value.conversation.title);
  await drawer.getByRole('button', { name: '更早任务', exact: true }).click();
  await expect(drawer.locator('.ga-pagination')).toContainText('21–21');
  await drawer.getByText('直接准备后台任务', { exact: false }).click();
  await drawer.getByRole('button', { name: '生成流水草稿', exact: true }).click();
  await expect(drawer.locator('.ga-action-plan')).toContainText('合成新预览');
  await expect(drawer.locator('.ga-pagination')).toContainText('1–20');
  await expect.poll(() => offsetsAfterCreate.length).toBeGreaterThan(0);
  expect(offsetsAfterCreate.every((offset) => offset === 0)).toBe(true);
  const old = reads.holdNext();
  await page.clock.runFor(15_001);
  await old.arrived;
  await drawer.getByRole('button', { name: '确认执行这 1 项', exact: true }).click();
  await expect(drawer.locator('.ga-receipt')).toContainText('1 份草稿已生成');
  old.release();
  await expect(drawer.locator('.ga-receipt')).toContainText('1 份草稿已生成');
  expect(created).toBe(1);
  expect(confirmed).toBe(1);
});

test('关闭助手取消慢读取且停止后台轮询', async ({ page, request }) => {
  const value = await fixture(request, '合成关闭');
  const reads = await controlledReads(page, value.path, () => value.detail);
  const held = reads.holdNext();
  const drawer = await openDrawer(page, value.book.company_id);
  await page.clock.install();
  await openHistory(page, value.conversation.title);
  await held.arrived;
  await drawer.getByRole('button', { name: '关闭全局会计助手', exact: true }).click();
  held.release();
  await page.clock.runFor(45_000);
  await expect(drawer).toHaveCount(0);
  expect(reads.count()).toBe(1);
});
