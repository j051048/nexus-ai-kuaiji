import { expect, type APIRequestContext, type Page } from '@playwright/test';
import { createHash } from 'node:crypto';
import { checked, formalFixture, reviewedManual } from './formal-fixtures';

export async function taxFixture(request: APIRequestContext) {
  return formalFixture(request, false, 'N04税务虚构企业');
}

export async function taxFullFixture(request: APIRequestContext) {
  const f = await taxFixture(request);
  const info = await checked(
    await request.get(`/api/admin/companies/${f.company.id}`, { headers: f.headers }),
    200,
  );
  const taxId = `913301N04${crypto.randomUUID().replaceAll('-', '').slice(0, 10).toUpperCase()}`;
  await checked(
    await request.patch(`/api/admin/companies/${f.company.id}`, {
      headers: f.headers,
      data: {
        name: f.company.name,
        tax_id: taxId,
        expected_version: info.company.version,
        reason: '本地虚构发票确定所属企业',
      },
    }),
    200,
  );
  const vat = await checked(
    await request.post('/api/opening/accounts', {
      headers: f.headers,
      data: {
        ledger_id: f.ledger.id,
        code: '222101',
        name: '虚构应交增值税',
        category: 'liability',
        normal_balance: 'credit',
        effective_from: '2026-01',
        effective_to: '2026-12',
        reason: '税务界面真实正式科目映射',
      },
    }),
  );
  const revenue = await checked(
    await request.post('/api/opening/accounts', {
      headers: f.headers,
      data: {
        ledger_id: f.ledger.id,
        code: '6001',
        name: '虚构主营业务收入',
        category: 'income',
        normal_balance: 'credit',
        effective_from: '2026-01',
        effective_to: '2026-12',
        reason: '税务界面真实正式科目映射',
      },
    }),
  );
  await reviewedManual(request, f.headers, f.reviewer, {
    book_id: f.book.id,
    command_key: `n04-tax-sales-${crypto.randomUUID()}`,
    voucher_date: '2026-10-16',
    description: '虚构销售业务，应税额与账面暂有差异',
    reason: '独立审核后记账，差异在税务底稿另行人工说明',
    lines: [
      { account_id: f.accounts[0].id, account_version: 1, debit: '1025.00', credit: '0.00' },
      { account_id: revenue.id, account_version: 1, debit: '0.00', credit: '1000.00' },
      { account_id: vat.id, account_version: 1, debit: '0.00', credit: '25.00' },
    ],
  });
  const number = `0000${BigInt(`0x${crypto.randomUUID().replaceAll('-', '').slice(0, 12)}`)
    .toString()
    .padStart(15, '0')}`;
  const content = Buffer.from(
    [
      '发票种类,发票代码,发票号码,开票日期,销方名称,销方纳税人识别号,购方名称,购方纳税人识别号,金额,税额,价税合计,发票状态',
      [
        '数电票（增值税普通发票）',
        '',
        number,
        '2026-10-16',
        f.company.name,
        taxId,
        '虚构客户',
        '913301N04CUSTOMER001',
        '1000.00',
        '30.00',
        '1030.00',
        '正常',
      ].join(','),
      '',
    ].join('\n'),
    'utf-8',
  );
  const upload = await checked(
    await request.post('/api/uploads', {
      headers: f.headers,
      data: {
        purpose: 'invoice_import',
        filename: 'N04虚构销项发票.csv',
        media_type: 'text/csv',
        size_bytes: content.length,
        sha256: createHash('sha256').update(content).digest('hex'),
      },
    }),
  );
  await checked(
    await request.put(upload.url, {
      headers: { ...f.headers, 'Content-Type': 'text/csv' },
      data: content,
    }),
    200,
  );
  await checked(
    await request.post(`/api/uploads/${upload.id}/complete`, { headers: f.headers }),
    200,
  );
  const imported = await checked(
    await request.post(`/api/books/${f.book.id}/invoice-imports`, {
      headers: f.headers,
      data: { upload_id: upload.id },
    }),
  );
  expect(imported.row_count).toBe(1);
  return { ...f, vat, revenue, accounts: [...f.accounts, vat, revenue], invoiceNumber: number };
}

export async function taxProfile(
  request: APIRequestContext,
  f: Awaited<ReturnType<typeof taxFixture>>,
) {
  return checked(
    await request.put('/api/tax/profile', {
      headers: f.headers,
      data: {
        book_id: f.book.id,
        taxpayer_type: 'small_scale',
        filing_cycle: 'monthly',
        vat_rate_bp: 300,
        surtax_rate_bp: 1200,
        effective_from: '2026-10',
        reason: '仅本地虚构销售业务的界面验收税率，不用于真实申报',
      },
    }),
    200,
  );
}

export async function taxPaper(
  request: APIRequestContext,
  f: Awaited<ReturnType<typeof taxFixture>>,
  reuseProfile = false,
) {
  if (!reuseProfile) await taxProfile(request, f);
  return checked(
    await request.post('/api/tax/workpapers', {
      headers: f.headers,
      data: {
        book_id: f.book.id,
        period: '2026-10',
        reason: '本地真实接口准备未复核的税务底稿',
        vat_accounts: f.accounts.map((account) => ({
          account_id: account.id,
          account_version: 1,
          role: account.code === '222101' ? 'vat_payable' : 'excluded',
        })),
      },
    }),
  );
}

export async function reviewTaxPaper(
  request: APIRequestContext,
  f: Awaited<ReturnType<typeof taxFixture>>,
  paper: { id: string; version: number },
) {
  return checked(
    await request.post(`/api/tax/workpapers/${paper.id}/review`, {
      headers: f.reviewer,
      data: {
        book_id: f.book.id,
        expected_version: paper.version,
        reason: '独立人员核验本地虚构票账差异与底稿来源',
      },
    }),
    200,
  );
}

export async function changeFormalSource(
  request: APIRequestContext,
  f: Awaited<ReturnType<typeof taxFixture>>,
) {
  return reviewedManual(request, f.headers, f.reviewer, {
    book_id: f.book.id,
    command_key: `n04-change-${crypto.randomUUID()}`,
    voucher_date: '2026-10-20',
    description: 'N04底稿后发生新的正式账务事实',
    reason: '用真实独立审核凭证触发来源版本失效',
    lines: f.accounts.slice(0, 2).map((account, index) => ({
      account_id: account.id,
      account_version: 1,
      debit: index === 0 ? '20.00' : '0.00',
      credit: index === 1 ? '20.00' : '0.00',
    })),
  });
}

export async function pinPersona(page: Page, persona: string) {
  await page.route('**/api/session', async (route) => {
    const url = new URL(route.request().url());
    url.searchParams.set('persona', persona);
    await route.fulfill({ response: await route.fetch({ url: url.toString() }) });
  });
}

export function deliveryUrl(f: Awaited<ReturnType<typeof taxFixture>>) {
  return `/workspace/delivery?book=${f.book.id}&company=${f.company.id}&period=2026-10&search=${encodeURIComponent(f.tag)}`;
}

export async function openTax(page: Page, f: Awaited<ReturnType<typeof taxFixture>>) {
  await page.goto(deliveryUrl(f));
  await page.getByRole('button', { name: '税务底稿', exact: true }).click();
  const panel = page.getByRole('region', { name: '税务底稿', exact: true });
  await expect(panel).toBeVisible();
  return panel;
}
