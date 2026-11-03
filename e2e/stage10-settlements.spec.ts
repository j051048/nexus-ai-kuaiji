import { independentReviewer, pinReviewer } from './independent-review';
import { openAccounting, openBookTools } from './ui-navigation';
import { expect, test, type APIRequestContext } from '@playwright/test';
const image = {
  name: '第十阶段虚构未付款票据.png',
  mimeType: 'image/png',
  buffer: Buffer.from(
    'iVBORw0KGgoAAAANSUhEUgAAAAgAAAAICAIAAABLbSncAAAAFUlEQVR4nGP8//8/AzbAhFV00EoAAFbUAw037MyjAAAAAElFTkSuQmCC',
    'base64',
  ),
};

async function prepare(request: APIRequestContext) {
  const headers: Record<string, { Authorization: string }> = {};
  for (const persona of ['employee', 'department', 'finance', 'accountant']) {
    const session = await (await request.get(`/api/session?persona=${persona}`)).json();
    headers[persona] = { Authorization: `Bearer ${session.token}` };
  }
  const context = await (
      await request.get('/api/business/context', { headers: headers.employee })
    ).json(),
    company = context.companies.find(
      (item: { name: string }) => item.name === '青屿设计（虚构示例）',
    ),
    suffix = crypto.randomUUID().slice(0, 8),
    partyName = `核销虚构供应商 ${suffix}`,
    description = `第十阶段未付款采购 ${suffix}`;
  expect(
    (
      await request.put(`/api/companies/${company.id}/intelligence/settings`, {
        headers: headers.accountant,
        data: { enabled: false },
      })
    ).status(),
  ).toBe(200);
  let response = await request.post('/api/business/events', {
    headers: headers.employee,
    data: {
      company_id: company.id,
      template_id: 'procurement_expense',
      payload: {
        occurred_on: '2026-10-10',
        description,
        counterparty: partyName,
        amount: '100.00',
        direction: 'expense',
        payment_status: 'unpaid',
        category: 'office',
        purpose: '虚构同期开票后付款采购，仅用于验证核销',
      },
    },
  });
  expect(response.status()).toBe(201);
  let event = await response.json();
  response = await request.post(
    `/api/business/events/${event.id}/attachments?expected_version=${event.version}`,
    { headers: headers.employee, multipart: { file: image } },
  );
  expect(response.status()).toBe(200);
  event = await response.json();
  for (const [action, actor] of [
    ['submit', 'employee'],
    ['department-approve', 'department'],
    ['finance-approve', 'finance'],
  ]) {
    response = await request.post(`/api/business/events/${event.id}/${action}`, {
      headers: headers[actor],
      data: { expected_version: event.version },
    });
    expect(response.status()).toBe(200);
    event = await response.json();
  }
  const workbench = await (
      await request.get('/api/workbench', { headers: headers.finance })
    ).json(),
    book = workbench.books.find((row: { company_id: string }) => row.company_id === company.id);
  response = await request.post(`/api/business/events/${event.id}/handoff`, {
    headers: headers.finance,
    data: {
      expected_version: event.version,
      book_id: book.id,
      idempotency_key: crypto.randomUUID(),
    },
  });
  expect(response.status()).toBe(200);
  const mapping = await response.json();
  response = await request.post(`/api/books/${book.id}/ledger/entities`, {
    headers: headers.accountant,
    data: { kind: 'counterparty', code: `SUP-SETTLE-${suffix}`, name: partyName },
  });
  expect(response.status()).toBe(201);
  const bankDescription = `第十阶段应付清偿回单 ${suffix}`;
  response = await request.post(`/api/books/${book.id}/imports`, {
    headers: headers.accountant,
    data: {
      source_name: '虚构核销银行文件',
      rows: [
        {
          external_id: `settle-${suffix}`,
          date: '2026-10-10',
          description: bankDescription,
          counterparty: partyName,
          amount: '100.00',
          direction: 'expense',
          evidence_ref: '虚构付款回单，金额100元，仅验证流程',
        },
      ],
    },
  });
  expect(response.status()).toBe(200);
  expect(
    (
      await request.post(`/api/books/${book.id}/suggestions`, { headers: headers.accountant })
    ).status(),
  ).toBe(200);
  const overview = await (
      await request.get(`/api/books/${book.id}/settlements`, { headers: headers.accountant })
    ).json(),
    bank = overview.banks.find(
      (row: { description: string }) => row.description === bankDescription,
    );
  expect(bank).toBeTruthy();
  return { headers, book, event, mapping, description, partyName, bank };
}

test('同期辅助核销的真实界面闭环：登记未结、确认分配、先审业务再清偿银行且保持人工复核', async ({
  page,
  request,
}) => {
  // Two real review identities, both business and bank workflows, plus a held
  // navigation response. Individual expectations retain their normal deadlines.
  test.setTimeout(60_000);
  const { headers, book, event, mapping, description, partyName, bank } = await prepare(request);
  await page.goto('/');
  await page.getByLabel('切换验证身份').selectOption('accountant');
  await openAccounting(page);
  await page.getByLabel('搜索企业名称').fill(book.name);
  await page.getByRole('listitem').filter({ hasText: book.name }).click();
  await openBookTools(page);
  await page.getByRole('button', { name: '收付款核销', exact: true }).click();
  await expect(page.getByRole('heading', { name: '收付款核销', exact: true })).toBeVisible();
  await page
    .getByLabel('核销核对依据', { exact: true })
    .fill('已核对合同主体、未付款采购及同期间银行付款回单');
  await page
    .locator('.settlement-candidate')
    .filter({ hasText: description })
    .getByRole('button', { name: '登记未结事项', exact: true })
    .click();
  await expect(page.getByRole('status')).toContainText('已登记辅助未结事项');
  const afterOpening = await (
      await request.get(`/api/books/${book.id}/settlements`, { headers: headers.accountant })
    ).json(),
    item = afterOpening.items.find(
      (row: { source_id: string }) => row.source_id === mapping.source_id,
    );
  expect(item.remaining).toBe('100.00');
  await page.getByLabel('核销银行流水', { exact: true }).selectOption(bank.id);
  await page.getByLabel(`选择核销事项${item.id}`, { exact: true }).check();
  await page.getByRole('button', { name: '预览收付分配', exact: true }).click();
  await expect(page.getByRole('dialog', { name: '收付分配预览' })).toContainText('100.00');
  await page.getByRole('button', { name: '确认分配', exact: true }).click();
  await expect(page.getByRole('status')).toContainText('分配已持久记录');
  const allocated = await (
    await request.get(`/api/books/${book.id}/settlements`, { headers: headers.accountant })
  ).json();
  expect(allocated.items.find((row: { id: string }) => row.id === item.id).remaining).toBe('0.00');
  // Hold the target book response so an old seed voucher cannot briefly remain
  // actionable while navigation to the requested original business is pending.
  let releaseSummary!: () => void,
    summaryArrived!: () => void,
    holdSummary = true;
  const summaryGate = new Promise<void>((resolve) => {
      releaseSummary = resolve;
    }),
    summarySeen = new Promise<void>((resolve) => {
      summaryArrived = resolve;
    });
  const summaryUrl = (url: URL) =>
    url.pathname === `/api/books/${book.id}` &&
    url.searchParams.get('summaries') === 'true' &&
    url.searchParams.get('focus_draft_id') === mapping.draft_id;
  const summaryRoutes = new Set<Promise<void>>();
  await page.route(summaryUrl, async (route) => {
    const task = (async () => {
      const response = await route.fetch();
      if (holdSummary) {
        summaryArrived();
        await summaryGate;
      }
      await route.fulfill({ response });
    })();
    summaryRoutes.add(task);
    try {
      await task;
    } finally {
      summaryRoutes.delete(task);
    }
  });
  const approvedRequests: string[] = [];
  page.on('request', (req) => {
    if (req.method() === 'POST' && new URL(req.url()).pathname.endsWith('/approve'))
      approvedRequests.push(new URL(req.url()).pathname);
  });
  await page
    .locator('.settlement-history')
    .filter({ has: page.getByRole('heading', { name: '未结余额', exact: true }) })
    .getByRole('row')
    .filter({ hasText: partyName })
    .getByRole('button', { name: '打开原业务凭证', exact: true })
    .click();
  await summarySeen;
  await expect(page.getByRole('button', { name: '确认审核', exact: true })).toHaveCount(0);
  await expect(page.locator('.review-panel')).toContainText('正在核对当前凭证');
  holdSummary = false;
  releaseSummary();
  while (summaryRoutes.size) await Promise.all([...summaryRoutes]);
  await page.unroute(summaryUrl);
  await expect(page.getByRole('textbox', { name: '凭证摘要', exact: true })).toHaveValue(
    description,
  );
  await expect(page.getByRole('button', { name: '确认审核', exact: true })).toBeEnabled();
  await page.getByRole('button', { name: '确认审核', exact: true }).click();
  await expect(page.locator('.approved-note')).toContainText('此凭证已审核，选中后即可导出。');
  expect(approvedRequests).toEqual([`/api/books/${book.id}/drafts/${mapping.draft_id}/approve`]);
  let draft = await (
    await request.get(`/api/books/${book.id}/drafts/${mapping.draft_id}`, {
      headers: headers.accountant,
    })
  ).json();
  expect(draft.status).toBe('approved');
  expect(
    draft.lines.some(
      (row: { account_code: string; credit: string }) =>
        row.account_code === item.clearing_account && row.credit === '100.00',
    ),
  ).toBe(true);
  await openBookTools(page);
  await page.getByRole('button', { name: '收付款核销', exact: true }).click();
  await page.getByRole('button', { name: '检查 / 生成清偿草稿', exact: true }).click();
  await expect(page.getByRole('dialog', { name: '银行清偿草稿' })).toContainText(
    item.clearing_account,
  );
  await page.getByRole('button', { name: '生成未审核清偿草稿', exact: true }).click();
  await expect(page.getByRole('status')).toContainText('尚未审核或导出');
  draft = await (
    await request.get(`/api/books/${book.id}/drafts/${bank.draft_id}`, {
      headers: headers.accountant,
    })
  ).json();
  expect(draft.status).toBe('draft');
  expect(draft.lines.map((row: { account_code: string }) => row.account_code).sort()).toEqual(
    ['1002', item.clearing_account].sort(),
  );
  await page.getByRole('button', { name: '检查 / 生成清偿草稿', exact: true }).click();
  await page.getByRole('button', { name: '打开凭证复核', exact: true }).click();
  await expect(page.getByRole('textbox', { name: '凭证摘要', exact: true })).toHaveValue(
    bank.description + '（应收/应付清偿）',
  );
  await expect(page.getByRole('button', { name: '确认审核', exact: true })).toBeDisabled();
  await independentReviewer(request, headers.accountant, book.id);
  await pinReviewer(page);
  await page.reload();
  await expect(page.getByRole('button', { name: '确认审核', exact: true })).toBeEnabled();
  await page.getByRole('button', { name: '确认审核', exact: true }).click();
  await expect(page.locator('.approved-note')).toContainText('此凭证已审核，选中后即可导出。');
  expect(approvedRequests).toEqual([
    `/api/books/${book.id}/drafts/${mapping.draft_id}/approve`,
    `/api/books/${book.id}/drafts/${bank.draft_id}/approve`,
  ]);
  draft = await (
    await request.get(`/api/books/${book.id}/drafts/${bank.draft_id}`, {
      headers: headers.accountant,
    })
  ).json();
  expect(draft.status).toBe('approved');
  const originalEvent = await (
    await request.get(`/api/business/events/${event.id}`, { headers: headers.accountant })
  ).json();
  expect(originalEvent.payload.payment_status).toBe('unpaid');
  expect(
    (
      await request.get(`/api/books/${book.id}/settlements`, { headers: headers.employee })
    ).status(),
  ).toBe(404);
  await page.screenshot({ path: 'artifacts/stage10-settlement-reviewed.png', fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(
    true,
  );
});

test('打开明确凭证期间真实撤销账套权限后保留错误，不自动展示首账套的其他凭证', async ({
  page,
  request,
}) => {
  const { headers, book, mapping, description } = await prepare(request);
  await page.goto('/');
  await page.getByLabel('切换验证身份').selectOption('finance');
  await page.getByRole('button', { name: '业务事项', exact: true }).click();
  await page.getByRole('tab', { name: '企业财务', exact: true }).click();
  if ((await page.getByLabel('筛选业务事项状态', { exact: true }).inputValue()) !== '')
    await page.getByLabel('筛选业务事项状态', { exact: true }).selectOption('');
  await page
    .getByRole('button')
    .filter({ has: page.getByText(description, { exact: true }) })
    .click();
  await expect(page.getByRole('button', { name: '查看关联凭证', exact: true })).toBeVisible();
  let observedRevoke!: () => void;
  const revoked = new Promise<void>((resolve) => {
    observedRevoke = resolve;
  });
  let handled = false;
  const targetUrl = (url: URL) =>
    url.pathname === `/api/books/${book.id}` &&
    url.searchParams.get('focus_draft_id') === mapping.draft_id;
  await page.route(targetUrl, async (route) => {
    if (!handled) {
      handled = true;
      const result = await request.delete(`/api/books/${book.id}/members/local-business-finance`, {
        headers: headers.accountant,
      });
      expect(result.status()).toBe(200);
      observedRevoke();
    }
    await route.continue();
  });
  try {
    await page.getByRole('button', { name: '查看关联凭证', exact: true }).click();
    await revoked;
    await expect(page.getByRole('alert').filter({ hasText: '不存在或无权访问' })).toBeVisible();
    expect(
      (
        await request.get(`/api/books/${book.id}/drafts/${mapping.draft_id}`, {
          headers: headers.finance,
        })
      ).status(),
    ).toBe(404);
    await expect(page.getByRole('textbox', { name: '同屏凭证摘要', exact: true })).toHaveCount(0);
    await expect(page.getByRole('button', { name: '确认审核', exact: true })).toBeDisabled();
    await expect(page.getByRole('alert').filter({ hasText: '不存在或无权访问' })).toBeVisible();
  } finally {
    await page.unroute(targetUrl);
    const restored = await request.put(`/api/books/${book.id}/members/local-business-finance`, {
      headers: headers.accountant,
      data: { role: 'editor' },
    });
    expect(restored.status()).toBe(200);
  }
});
