import { expect, test } from '@playwright/test';

test('人工客户回执与逐行差异：原审核版本留存，差异说明不修改已导出分录', async ({
  page,
  request,
}) => {
  const session = await (await request.get('/api/session?persona=accountant')).json();
  const finance = await (await request.get('/api/session?persona=finance')).json();
  const headers = { Authorization: `Bearer ${session.token}` };
  const reviewerHeaders = { Authorization: `Bearer ${finance.token}` };
  const makerResponse = await request.get('/api/me', { headers });
  const reviewerResponse = await request.get('/api/me', { headers: reviewerHeaders });
  expect(makerResponse.status()).toBe(200);
  expect(reviewerResponse.status()).toBe(200);
  const maker = (await makerResponse.json()).user;
  const reviewer = (await reviewerResponse.json()).user;
  expect(typeof maker.id).toBe('string');
  expect(typeof reviewer.id).toBe('string');
  expect(reviewer.id).not.toBe(maker.id);
  const created = await request.post('/api/books', {
    headers,
    data: { name: '第九阶段交付回执验证（虚构企业）', period: '2026-10' },
  });
  expect(created.status()).toBe(201);
  const book = await created.json();
  // Independent review uses a separately authenticated local identity and real
  // book/company grants. Never disable the policy to preserve an old fixture.
  expect(
    (
      await request.put(`/api/companies/${book.company_id}/members`, {
        headers,
        data: { email: reviewer.email, role: 'finance', department_id: null },
      })
    ).status(),
  ).toBe(200);
  expect(
    (
      await request.put(`/api/books/${book.id}/members/${reviewer.id}`, {
        headers,
        data: { role: 'reviewer' },
      })
    ).status(),
  ).toBe(200);
  const rows = [0, 1].map((i) => ({
    external_id: `stage9-receipt:${i}`,
    date: '2026-10-03',
    description: `合成办公采购 ${i + 1}`,
    counterparty: '虚构供应商',
    amount: '113.01',
    direction: 'expense',
    evidence_ref: `合成归档原件-${i}`,
  }));
  expect(
    (
      await request.post(`/api/books/${book.id}/imports`, {
        headers,
        data: { source_name: '合成交付验证', rows },
      })
    ).status(),
  ).toBe(200);
  expect((await request.post(`/api/books/${book.id}/suggestions`, { headers })).status()).toBe(200);
  const drafts = (await (await request.get(`/api/books/${book.id}`, { headers })).json()).drafts;
  for (const draft of drafts) {
    const data = {
      expected_version: draft.version,
      expected_policy_version: draft.review_responsibility.policy_version,
      review_reason: '独立身份核对合成交付凭证及原件证据',
    };
    const ownReview = await request.post(`/api/books/${book.id}/drafts/${draft.id}/approve`, {
      headers,
      data,
    });
    expect(ownReview.status()).toBe(403);
    expect(await ownReview.text()).toContain('独立复核');
    const reviewed = await request.post(`/api/books/${book.id}/drafts/${draft.id}/approve`, {
      headers: reviewerHeaders,
      data,
    });
    expect(reviewed.status(), await reviewed.text()).toBe(200);
    const receipt = (await reviewed.json()).review_responsibility;
    expect(receipt.independent).toBe(true);
    expect(receipt.maker_user_id).toBe(maker.id);
    expect(receipt.reviewer_user_id).toBe(reviewer.id);
  }
  const exported = await request.post(`/api/books/${book.id}/exports`, {
    headers,
    data: {
      draft_ids: drafts.map((item: { id: string }) => item.id),
      idempotency_key: crypto.randomUUID(),
    },
  });
  expect(exported.status()).toBe(200);
  const manifestExport = await exported.json();
  const templateResponse = await request.get(
    `/api/books/${book.id}/delivery/exports/${manifestExport.id}/template`,
    { headers },
  );
  expect(templateResponse.status()).toBe(200);
  const facts = await templateResponse.json();
  facts.items.forEach((item: { status: string; external_voucher_id: string }, i: number) => {
    item.status = 'accepted';
    item.external_voucher_id = `合成客户凭证-${i + 1}`;
  });
  facts.items[1].lines[0].debit = '113.02';
  await page.goto('/');
  // The owner profile arrives asynchronously. Wait for its current primary
  // action instead of selecting a retired fallback from an initial count().
  const clients = page
    .getByRole('navigation', { name: '主导航', exact: true })
    .getByRole('button', { name: '客户企业', exact: true });
  await expect(clients).toBeVisible();
  await clients.click();
  await page.getByLabel('搜索企业名称').fill('第九阶段交付回执验证');
  await page.getByRole('listitem').filter({ hasText: book.name }).click();
  await page.getByRole('button', { name: '导入回执核验', exact: true }).click();
  await expect(page.getByRole('heading', { name: '交付与回执核验', exact: true })).toBeVisible();
  const pipeline = page.getByRole('navigation', { name: '做账五步流水线' });
  await expect(pipeline).toBeVisible();
  await expect(pipeline.getByRole('button', { name: '⑤ 交付与导出', exact: true })).toHaveAttribute(
    'aria-current',
    'step',
  );
  await expect(page.getByRole('button', { name: '返回账套', exact: true })).toHaveCount(0);
  await page.getByRole('button').filter({ hasText: manifestExport.filename }).click();
  await expect(
    page.getByRole('heading', { name: '2 张凭证 · 原始导出版本', exact: true }),
  ).toBeVisible();
  const download = page.waitForEvent('download');
  await page.getByRole('button', { name: 'JSON 回执模板', exact: true }).click();
  expect((await download).suggestedFilename()).toContain('.json');
  await page.getByLabel('交付回执文件', { exact: true }).setInputFiles({
    name: 'stage9合成客户回执.json',
    mimeType: 'application/json',
    buffer: Buffer.from(JSON.stringify(facts), 'utf8'),
  });
  await page
    .getByLabel('回执来源与核验依据', { exact: true })
    .fill('合成客户软件回执用于逐行对照验证，尚未接入真实厂商');
  await page.getByRole('button', { name: '导入并核对回执', exact: true }).click();
  await expect(page.getByRole('status')).toContainText('回执事实已保存');
  await expect(page.getByText('回执数据一致', { exact: true })).toBeVisible();
  await expect(page.getByText('分录借方金额不一致', { exact: true })).toBeVisible();
  await page
    .locator('.delivery-result-row.discrepancy')
    .getByRole('button', { name: '记录处理依据', exact: true })
    .click();
  await page
    .getByLabel('交付差异处理依据', { exact: true })
    .fill('已联系虚构客户核实借方金额，等待修正后的回执；不重发凭证');
  await page.getByRole('button', { name: '保存处理依据', exact: true }).click();
  await expect(page.getByRole('status')).toContainText('处理依据已追加');
  await expect(page.getByText('存在交付差异', { exact: true })).toBeVisible();
  const detail = await (
    await request.get(`/api/books/${book.id}/delivery/exports/${manifestExport.id}`, { headers })
  ).json();
  expect(detail.receipts[0].summary).toEqual({
    verified: 1,
    received_unverified: 0,
    discrepancy: 1,
  });
  for (const draft of drafts)
    expect(
      (await (await request.get(`/api/books/${book.id}/drafts/${draft.id}`, { headers })).json())
        .status,
    ).toBe('exported');
  const employee = await (await request.get('/api/session?persona=employee')).json();
  expect(
    (
      await request.get(`/api/books/${book.id}/delivery/exports/${manifestExport.id}`, {
        headers: { Authorization: `Bearer ${employee.token}` },
      })
    ).status(),
  ).toBe(404);
  await page.screenshot({ path: 'artifacts/stage9-manual-delivery-receipt.png', fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(
    true,
  );
});
