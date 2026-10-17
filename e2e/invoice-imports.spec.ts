import { createHash } from 'node:crypto';
import { openAccounting } from './ui-navigation';
import { expect, test, type APIRequestContext } from '@playwright/test';

const HEADERS = [
  '发票种类',
  '发票代码',
  '发票号码',
  '开票日期',
  '销方名称',
  '销方纳税人识别号',
  '购方名称',
  '购方纳税人识别号',
  '金额',
  '税额',
  '价税合计',
  '发票状态',
].join(',');

function invoice(number: string, status = '正常') {
  return [
    '数电票（增值税普通发票）',
    '',
    number,
    '2026-10-12',
    '虚构销方有限公司',
    '91330100FAKE0001X',
    '虚构购方有限公司',
    '91330100FAKE0002X',
    '1000.00',
    '130.00',
    '1130.00',
    status,
  ].join(',');
}

function companyInvoice(number: string, companyTax: string) {
  return [
    '数电票（增值税普通发票）',
    '',
    number,
    '2026-10-12',
    '挂账测试客户企业',
    companyTax,
    '虚构应收客户有限公司',
    '91330100CUSTOMER01X',
    '1000.00',
    '130.00',
    '1130.00',
    '正常',
  ].join(',');
}

async function uploadInvoices(request: APIRequestContext, authorization: string, data: Buffer) {
  const digest = createHash('sha256').update(data).digest('hex');
  let response = await request.post('/api/uploads', {
    headers: { Authorization: authorization },
    data: {
      purpose: 'invoice_import',
      filename: `全量发票查询导出表-${Date.now()}.csv`,
      media_type: 'text/csv',
      size_bytes: data.length,
      sha256: digest,
    },
  });
  expect(response.status()).toBe(201);
  const intent = await response.json();
  response = await request.put(intent.url, {
    headers: { Authorization: authorization, 'Content-Type': 'text/csv' },
    data,
  });
  expect(response.status()).toBe(200);
  response = await request.post(`/api/uploads/${intent.id}/complete`, {
    headers: { Authorization: authorization },
  });
  expect(response.status()).toBe(200);
  expect((await response.json()).state).toBe('stored');
  return intent.id as string;
}

test('电子税务局发票导出表：导入批次留在对账页，只作勾稽证据', async ({ page, request }) => {
  const headers: Record<string, { Authorization: string }> = {};
  for (const persona of ['employee', 'finance', 'accountant']) {
    const session = await (await request.get(`/api/session?persona=${persona}`)).json();
    headers[persona] = { Authorization: `Bearer ${session.token}` };
  }
  const context = await (
    await request.get('/api/business/context', { headers: headers.employee })
  ).json();
  const company = context.companies[0];
  const choices = await (
    await request.get(
      `/api/companies/${encodeURIComponent(company.id)}/books?period=2026-10&limit=100`,
      {
        headers: headers.accountant,
      },
    )
  ).json();
  const book = choices.books.find(
    (item: { period_status: string; permissions: { edit: boolean } }) =>
      item.period_status === 'open' && item.permissions.edit,
  );
  expect(book, '会计需要当月可编辑的开放账套').toBeDefined();

  const suffix = Date.now().toString().slice(-10);
  const csv = Buffer.from(
    [
      HEADERS,
      invoice(`2499200${suffix}1`),
      invoice(`2499200${suffix}1`),
      invoice(`2499200${suffix}2`, '作废'),
    ].join('\n') + '\n',
    'utf-8',
  );
  const uploadId = await uploadInvoices(request, headers.accountant.Authorization, csv);

  let response = await request.post(`/api/books/${book.id}/invoice-imports`, {
    headers: headers.accountant,
    data: { upload_id: uploadId },
  });
  expect(response.status()).toBe(201);
  const batch = await response.json();
  expect(batch.row_count).toBe(3);
  expect(batch.duplicates).toBe(1);

  response = await request.get(`/api/books/${book.id}/invoice-imports?limit=20`, {
    headers: headers.accountant,
  });
  expect(response.status()).toBe(200);
  const listing = await response.json();
  expect(listing.items.find((item: { id: string }) => item.id === batch.id)).toBeDefined();
  expect(
    (
      await request.get(`/api/books/${book.id}/invoice-imports?limit=20`, {
        headers: headers.employee,
      })
    ).status(),
  ).not.toBe(200);

  await page.goto('/');
  await openAccounting(page);
  await page.getByLabel('搜索企业名称').fill(book.name);
  await page.getByRole('listitem').filter({ hasText: book.name }).click();
  const match = page.getByRole('button', { name: '流水与业务匹配', exact: true });
  await expect(match).toBeVisible({ timeout: 60_000 });
  await match.click();
  await expect(
    page.getByRole('heading', { name: '让一笔业务，对上一笔付款。', exact: true }),
  ).toBeVisible({
    timeout: 60_000,
  });
  const pipeline = page.getByRole('navigation', { name: '做账五步流水线' });
  await expect(pipeline).toBeVisible();
  await expect(pipeline.getByRole('button', { name: '② 匹配与核销', exact: true })).toHaveAttribute(
    'aria-current',
    'step',
  );
  const panel = page.locator('section.invoice-imports');
  await expect(panel).toBeVisible({ timeout: 60_000 });
  await expect(panel).toContainText('重复 1');
  const previewCsv = Buffer.from(
    [HEADERS, invoice(`2499300${suffix}1`)].join('\n') + '\n',
    'utf-8',
  );
  await panel.getByLabel('选择发票导出表').setInputFiles({
    name: `预检发票-${suffix}.csv`,
    mimeType: 'text/csv',
    buffer: previewCsv,
  });
  await panel.getByRole('button', { name: '预检文件', exact: true }).click();
  const precheck = panel.getByLabel('发票导入预检结果');
  await expect(precheck).toContainText('待确认');
  await expect(precheck).toContainText('有效总行数');
  await precheck.getByRole('button', { name: '确认导入并勾稽', exact: true }).click();
  await expect(precheck).toHaveCount(0);
  await expect(panel).toContainText('已导入 1 行');
  const originalBatch = panel.getByRole('button').filter({ hasText: batch.filename }).first();
  await expect(originalBatch).toBeVisible();
  await originalBatch.click();
  await expect(panel.locator('table')).toContainText(`2499200${suffix}1`);
  await expect(panel).toContainText('重复');
  await expect(panel).toContainText('作废');
  await page.screenshot({ path: 'artifacts/invoice-imports-panel.png', fullPage: true });
});

test('未匹配销项发票可生成借贷平衡的应收挂账草稿', async ({ page, request }) => {
  const session = await (await request.get('/api/session?persona=accountant')).json();
  const headers = { Authorization: `Bearer ${session.token}` };
  const suffix = Date.now().toString().slice(-8);
  const companyTax = `91330100ACCRUAL${suffix}`;
  let response = await request.post('/api/companies', {
    headers,
    data: { name: `发票挂账客户-${suffix}`, tax_id: companyTax },
  });
  expect(response.status()).toBe(201);
  const company = await response.json();
  response = await request.post('/api/books', {
    headers,
    data: { name: `发票挂账账套-${suffix}`, period: '2026-10', company_id: company.id },
  });
  expect(response.status()).toBe(201);
  const book = await response.json();
  response = await request.post(`/api/books/${book.id}/catalog/accounts`, {
    headers,
    data: {
      code: '2221.01',
      name: '应交税费—应交增值税（销项税额）',
      category: 'liability',
      normal_balance: 'credit',
      active: true,
      auxiliary_requirements: [],
    },
  });
  expect(response.status()).toBe(201);

  const number = `2499400${suffix}1`;
  const csv = Buffer.from([HEADERS, companyInvoice(number, companyTax)].join('\n') + '\n');
  const uploadId = await uploadInvoices(request, headers.Authorization, csv);
  response = await request.post(`/api/books/${book.id}/invoice-imports`, {
    headers,
    data: { upload_id: uploadId },
  });
  expect(response.status()).toBe(201);

  await page.goto('/');
  await openAccounting(page);
  await page.getByLabel('搜索企业名称').fill(book.name);
  await page.getByRole('listitem').filter({ hasText: book.name }).click();
  await page.getByRole('button', { name: '流水与业务匹配', exact: true }).click();
  const panel = page.locator('section.invoice-imports');
  await expect(panel).toBeVisible({ timeout: 60_000 });
  await panel.getByRole('button').filter({ hasText: '全量发票查询导出表' }).first().click();
  await expect(panel).toContainText(number);
  await panel.getByRole('button', { name: '生成挂账草稿', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: '生成往来挂账草稿', exact: true });
  await expect(dialog).toBeVisible();
  await expect(dialog.getByLabel('应收账款科目')).toHaveValue('1122');
  await expect(dialog.getByLabel('收入科目')).toHaveValue('6001');
  await expect(dialog.getByLabel('销项税额科目')).toHaveValue('2221.01');
  await dialog.getByLabel('处理依据').fill('发票已开具，本月尚未收款，按权责发生制挂应收。');
  await dialog.getByRole('button', { name: '生成挂账草稿', exact: true }).click();
  await expect(dialog).toHaveCount(0);
  await expect(panel).toContainText('已生成未审核往来挂账草稿');
  await expect(panel).toContainText('挂账草稿');

  const refreshed = await (await request.get(`/api/books/${book.id}`, { headers })).json();
  expect(refreshed.sources).toHaveLength(1);
  expect(refreshed.sources[0].direction).toBe('income');
  const draft = refreshed.drafts[0];
  expect(draft.status).toBe('draft');
  const draftDetail = await (
    await request.get(`/api/books/${book.id}/drafts/${draft.id}`, { headers })
  ).json();
  expect(draftDetail.lines.map((line: { debit: string }) => line.debit)).toEqual([
    '1130.00',
    '0.00',
    '0.00',
  ]);
  expect(draftDetail.lines.map((line: { credit: string }) => line.credit)).toEqual([
    '0.00',
    '1000.00',
    '130.00',
  ]);
});
