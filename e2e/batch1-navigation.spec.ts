import { expect, test, type APIRequestContext } from '@playwright/test';

async function prepare(request: APIRequestContext) {
  const session = await (await request.get('/api/session?persona=accountant')).json();
  const headers = { Authorization: `Bearer ${session.token}` };
  const tag = crypto.randomUUID().slice(0, 8),
    party = `路由供应商 ${tag}`;
  const response = await request.post('/api/books', {
    headers,
    data: { name: `路由验证企业 ${tag}`, period: '2026-10' },
  });
  expect(response.status()).toBe(201);
  const book = await response.json();
  expect(
    (
      await request.post(`/api/books/${book.id}/ledger/entities`, {
        headers,
        data: { kind: 'counterparty', code: tag, name: party },
      })
    ).status(),
  ).toBe(201);
  expect(
    (
      await request.post(`/api/books/${book.id}/imports`, {
        headers,
        data: {
          source_name: '虚构路由验证',
          rows: [1, 2].map((index) => ({
            external_id: `${tag}-${index}`,
            date: '2026-10-05',
            description: `路由办公用品 ${tag}-${index}`,
            counterparty: party,
            amount: '100.00',
            direction: 'expense',
            evidence_ref: `合成归档索引-${index}`,
          })),
        },
      })
    ).status(),
  ).toBe(200);
  expect((await request.post(`/api/books/${book.id}/suggestions`, { headers })).status()).toBe(200);
  const detail = await (await request.get(`/api/books/${book.id}`, { headers })).json();
  return { book, draft: detail.drafts[0], drafts: detail.drafts };
}

test('账套与凭证链接可直达、刷新、后退；未保存修改明确阻断离开', async ({ page, request }) => {
  const { book, draft } = await prepare(request);
  await page.goto(`/workspace/accounting?book=${book.id}&draft=${draft.id}`);
  await expect(page.locator('#journal-description')).toHaveValue(draft.description, {
    timeout: 30_000,
  });
  await expect(page).toHaveURL(new RegExp(`company=${book.company_id}`));
  await page.reload();
  await expect(page.locator('#journal-description')).toHaveValue(draft.description, {
    timeout: 30_000,
  });
  await page.getByRole('link', { name: '衡账，返回工作台', exact: true }).click();
  await expect(page).toHaveURL(/\/workspace\/home/);
  await page.goBack();
  await expect(page.locator('#journal-description')).toHaveValue(draft.description);
  await page.locator('#journal-description').fill('尚未保存的路由编辑');
  await page.goForward();
  const dialog = page.getByRole('dialog', { name: '当前工作尚未结束' });
  await expect(dialog).toBeVisible();
  await dialog.getByRole('button', { name: '继续当前工作' }).click();
  await expect(page.locator('#journal-description')).toHaveValue('尚未保存的路由编辑');
  await page.goForward();
  await dialog.getByRole('button', { name: '放弃修改并离开' }).click();
  await expect(page).toHaveURL(/\/workspace\/home/);
  await expect(page.locator('#journal-description')).toHaveCount(0);
});

test('同屏复核链接、连续切换和快捷保存保留人工确认边界', async ({ page, request }) => {
  const { book } = await prepare(request);
  await page.setViewportSize({ width: 1600, height: 1000 });
  await page.goto(`/workspace/review?review_company=${book.company_id}`);
  await page.getByRole('button', { name: '查看证据', exact: true }).first().click();
  const drawer = page.getByRole('dialog', { name: '原件与凭证，一起核对', exact: true });
  await expect(drawer).toBeVisible();
  await expect(page).toHaveURL(/review_book=/);
  await expect(drawer.getByRole('table', { name: '同屏凭证分录表' })).toBeVisible();
  await expect(drawer.locator('.context-assistant-loading')).toHaveCount(0);
  await page.screenshot({ path: 'artifacts/batch1-compact-voucher-desktop.png', fullPage: false });
  await drawer.getByLabel('同屏凭证摘要').fill('快捷保存后的会计摘要');
  await page.keyboard.press('Control+s');
  await expect(drawer.locator('.inline-review-notice')).toContainText('草稿已保存');
  await expect(drawer.getByRole('button', { name: '确认审核', exact: true })).toBeDisabled();
  await drawer.getByRole('button', { name: '下一笔', exact: true }).click();
  await expect(drawer.getByLabel('同屏凭证摘要')).not.toHaveValue('快捷保存后的会计摘要');
  const expectedDescription = await drawer.getByLabel('同屏凭证摘要').inputValue();
  const url = page.url();
  await page.reload();
  await expect(drawer).toBeVisible();
  await expect(page).toHaveURL(url);
  await expect(drawer.getByLabel('同屏凭证摘要')).toHaveValue(expectedDescription);
  await page.setViewportSize({ width: 390, height: 844 });
  const closeAssistant = drawer.getByRole('button', { name: '关闭会计助手', exact: true });
  if (await closeAssistant.isVisible()) await closeAssistant.click();
  await drawer.getByLabel('同屏凭证摘要').scrollIntoViewIfNeeded();
  await page.screenshot({ path: 'artifacts/batch1-compact-voucher-mobile.png', fullPage: false });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(
    true,
  );
});

test('不获授权的企业直达链接不会显示凭证或原件', async ({ page, request }) => {
  const { book, draft } = await prepare(request);
  const employee = await (await request.get('/api/session?persona=employee')).json();
  await page.route('**/api/session', (route) => route.fulfill({ json: employee }));
  await page.goto(`/workspace/accounting?book=${book.id}&draft=${draft.id}`);
  await expect(page.getByLabel('切换验证身份')).toBeVisible();
  await expect(page.locator('#journal-description')).toHaveCount(0);
  await expect(page.getByRole('heading', { name: book.name, exact: true })).toHaveCount(0);
  await expect(page.getByRole('dialog', { name: '原件与凭证，一起核对' })).toHaveCount(0);
});
