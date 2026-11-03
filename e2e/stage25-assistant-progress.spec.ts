import { expect, test, type APIRequestContext, type Page } from '@playwright/test';
import { choosePersona } from './ui-navigation';

test.setTimeout(60_000);

async function fixture(request: APIRequestContext) {
  const session = await (await request.get('/api/session?persona=accountant')).json();
  const headers = { Authorization: `Bearer ${session.token}` };
  const created = await request.post('/api/books', {
    headers,
    data: {
      name: `轻量进度-${crypto.randomUUID().slice(0, 8)}`,
      period: '2026-10',
    },
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
          idempotency_key: crypto.randomUUID(),
          title: `轻量会话-${crypto.randomUUID().slice(0, 8)}`,
        },
      })
    ).json()
  ).conversation;
  const path = `/api/assistant/conversations/${conversation.id}`;
  return {
    headers,
    book,
    conversation,
    path,
    workspace: await (await request.get(`${path}/workspace`, { headers })).json(),
  };
}

test('全所效率按月显示实际样本，暂无计时保持未知，账套明细可下钻且适应手机', async ({
  page,
  request,
}) => {
  const measured = await fixture(request),
    unmeasured = await fixture(request);
  const started = await request.post('/api/efficiency/sessions', {
    headers: measured.headers,
    data: {
      book_id: measured.book.id,
      activity: 'review',
      idempotency_key: crypto.randomUUID(),
    },
  });
  expect(started.status()).toBe(200);
  await page.goto('/');
  await choosePersona(page, 'accountant');
  await page.goto('/workspace/portfolio');
  await expect(page.getByRole('heading', { name: '看清每家企业的人工负担' })).toBeVisible({
    timeout: 15000,
  });
  await page.getByLabel('效率统计月份').fill('2026-10');
  // Other cases create independent companies in this run. Exercise the real
  // pagination instead of assuming our two new books are on the first page.
  async function findBookRow(name: string) {
    await expect(page.getByRole('table')).toBeVisible();
    while (await page.getByRole('button', { name: '上一页', exact: true }).isEnabled()) {
      await page.getByRole('button', { name: '上一页', exact: true }).click();
      await expect(page.getByRole('table')).toBeVisible();
    }
    for (let count = 0; count < 30; count++) {
      const row = page.getByRole('row').filter({ hasText: name });
      if (await row.count()) return row;
      const next = page.getByRole('button', { name: '下一页', exact: true });
      await expect(next).toBeEnabled();
      await next.click();
      await expect(page.getByRole('table')).toBeVisible();
    }
    throw new Error(`Expected authorized book missing: ${name}`);
  }
  await expect(await findBookRow(unmeasured.book.name)).toContainText('暂无计时样本');
  const measuredRow = await findBookRow(measured.book.name);
  await expect(measuredRow).toContainText('1 次');
  await measuredRow.locator('summary').click();
  await expect(
    measuredRow.getByRole('button', { name: `查看${measured.book.name}的计时` }),
  ).toBeVisible();
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({ path: 'artifacts/stage25-efficiency-mobile.png', fullPage: true });
  await page.setViewportSize({ width: 1440, height: 1000 });
  await measuredRow.getByRole('button', { name: `查看${measured.book.name}的计时` }).click();
  await expect(page.getByRole('heading', { name: '用真实处理时间，衡量自动化。' })).toBeVisible();
});
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

test('活跃任务每秒只查轻量游标，未变化不重复整页，终态变化马上读完整证据', async ({
  page,
  request,
}) => {
  const value = await fixture(request);
  let reads = 0,
    polls = 0,
    revision = 'first';
  value.workspace.detail.turns = [
    {
      id: 'synthetic-turn',
      status: 'queued',
      question: '虚构问题',
      created_at: '2026-10-16T00:00:00Z',
    },
  ];
  await page.route(new RegExp(`${value.path}/workspace\\?`), async (route) => {
    reads++;
    await route.fulfill({ json: value.workspace });
  });
  await page.route(new RegExp(`${value.path}/progress\\?`), async (route) => {
    polls++;
    await route.fulfill({
      json: {
        conversation_id: value.conversation.id,
        company_id: value.book.company_id,
        scope_version: 1,
        revision,
        active: true,
      },
    });
  });
  await page.clock.install();
  await open(page, value);
  expect(reads).toBe(1);
  await page.clock.runFor(1100);
  await expect.poll(() => reads).toBe(2);
  for (let index = 0; index < 4; index++) {
    const before = polls;
    await page.clock.runFor(1100);
    await expect.poll(() => polls).toBeGreaterThan(before);
  }
  expect(reads).toBe(2);
  revision = 'completed';
  value.workspace.detail.turns[0].status = 'succeeded';
  await page.clock.runFor(1100);
  await expect.poll(() => reads).toBe(3);
});

for (const status of [403, 404]) {
  test(`轻量进度${status}授权拒绝立即清空资料且不回退旧接口`, async ({ page, request }) => {
    const value = await fixture(request);
    let reads = 0,
      polls = 0;
    await page.route(new RegExp(`${value.path}/workspace\\?`), async (route) => {
      reads++;
      await route.fulfill({ json: value.workspace });
    });
    await page.route(new RegExp(`${value.path}/progress\\?`), async (route) => {
      polls++;
      await route.fulfill({ status, json: { detail: '对话不存在或当前范围已失去授权' } });
    });
    await page.clock.install();
    const drawer = await open(page, value);
    await page.clock.runFor(15_100);
    await expect(drawer.locator('.ga-context')).toHaveCount(0);
    const after = polls;
    await page.clock.runFor(45_000);
    expect(polls).toBe(after);
    expect(reads).toBe(1);
  });
}
