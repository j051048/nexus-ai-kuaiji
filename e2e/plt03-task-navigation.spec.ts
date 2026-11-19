import { expect, test } from '@playwright/test';
import { choosePersona } from './ui-navigation';

test('手机端从复核待办返回后恢复原滚动位置', async ({ page }) => {
  test.setTimeout(60_000);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/workspace/home');
  await choosePersona(page, 'reviewer');
  await page.getByLabel('筛选待办类型').selectOption('review');
  const rows = page.locator('[data-task-key^="review:"]');
  await expect(rows.first()).toBeVisible({ timeout: 15_000 });
  const originalCount = await rows.count();
  expect(originalCount).toBeGreaterThan(0);
  const last = rows.last();
  await last.scrollIntoViewIfNeeded();
  const before = await page.evaluate(() => window.scrollY);
  expect(before).toBeGreaterThan(400);
  await last.getByRole('button', { name: '同屏复核', exact: true }).click();
  const drawer = page.getByRole('dialog', { name: '原件与凭证，一起核对', exact: true });
  await expect(drawer).toBeVisible();
  await drawer.getByRole('button', { name: '返回列表', exact: true }).click();
  await expect(drawer).toHaveCount(0);
  await expect(rows).toHaveCount(originalCount);
  await expect.poll(() => page.evaluate(() => window.scrollY)).toBeGreaterThan(before - 100);
});

test('跨企业复核待办切换目标范围并在返回后保留筛选', async ({ page, request }) => {
  test.setTimeout(120_000);
  await page.setViewportSize({ width: 1366, height: 500 });
  const accountant = await (await request.get('/api/session?persona=accountant')).json();
  const reviewer = await (await request.get('/api/session?persona=reviewer')).json();
  const headers = { Authorization: `Bearer ${accountant.token}` },
    reviewerHeaders = { Authorization: `Bearer ${reviewer.token}` };
  const context = await (
    await request.get('/api/business/context', { headers: reviewerHeaders })
  ).json();
  const companyA = context.companies[0];
  const marker = crypto.randomUUID().slice(0, 8);
  const companyBResponse = await request.post('/api/companies', {
    headers,
    data: { name: `PLT03 跨企业目标 ${marker}`, tax_id: '' },
  });
  expect(companyBResponse.status()).toBe(201);
  const companyB = await companyBResponse.json();
  const created: { bookId: string; draftId: string; name: string; companyId: string }[] = [];

  for (const [company, label] of [
    [companyA, '当前企业'],
    [companyB, '跨企业目标'],
  ] as const) {
    const name = `PLT03 ${label} ${marker}`;
    const bookResponse = await request.post('/api/books', {
      headers,
      data: { name, company_id: company.id, period: '2026-10' },
    });
    expect(bookResponse.status(), await bookResponse.text()).toBe(201);
    const book = await bookResponse.json();
    expect(
      (
        await request.put(`/api/books/${book.id}/members/local-reviewer`, {
          headers,
          data: { role: 'reviewer' },
        })
      ).status(),
    ).toBe(200);
    expect(
      (
        await request.post(`/api/books/${book.id}/imports`, {
          headers,
          data: {
            source_name: 'PLT03 合成待办导航',
            rows: [
              {
                external_id: `plt03-${marker}-${label}`,
                date: '2026-10-01',
                description: `${name} · 办公用品`,
                counterparty: '虚构导航供方',
                amount: '1.07',
                direction: 'expense',
                evidence_ref: '合成票据，仅验证待办目标范围',
              },
            ],
          },
        })
      ).status(),
    ).toBe(200);
    expect((await request.post(`/api/books/${book.id}/suggestions`, { headers })).status()).toBe(
      200,
    );
    const detail = await (await request.get(`/api/books/${book.id}`, { headers })).json();
    created.push({ bookId: book.id, draftId: detail.drafts[0].id, name, companyId: company.id });
  }

  const tasksResponse = await request.get('/api/workspace/tasks?kind=review&bucket=all&limit=50', {
    headers: reviewerHeaders,
  });
  expect(tasksResponse.status()).toBe(200);
  const tasks = (await tasksResponse.json()).tasks as Array<Record<string, unknown>>;
  const target = created[1];
  const task = tasks.find((item) => item.kind === 'review' && item.ref === target.draftId);
  expect(task).toBeTruthy();
  expect(task).toMatchObject({
    company_id: companyB.id,
    period: '2026-10',
    object_type: 'review',
    object_id: target.draftId,
    available_action: 'can_act',
    business_action: '同屏复核',
  });

  await page.goto('/workspace/home');
  await choosePersona(page, 'reviewer');
  await page.getByLabel('筛选待办类型').selectOption('review');
  const row = page.locator(`[data-task-key="review:${target.draftId}"]`);
  await expect(row).toBeVisible({ timeout: 15_000 });
  await row.getByRole('button', { name: '同屏复核', exact: true }).click();
  const drawer = page.getByRole('dialog', { name: '原件与凭证，一起核对', exact: true });
  await expect(drawer).toBeVisible();
  await expect
    .poll(() => {
      const query = new URL(page.url()).searchParams;
      return [
        query.get('company'),
        query.get('period'),
        query.get('review_book'),
        query.get('review_draft'),
      ];
    })
    .toEqual([companyB.id, '2026-10', target.bookId, target.draftId]);
  await expect(drawer).toContainText(companyB.name);
  await expect(drawer).toContainText('2026-10');
  await expect(
    page.getByRole('button', { name: '问会计助手', exact: true }).locator('small'),
  ).toContainText(`${companyB.name} · 2026-10`);
  await drawer.getByRole('button', { name: '返回列表', exact: true }).click();
  await expect(drawer).toHaveCount(0);
  await expect(page.getByLabel('筛选待办类型')).toHaveValue('review');
  await expect(row).toBeVisible();

  // 旧列表条目：服务端对象版本已变化时停在原队列，不打开旧版本，也不清空列表。
  await page.route('**/api/workspace/tasks/review/**', async (route) => {
    const response = await route.fetch();
    const body = await response.json();
    await route.fulfill({ response, json: { ...body, object_version: 'stale-version' } });
  });
  await row.getByRole('button', { name: '同屏复核', exact: true }).click();
  const staleDialog = page.getByRole('dialog', { name: '待办内容已更新', exact: true });
  await expect(staleDialog).toBeVisible();
  await page.screenshot({ path: 'tmp_test/plt03c-stale-dialog.png' });
  await expect(staleDialog).toContainText('为避免在旧版本上办理');
  await expect(row).toBeVisible();
  await page.unroute('**/api/workspace/tasks/review/**');
  await staleDialog.getByRole('button', { name: '打开最新内容', exact: true }).click();
  await expect(drawer).toBeVisible();
  await drawer.getByRole('button', { name: '返回列表', exact: true }).click();

  await page.route('**/api/workspace/tasks/review/**', (route) =>
    route.fulfill({
      status: 403,
      contentType: 'application/json',
      body: JSON.stringify({ detail: '当前账号已无权访问该待办' }),
    }),
  );
  await expect(row).toBeVisible();
  await row.getByRole('button', { name: '同屏复核', exact: true }).click();
  const forbidden = page.getByRole('dialog', { name: '权限已撤销', exact: true });
  await expect(forbidden).toBeVisible();
  await page.screenshot({ path: 'tmp_test/plt03c-forbidden-dialog.png' });
  await expect(forbidden).toContainText('当前处理人');
  await forbidden.getByRole('button', { name: '返回队列', exact: true }).click();
  await expect(forbidden).toHaveCount(0);
  await page.unroute('**/api/workspace/tasks/review/**');

  await page.route('**/api/workspace/tasks/review/**', (route) =>
    route.fulfill({
      status: 410,
      contentType: 'application/json',
      body: JSON.stringify({
        detail:
          '该待办已由 示例复核会计 于 2026-10-18T09:30:00+00:00 办理，列表状态已更新，请返回队列查看。',
      }),
    }),
  );
  await expect(row).toBeVisible();
  await row.getByRole('button').first().click();
  const completed = page.getByRole('dialog', { name: '待办已办完', exact: true });
  await expect(completed).toBeVisible();
  await page.screenshot({ path: 'tmp_test/plt03c-completed-dialog.png' });
  await expect(completed).toContainText('示例复核会计');
  await expect(completed).toContainText('2026-10-18T09:30:00+00:00');
  await completed.getByRole('button', { name: '查看处理记录', exact: true }).click();
  await expect(completed).toHaveCount(0);
  await expect(page).toHaveURL(/\/workspace\/accounting\?/);
  await expect.poll(() => new URL(page.url()).searchParams.get('book')).toBeTruthy();
  const accountingUrl = new URL(page.url());
  expect(accountingUrl.searchParams.get('book')).toBe(target.bookId);
  expect(accountingUrl.searchParams.get('company')).toBe(companyB.id);
  await page.unroute('**/api/workspace/tasks/review/**');

  for (const item of created) {
    expect(
      (
        await request.delete(`/api/books/${item.bookId}/members/local-reviewer`, {
          headers,
        })
      ).status(),
    ).toBe(200);
  }
});

test('已办完的异常待办可查看关闭记录并定位原事项', async ({ page, request }) => {
  test.setTimeout(60_000);
  const session = await (await request.get('/api/session?persona=reviewer')).json();
  const headers = { Authorization: `Bearer ${session.token}` };
  const context = await (await request.get('/api/business/context', { headers })).json();
  const company = context.companies[0];
  const caseId = 'plt03-closed-case';
  const task = {
    key: `case:${caseId}`,
    kind: 'case',
    ref: caseId,
    company_id: company.id,
    company_name: company.name,
    book_id: null,
    event_id: null,
    draft_id: null,
    batch_id: null,
    title: '已关闭的异常事项',
    state: 'closed',
    priority: 1,
    due_on: null,
    updated_at: '2026-10-18T09:30:00+00:00',
    bucket: 'action',
    action_label: '查看',
    responsible: '示例复核会计',
    overdue: false,
    period: '2026-10',
    object_type: 'case',
    object_id: caseId,
    object_version: '2026-10-18T09:30:00+00:00',
    available_action: 'can_act',
    business_action: '查看',
    current_handler: '示例复核会计',
    alternate_actions: [],
    amount_impact: null,
  };
  await page.route('**/api/workspace/bootstrap**', async (route) => {
    const response = await route.fetch();
    const body = await response.json();
    await route.fulfill({
      response,
      json: {
        ...body,
        tasks: {
          tasks: [task],
          counts: { action: 1 },
          pagination: { total: 1, limit: 20, offset: 0, has_more: false },
        },
      },
    });
  });
  await page.route(`**/api/workspace/tasks/case/${caseId}`, (route) =>
    route.fulfill({
      status: 410,
      contentType: 'application/json',
      body: JSON.stringify({
        detail:
          '该待办已由 示例复核会计 于 2026-10-18T09:30:00+00:00 办理，列表状态已更新，请返回队列查看。',
      }),
    }),
  );

  await page.goto('/workspace/home');
  await choosePersona(page, 'reviewer');
  const row = page.locator(`[data-task-key="case:${caseId}"]`);
  await expect(row).toBeVisible({ timeout: 15_000 });
  await row.getByRole('button').first().click();
  const completed = page.getByRole('dialog', { name: '待办已办完', exact: true });
  await expect(completed).toBeVisible();
  await completed.getByRole('button', { name: '查看处理记录', exact: true }).click();
  await expect(completed).toHaveCount(0);
  await expect.poll(() => new URL(page.url()).pathname).toBe('/workspace/cases');
  await expect
    .poll(() => {
      const query = new URL(page.url()).searchParams;
      return [query.get('case'), query.get('company'), query.get('period')];
    })
    .toEqual([caseId, company.id, '2026-10']);
});

test('已办完的审批待办可查看处理记录并定位原事项', async ({ page, request }) => {
  test.setTimeout(60_000);
  const session = await (await request.get('/api/session?persona=reviewer')).json();
  const headers = { Authorization: `Bearer ${session.token}` };
  const context = await (await request.get('/api/business/context', { headers })).json();
  const company = context.companies[0];
  const eventId = 'plt03-completed-approval';
  const task = {
    key: `approval:${eventId}`,
    kind: 'approval',
    ref: eventId,
    company_id: company.id,
    company_name: company.name,
    book_id: null,
    event_id: eventId,
    draft_id: null,
    batch_id: null,
    title: '已办完的部门审批',
    state: 'done',
    priority: 1,
    due_on: null,
    updated_at: '2026-10-18T09:30:00+00:00',
    bucket: 'action',
    action_label: '审批事项',
    responsible: '示例部门负责人',
    overdue: false,
    period: '2026-10',
    object_type: 'approval',
    object_id: eventId,
    object_version: '2026-10-18T09:30:00+00:00',
    available_action: 'can_act',
    business_action: '审批事项',
    current_handler: '示例部门负责人',
    alternate_actions: [],
    amount_impact: null,
  };
  await page.route('**/api/workspace/bootstrap**', async (route) => {
    const response = await route.fetch();
    const body = await response.json();
    await route.fulfill({
      response,
      json: {
        ...body,
        tasks: {
          tasks: [task],
          counts: { action: 1 },
          pagination: { total: 1, limit: 20, offset: 0, has_more: false },
        },
      },
    });
  });
  await page.route(`**/api/workspace/tasks/approval/${eventId}`, (route) =>
    route.fulfill({
      status: 410,
      contentType: 'application/json',
      body: JSON.stringify({
        detail:
          '该待办已由 示例部门负责人 于 2026-10-18T09:30:00+00:00 办理，列表状态已更新，请返回队列查看。',
      }),
    }),
  );

  await page.goto('/workspace/home');
  await choosePersona(page, 'reviewer');
  const row = page.locator(`[data-task-key="approval:${eventId}"]`);
  await expect(row).toBeVisible({ timeout: 15_000 });
  await row.getByRole('button').first().click();
  const completed = page.getByRole('dialog', { name: '待办已办完', exact: true });
  await expect(completed).toBeVisible();
  await completed.getByRole('button', { name: '查看处理记录', exact: true }).click();
  await expect(completed).toHaveCount(0);
  await expect.poll(() => new URL(page.url()).pathname).toBe('/workspace/business');
  await expect
    .poll(() => {
      const query = new URL(page.url()).searchParams;
      return [query.get('event'), query.get('company'), query.get('period')];
    })
    .toEqual([eventId, company.id, '2026-10']);
});
