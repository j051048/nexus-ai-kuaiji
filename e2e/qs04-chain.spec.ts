import { expect, test } from '@playwright/test';

test.setTimeout(150_000);

/**
 * Q-S04 全链路（本地虚构数据）：财务导入并生成建议 → 会计在真实界面复核确认 → 导出 → 账簿查询。
 * 制单责任由「生成草稿的财务身份」承担，会计是另一个人，因此独立复核成立。
 */
test('财务制单、会计复核、导出与账簿查询的本地全链路', async ({ page, request }) => {
  const session = async (persona: string) => {
    const value = await (await request.get(`/api/session?persona=${persona}`)).json();
    return { Authorization: `Bearer ${value.token}` };
  };
  const accountant = await session('accountant');
  const finance = await session('finance');
  const tag = crypto.randomUUID().slice(0, 8);

  const company = await (
    await request.post('/api/companies', {
      headers: accountant,
      data: { name: `全链路虚构企业 ${tag}`, tax_id: '' },
    })
  ).json();
  const book = await (
    await request.post('/api/books', {
      headers: accountant,
      data: { company_id: company.id, name: `全链路虚构账套 ${tag}`, period: '2026-10' },
    })
  ).json();
  expect(
    (
      await request.put(`/api/companies/${company.id}/members`, {
        headers: accountant,
        data: { email: 'finance@demo.example.invalid', role: 'finance', department_id: null },
      })
    ).status(),
  ).toBe(200);
  expect(
    (
      await request.put(`/api/books/${book.id}/members/local-business-finance`, {
        headers: accountant,
        data: { role: 'editor' },
      })
    ).status(),
  ).toBe(200);

  // 财务先记账：导入流水并生成候选分录，制单责任因此落在财务身上。
  expect(
    (
      await request.post(`/api/books/${book.id}/imports`, {
        headers: finance,
        data: {
          source_name: `全链路虚构流水 ${tag}`,
          rows: [
            {
              external_id: `${tag}-1`,
              date: '2026-10-05',
              description: `全链路办公用品甲 ${tag}`,
              amount: '100.00',
              direction: 'expense',
              evidence_ref: '合成票据甲，仅验证流程',
            },
            {
              external_id: `${tag}-2`,
              date: '2026-10-06',
              description: `全链路办公用品乙 ${tag}`,
              amount: '125.00',
              direction: 'expense',
              evidence_ref: '合成票据乙，仅验证流程',
            },
          ],
        },
      })
    ).status(),
  ).toBe(200);
  expect(
    (await request.post(`/api/books/${book.id}/suggestions`, { headers: finance })).status(),
  ).toBe(200);
  const drafts = (
    await (await request.get(`/api/books/${book.id}`, { headers: accountant })).json()
  ).drafts as { id: string; status: string }[];
  expect(drafts.length).toBeGreaterThan(0);
  expect(drafts.every((row) => row.status === 'draft')).toBe(true);

  // 会计在真实界面复核：制单人是财务，因此“确认所选复核包”必须真的可用。
  await page.goto('/');
  await page.getByLabel('切换验证身份').selectOption('accountant');
  const nav = page.getByRole('navigation', { name: '主导航', exact: true });
  if (!(await nav.getByRole('button', { name: '跨企业批量复核', exact: true }).isVisible())) {
    await nav.locator('summary', { hasText: '会计工作区' }).click();
  }
  await nav.getByRole('button', { name: '跨企业批量复核', exact: true }).click();
  await page.getByLabel('复核企业', { exact: true }).selectOption(company.id);
  await page.getByLabel('选择本页凭证', { exact: true }).check();
  await page.getByRole('button', { name: '准备同类复核包', exact: true }).click();
  await expect(
    page.getByRole('heading', { name: '同类业务，一起核对。', exact: true }),
  ).toBeVisible();
  await expect(page.getByText('当前检查通过 0', { exact: false })).toHaveCount(0);

  const packets = page.getByLabel('选择同类复核包', { exact: false });
  await expect(packets.first()).toBeVisible();
  const packetCount = await packets.count();
  expect(packetCount).toBeGreaterThan(0);
  for (let index = 0; index < packetCount; index += 1) await packets.nth(index).check();
  await page.getByRole('button', { name: '生成所选复核预览', exact: true }).click();
  const confirm = page.getByRole('button', { name: '确认所选复核包', exact: true });
  await expect(confirm).toBeDisabled();
  await page.getByLabel('确认已核对同类复核包的证据和差异', { exact: true }).check();
  await expect(confirm).toBeEnabled();
  const [response] = await Promise.all([
    page.waitForResponse(
      (candidate) =>
        candidate.url().includes('/confirm') && candidate.request().method() === 'POST',
    ),
    confirm.click(),
  ]);
  const result = (await response.json()) as {
    results?: { status: string; error?: string | null }[];
  };
  expect(result.results?.every((row) => row.status === 'succeeded')).toBe(true);

  let statuses: string[] = [];
  for (let attempt = 0; attempt < 10; attempt += 1) {
    const rows = (
      await (await request.get(`/api/books/${book.id}`, { headers: accountant })).json()
    ).drafts as { status: string }[];
    statuses = rows.map((row) => row.status);
    if (statuses.every((value) => value === 'approved')) break;
    await page.waitForTimeout(300);
  }
  expect(statuses).toEqual(['approved', 'approved']);

  // 审核通过后才有导出；导出记录与账簿查询都来自真实接口。
  const exported = await request.post(`/api/books/${book.id}/exports`, {
    headers: accountant,
    data: { draft_ids: drafts.map((row) => row.id), idempotency_key: `qs04-export-${tag}` },
  });
  expect(exported.status(), await exported.text()).toBe(200);
  const exportList = await (
    await request.get(`/api/books/${book.id}/exports`, { headers: accountant })
  ).json();
  expect((exportList.exports as unknown[]).length).toBeGreaterThan(0);

  // 账簿读模型（试算平衡/正式账簿）由 formal-books 核心用例覆盖；此处只证明
  // “同一会话内从制单到导出”的链路是通的，不重复断言读模型内部结构。
  const detail = await (await request.get(`/api/books/${book.id}`, { headers: accountant })).json();
  // 导出会把同一批凭证推进到 exported；两种状态都属于“已复核过”这一轴。
  expect(
    (detail.drafts as { status: string }[]).every((row) =>
      ['approved', 'exported'].includes(row.status),
    ),
  ).toBe(true);
});
