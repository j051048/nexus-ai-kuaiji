import { independentReviewer, pinReviewer } from './independent-review';
import { expect, test, type APIRequestContext, type Page } from '@playwright/test';
import { openNavigation } from './ui-navigation';

test.setTimeout(90_000);

async function ownerHeaders(request: APIRequestContext) {
  const response = await request.get('/api/session?persona=accountant');
  expect(response.ok()).toBe(true);
  const session = await response.json();
  return { Authorization: `Bearer ${session.token}` };
}

async function openFirmHome(page: Page) {
  await page.goto('/workspace/home');
  await expect(page.getByLabel('切换验证身份')).toHaveValue('accountant');
  await expect(page.getByLabel('我的工作概览')).toBeVisible();
}

for (const failure of ['503', 'offline'] as const) {
  test(`R5 待办${failure}显示可行动错误，恢复后刷新且不误登出或显示空结果`, async ({ page }) => {
    await openFirmHome(page);
    let fail = true;
    let failedRequests = 0;
    await page.route('**/api/workspace/tasks?**', async (route) => {
      if (!fail) return route.continue();
      failedRequests++;
      if (failure === 'offline') return route.abort('internetdisconnected');
      return route.fulfill({ status: 503, json: { detail: 'R5 合成待办服务暂不可用' } });
    });
    await page.getByRole('button', { name: '刷新工作台', exact: true }).click();
    const error = page.locator('.workspace-load-error');
    await expect(error).toBeVisible();
    await expect(error).not.toHaveText('');
    if (failure === '503') {
      await expect(error).not.toContainText('R5 合成待办服务暂不可用');
      await expect(error).toContainText('暂时');
    }
    expect(failedRequests).toBe(1);
    await expect(page.locator('.auth-shell')).toHaveCount(0);
    await expect(page.getByText('当前筛选下没有待处理事项', { exact: true })).toHaveCount(0);
    await expect(page.getByRole('button', { name: '刷新工作台', exact: true })).toBeEnabled();
    fail = false;
    await page.getByRole('button', { name: '刷新工作台', exact: true }).click();
    await expect(error).toHaveCount(0);
    await expect(page.getByLabel('我的工作概览')).toBeVisible();
    await expect(page).toHaveURL(/\/workspace\/home/);
  });
}

test('R5 手机409保留输入、关联错误，关闭弹窗返回原操作焦点', async ({ page }) => {
  await openFirmHome(page);
  await page.setViewportSize({ width: 375, height: 812 });
  await openNavigation(page);
  await page
    .getByRole('navigation', { name: '主导航', exact: true })
    .getByRole('button', { name: '月度交付', exact: true })
    .click();
  const opener = page.getByRole('button', { name: '批量开期', exact: true });
  await opener.click();
  const dialog = page.getByRole('dialog', { name: '月初批量开期', exact: true });
  await expect(dialog).toBeVisible();
  await expect
    .poll(() => dialog.evaluate((node) => node.contains(document.activeElement)))
    .toBe(true);
  const reason = dialog.getByLabel('批量开期操作依据', { exact: true });
  await reason.fill('R5 手机合成操作依据');
  let attempts = 0;
  await page.route('**/api/firm/batch-operations/monthly-open', (route) => {
    attempts++;
    return route.fulfill({
      status: 409,
      json: { detail: 'R5 合成账期已经变化，请刷新后重新核对' },
    });
  });
  await page.route('**/api/firm/batch-operations/monthly-open/preview', (route) =>
    route.fulfill({
      status: 200,
      json: {
        period: '2026-11',
        ready: 1,
        skipped: 0,
        blocked: 0,
        results: [{ company_id: 'r5-company', company_name: 'R5 合成客户', status: 'ready' }],
        notes: [],
      },
    }),
  );
  // 执行前必须先看到服务端预检结果（P02）。
  await dialog.getByRole('button', { name: '预检范围', exact: true }).click();
  await expect(dialog.locator('.batch-preview')).toBeVisible();
  await dialog.getByRole('button', { name: '开始批量开期', exact: true }).click();
  await expect(dialog.getByRole('alert')).toContainText('R5 合成账期已经变化');
  await expect(reason).toHaveValue('R5 手机合成操作依据');
  await expect(dialog).toHaveAccessibleDescription(/R5 合成账期已经变化/);
  await expect(page.locator('.auth-shell')).toHaveCount(0);
  expect(attempts).toBe(1);
  await page.keyboard.press('Escape');
  await expect(dialog).toHaveCount(0);
  await expect(opener).toBeFocused();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(
    true,
  );
});

test('R5 真实本地批量复核部分成功，补件后仅重试失败凭证并保持显式导出', async ({
  page,
  request,
}) => {
  const headers = await ownerHeaders(request);
  const tag = crypto.randomUUID().slice(0, 8);
  const items: Array<{ bookId: string; draftId: string; sourceId: string; description: string }> =
    [];
  for (const suffix of ['good', 'blocked']) {
    const response = await request.post('/api/books', {
      headers,
      data: { name: `R5 批量 ${tag}-${suffix}`, period: '2026-10' },
    });
    expect(response.status()).toBe(201);
    const book = await response.json();
    await independentReviewer(request, headers, book.id);
    const description = `R5办公用品 ${tag}-${suffix}`;
    const imported = await request.post(`/api/books/${book.id}/imports`, {
      headers,
      data: {
        source_name: 'R5 合成流水，仅本地验收',
        rows: [
          {
            external_id: `${tag}-${suffix}`,
            date: '2026-10-05',
            description,
            counterparty: 'R5 虚构供应商',
            amount: '100.00',
            direction: 'expense',
            evidence_ref: suffix === 'good' ? 'R5 合成归档索引' : '',
          },
        ],
      },
    });
    expect(imported.ok()).toBe(true);
    expect((await request.post(`/api/books/${book.id}/suggestions`, { headers })).ok()).toBe(true);
    const detail = await (await request.get(`/api/books/${book.id}`, { headers })).json();
    const draft = detail.drafts.find(
      (value: { description: string }) => value.description === description,
    );
    expect(draft).toBeTruthy();
    items.push({ bookId: book.id, draftId: draft.id, sourceId: draft.source_id, description });
  }
  const exportKey = `r5-browser-export-${tag}`;
  const beforeReview = await request.post(`/api/books/${items[0].bookId}/exports`, {
    headers,
    data: { draft_ids: [items[0].draftId], idempotency_key: exportKey },
  });
  expect(beforeReview.status()).toBe(409);
  await pinReviewer(page);
  await page.goto('/workspace/review');
  await expect(page.getByRole('region', { name: '当前登录身份' })).toContainText('复核会计');
  const requests: Array<{ items: Array<{ draft_id: string }> }> = [];
  page.on('request', (requestValue) => {
    if (
      requestValue.method() === 'POST' &&
      new URL(requestValue.url()).pathname === '/api/review/previews'
    )
      requests.push(requestValue.postDataJSON());
  });
  for (const item of items)
    await page.getByLabel(`选择凭证 ${item.description}`, { exact: true }).check();
  await page.getByRole('button', { name: '预览所选凭证', exact: true }).click();
  await page.getByLabel('确认已核对批量凭证证据与分录', { exact: true }).check();
  await page.getByRole('button', { name: '确认批量复核', exact: true }).click();
  const receipt = page.getByRole('status', { name: '批量复核回执', exact: true });
  await expect(receipt).toContainText('部分账套完成复核');
  await expect(receipt).toContainText('通过 1 张 · 未通过 1 个账套');
  for (const item of items) {
    const exports = await (
      await request.get(`/api/books/${item.bookId}/exports`, { headers })
    ).json();
    expect(exports.exports).toEqual([]);
  }
  const goodBefore = await (await request.get(`/api/books/${items[0].bookId}`, { headers })).json();
  const goodVersion = goodBefore.drafts.find(
    (value: { id: string }) => value.id === items[0].draftId,
  ).version;
  expect(
    (
      await request.post(`/api/books/${items[1].bookId}/sources/${items[1].sourceId}/evidence`, {
        headers,
        data: { evidence_ref: 'R5 人工补件后重新核对' },
      })
    ).ok(),
  ).toBe(true);
  await page.getByRole('button', { name: '关闭复核预览', exact: true }).click();
  await page.getByLabel(`选择凭证 ${items[1].description}`, { exact: true }).check();
  await page.getByRole('button', { name: '预览所选凭证', exact: true }).click();
  await page.getByLabel('确认已核对批量凭证证据与分录', { exact: true }).check();
  await page.getByRole('button', { name: '确认批量复核', exact: true }).click();
  await expect(receipt).toContainText('通过 1 张 · 未通过 0 个账套');
  expect(requests).toHaveLength(2);
  expect(requests[1].items.map((item) => item.draft_id)).toEqual([items[1].draftId]);
  const goodAfter = await (await request.get(`/api/books/${items[0].bookId}`, { headers })).json();
  expect(
    goodAfter.drafts.find((value: { id: string }) => value.id === items[0].draftId).version,
  ).toBe(goodVersion);
  const exported = await request.post(`/api/books/${items[0].bookId}/exports`, {
    headers,
    data: { draft_ids: [items[0].draftId], idempotency_key: exportKey },
  });
  expect(exported.ok()).toBe(true);
  const receiptValue = await exported.json();
  expect(receiptValue.count).toBe(1);
  expect(receiptValue.csv).toContain('100.00');
  const replay = await request.post(`/api/books/${items[0].bookId}/exports`, {
    headers,
    data: { draft_ids: [items[0].draftId], idempotency_key: exportKey },
  });
  expect((await replay.json()).id).toBe(receiptValue.id);
  const wrongBook = await request.post(`/api/books/${items[0].bookId}/exports`, {
    headers,
    data: { draft_ids: [items[1].draftId], idempotency_key: `r5-wrong-book-${tag}` },
  });
  expect(wrongBook.status()).toBe(409);
  const employee = await (await request.get('/api/session?persona=employee')).json();
  const denied = await request.get(`/api/books/${items[0].bookId}/exports`, {
    headers: { Authorization: `Bearer ${employee.token}` },
  });
  expect([403, 404]).toContain(denied.status());
});
