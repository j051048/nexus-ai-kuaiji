import { expect, test } from '@playwright/test';

test('同类复核包保留金额差异、明确选择和人工确认，审核后仍等待导出', async ({ page, request }) => {
  const session = await (await request.get('/api/session?persona=accountant')).json();
  const headers = { Authorization: `Bearer ${session.token}` },
    tag = crypto.randomUUID().slice(0, 8);
  const name = `第十一阶段同类复核企业 ${tag}`,
    party = `复核供应商 ${tag}`;
  let response = await request.post('/api/books', { headers, data: { name, period: '2026-10' } });
  expect(response.status()).toBe(201);
  const book = await response.json();
  response = await request.post(`/api/books/${book.id}/ledger/entities`, {
    headers,
    data: { kind: 'counterparty', code: 'SUP-PACKET', name: party },
  });
  expect(response.status()).toBe(201);
  const financeSession = await (await request.get('/api/session?persona=finance')).json();
  const finance = { Authorization: `Bearer ${financeSession.token}` };
  await request.put(`/api/companies/${book.company_id}/members`, {
    headers,
    data: { email: 'finance@demo.example.invalid', role: 'finance', department_id: null },
  });
  await request.put(`/api/books/${book.id}/members/local-business-finance`, {
    headers,
    data: { role: 'editor' },
  });
  response = await request.post(`/api/books/${book.id}/imports`, {
    headers: finance,
    data: {
      source_name: '虚构复核包流水',
      rows: [
        {
          external_id: `${tag}-1`,
          date: '2026-10-05',
          description: `复核包办公用品甲 ${tag}`,
          counterparty: party,
          amount: '100.00',
          direction: 'expense',
          evidence_ref: '合成票据甲，仅验证流程',
        },
        {
          external_id: `${tag}-2`,
          date: '2026-10-06',
          description: `复核包办公用品乙 ${tag}`,
          counterparty: party,
          amount: '125.00',
          direction: 'expense',
          evidence_ref: '合成票据乙，仅验证流程',
        },
      ],
    },
  });
  expect(response.status()).toBe(200);
  expect(
    (await request.post(`/api/books/${book.id}/suggestions`, { headers: finance })).status(),
  ).toBe(200);
  await page.goto('/');
  await page.getByLabel('切换验证身份').selectOption('accountant');
  const nav = page.getByRole('navigation', { name: '主导航', exact: true });
  // 会计入口默认收在“会计工作区”折叠组里：先展开再点，测试不应依赖“恰好落在该分组”。
  if (!(await nav.getByRole('button', { name: '跨企业批量复核', exact: true }).isVisible())) {
    await nav.locator('summary', { hasText: '会计工作区' }).click();
  }
  await nav.getByRole('button', { name: '跨企业批量复核', exact: true }).click();
  await page.getByLabel('复核企业', { exact: true }).selectOption(book.company_id);
  await page.getByLabel('选择本页凭证', { exact: true }).check();
  await page.getByRole('button', { name: '准备同类复核包', exact: true }).click();
  await expect(
    page.getByRole('heading', { name: '同类业务，一起核对。', exact: true }),
  ).toBeVisible();
  await expect(page.locator('.review-packet')).toHaveCount(1);
  await expect(page.locator('.review-packet-amounts')).toContainText('225.00');
  await expect(page.locator('.review-packet-amounts')).toContainText('100.00–125.00');
  expect(
    (await (await request.get(`/api/books/${book.id}`, { headers })).json()).drafts.every(
      (row: { status: string }) => row.status === 'draft',
    ),
  ).toBe(true);
  await page.getByLabel('选择复核包内当前检查通过的凭证', { exact: true }).check();
  await page.getByRole('button', { name: '生成所选复核预览', exact: true }).click();
  await expect(page.getByRole('button', { name: '确认所选复核包', exact: true })).toBeDisabled();
  await page.getByLabel('确认已核对同类复核包的证据和差异', { exact: true }).check();
  await page.getByRole('button', { name: '确认所选复核包', exact: true }).click();
  await expect(page.getByRole('status')).toContainText('已审核 2 张');
  const detail = await (await request.get(`/api/books/${book.id}`, { headers })).json();
  expect(detail.drafts.every((row: { status: string }) => row.status === 'approved')).toBe(true);
  expect(
    (await (await request.get(`/api/books/${book.id}/exports`, { headers })).json()).exports,
  ).toEqual([]);
  const employee = await (await request.get('/api/session?persona=employee')).json();
  const item = detail.drafts[0];
  response = await request.post('/api/review/packages', {
    headers: { Authorization: `Bearer ${employee.token}` },
    data: {
      items: [{ book_id: book.id, draft_id: item.id, expected_version: item.version }],
      idempotency_key: crypto.randomUUID(),
    },
  });
  expect([403, 404]).toContain(response.status());
  await page.screenshot({ path: 'artifacts/stage11-review-packages.png', fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(
    true,
  );
});
