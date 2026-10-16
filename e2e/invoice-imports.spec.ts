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
  const panel = page.locator('section.invoice-imports');
  await expect(panel).toBeVisible({ timeout: 60_000 });
  await expect(panel).toContainText('重复 1');
  await panel.getByRole('button').filter({ hasText: '全量发票查询导出表' }).first().click();
  await expect(panel.locator('table')).toContainText(`2499200${suffix}1`);
  await expect(panel).toContainText('重复');
  await expect(panel).toContainText('作废');
  await page.screenshot({ path: 'artifacts/invoice-imports-panel.png', fullPage: true });
});
