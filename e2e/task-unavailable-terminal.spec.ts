import { expect, test, type APIRequestContext, type Page } from '@playwright/test';
import { choosePersona } from './ui-navigation';

/**
 * 2C：待办失效不能变成死循环。
 *
 * 这里刻意走真实的「先陈旧、后办完」两步：
 * 1. 复核人先看到列表里的待办（object_version = 当时的 updated_at）；
 * 2. 会计修改草稿 → 复核人点开时服务端已更新 → 页面显示「待办内容已更新」；
 * 3. 会计办结 → 复核人点「打开最新内容」必须变成「待办已办完」并刷新队列，
 *    而不是再次提示「已更新」。
 */

type Setup = {
  marker: string;
  companyId: string;
  bookId: string;
  draftId: string;
  companyName: string;
};

async function session(request: APIRequestContext, persona: 'accountant' | 'reviewer') {
  const body = await (await request.get(`/api/session?persona=${persona}`)).json();
  return { Authorization: `Bearer ${body.token as string}` };
}

async function setupPendingReview(
  request: APIRequestContext,
  options: { amount?: string } = {},
): Promise<Setup> {
  const headers = await session(request, 'accountant');
  const marker = crypto.randomUUID().slice(0, 8);
  const companyName = `2C 待办终态 ${marker}`;
  const companyResponse = await request.post('/api/companies', {
    headers,
    data: { name: companyName, tax_id: '' },
  });
  expect(companyResponse.status(), await companyResponse.text()).toBe(201);
  const company = await companyResponse.json();
  const bookResponse = await request.post('/api/books', {
    headers,
    data: { name: `2C 合成账套 ${marker}`, company_id: company.id, period: '2026-10' },
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
  const importResponse = await request.post(`/api/books/${book.id}/imports`, {
    headers,
    data: {
      source_name: `2C 合成票据 ${marker}`,
      rows: [
        {
          external_id: `2c-${marker}`,
          date: '2026-10-02',
          description: `${companyName} · 办公用品`,
          counterparty: '虚构办公用品供方',
          amount: options.amount ?? '1.07',
          direction: 'expense',
          evidence_ref: '合成票据，仅验证待办失效',
        },
      ],
    },
  });
  expect([200, 201]).toContain(importResponse.status());
  expect((await request.post(`/api/books/${book.id}/suggestions`, { headers })).status()).toBe(200);
  const detailResponse = await request.get(`/api/books/${book.id}`, { headers });
  expect(detailResponse.status(), await detailResponse.text()).toBe(200);
  const detail = await detailResponse.json();
  const draft = (detail.drafts ?? [])[0] as Record<string, unknown> | undefined;
  expect(draft?.id).toBeTruthy();
  return { marker, companyId: company.id, bookId: book.id, draftId: String(draft.id), companyName };
}

async function readDraft(request: APIRequestContext, setup: Setup) {
  const headers = await session(request, 'accountant');
  const response = await request.get(`/api/books/${setup.bookId}`, { headers });
  expect(response.status()).toBe(200);
  const body = await response.json();
  const rows = (body.drafts ?? []) as Record<string, unknown>[];
  const draft = rows.find((row) => row.id === setup.draftId);
  expect(draft, '草稿应当仍在列表里').toBeTruthy();
  return { headers, draft: draft as Record<string, unknown> };
}

/** 会计修改草稿：服务端版本与 updated_at 变化，但复核待办仍然存在。 */
async function reviseDraft(request: APIRequestContext, setup: Setup) {
  const { headers, draft } = await readDraft(request, setup);
  const lines = (draft.lines ?? []) as Record<string, unknown>[];
  const response = await request.patch(`/api/books/${setup.bookId}/drafts/${setup.draftId}`, {
    headers,
    data: {
      expected_version: draft.version,
      description: `${String(draft.description)} · 补充说明`,
      lines: lines.map((line) => ({
        account_code: String(line.account_code),
        debit: String(line.debit),
        credit: String(line.credit),
        auxiliary_values: (line.auxiliary_values ?? {}) as Record<string, string>,
      })),
      correction_reason: '核对往来单位后补充说明',
    },
  });
  expect(response.status(), await response.text()).toBe(200);
}

/** 办结：此后该待办从列表消失，只能由 410 说明原因。 */
async function approveDraft(request: APIRequestContext, setup: Setup) {
  // 制单人不能独立复核本人版本：这里用复核会计身份（相当于另一位同事在另一处办结）。
  const headers = await session(request, 'reviewer');
  const detail = await (await request.get(`/api/books/${setup.bookId}`, { headers })).json();
  const rows = (detail.drafts ?? []) as Record<string, unknown>[];
  const draft = rows.find((row) => row.id === setup.draftId);
  expect(draft).toBeTruthy();
  const version = (draft as Record<string, unknown>).version;
  const response = await request.post(
    `/api/books/${setup.bookId}/drafts/${setup.draftId}/approve`,
    { headers, data: { expected_version: version, review_reason: '合成回归：核对无误' } },
  );
  expect(response.status(), await response.text()).toBe(200);
}

async function openReviewBookmark(page: Page, setup: Setup) {
  await page.goto('/workspace/home');
  await choosePersona(page, 'reviewer');
  await page.getByLabel('筛选待办类型').selectOption('review');
  const row = page.locator('[data-task-key^="review:"]').filter({ hasText: setup.companyName });
  await expect(row).toHaveCount(1, { timeout: 20_000 });
  return row;
}

async function noHorizontalOverflow(page: Page) {
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1),
  ).toBe(true);
}

async function expectStaleThenCompleted(page: Page, request: APIRequestContext, setup: Setup) {
  const row = await openReviewBookmark(page, setup);

  await reviseDraft(request, setup);
  // 队列会自行刷新版本号，这里固定把这一次详情响应标注成「比列表旧」，稳定复现陈旧分支。
  const detailPattern = '**/api/workspace/tasks/review/**';
  await page.route(detailPattern, async (route) => {
    const response = await route.fetch();
    const body = await response.json();
    await route.fulfill({ response, json: { ...body, object_version: 'older-listing' } });
  });
  await row.getByRole('button', { name: '同屏复核', exact: true }).click();
  const stale = page.getByRole('dialog', { name: '待办内容已更新', exact: true });
  await expect(stale).toBeVisible({ timeout: 20_000 });
  await noHorizontalOverflow(page);
  await page.unroute(detailPattern);

  await approveDraft(request, setup);
  await stale.getByRole('button', { name: '打开最新内容', exact: true }).click();

  const completed = page.getByRole('dialog', { name: '待办已办完', exact: true });
  await expect(completed).toBeVisible({ timeout: 20_000 });
  await expect(completed).toContainText('已由');
  // 终态说明必须来自服务端：谁、什么时候办完，而不是一句「请重试」。
  await expect(completed).toContainText('列表状态已更新');
  await expect(page.getByRole('button', { name: '打开最新内容', exact: true })).toHaveCount(0);
  await noHorizontalOverflow(page);

  await expect(completed.getByRole('button', { name: '查看处理记录', exact: true })).toBeVisible();
  await completed.getByRole('button', { name: '返回队列', exact: true }).click();
  await expect(
    page.locator('[data-task-key^="review:"]').filter({ hasText: setup.companyName }),
  ).toHaveCount(0, { timeout: 20_000 });
  await expect(page.getByLabel('筛选待办类型')).toHaveValue('review');
}

test('复核待办先陈旧后办完：打开最新内容给出终态并刷新队列', async ({ page, request }) => {
  test.setTimeout(120_000);
  await page.setViewportSize({ width: 1366, height: 900 });
  const setup = await setupPendingReview(request);
  await expectStaleThenCompleted(page, request, setup);
});

test('390 视口下同一流程的两个提示都不横向溢出', async ({ page, request }) => {
  test.setTimeout(120_000);
  await page.setViewportSize({ width: 390, height: 844 });
  const setup = await setupPendingReview(request, { amount: '1.08' });
  await expectStaleThenCompleted(page, request, setup);
});
