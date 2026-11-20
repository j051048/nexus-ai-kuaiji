import { expect, test, type APIRequestContext } from '@playwright/test';
import { readFileSync } from 'node:fs';
import { choosePersona } from './ui-navigation';
import { checked } from './formal-fixtures';
import type { BusinessContext } from '../src/business-types';

test.use({ viewport: { width: 1366, height: 768 } });

const sampleImage = readFileSync(new URL('./fixtures/v03-receipt.png', import.meta.url));

async function personaHeaders(request: APIRequestContext, persona: string) {
  const res = await request.get(`/api/session?persona=${persona}`);
  expect(res.ok()).toBe(true);
  return { Authorization: `Bearer ${(await res.json()).token}` };
}

async function createBusinessSource(
  request: APIRequestContext,
  params: {
    companyId: string;
    departmentId: string;
    bookId: string;
    employee: Record<string, string>;
    department: Record<string, string>;
    finance: Record<string, string>;
    description: string;
    amount: string;
    direction: 'income' | 'expense';
    occurredOn: string;
    attachmentName: string;
  },
) {
  let event = await checked(
    await request.post('/api/business/events', {
      headers: params.employee,
      data: {
        company_id: params.companyId,
        department_id: params.departmentId,
        template_id: 'daily_cashflow',
        payload: {
          occurred_on: params.occurredOn,
          description: params.description,
          counterparty: '虚构出行平台',
          amount: params.amount,
          direction: params.direction,
          payment_status: 'paid',
          category: params.direction === 'income' ? 'other' : 'office',
        },
      },
    }),
    201,
  );
  const route = `/api/business/events/${event.id}`;
  event = await checked(
    await request.post(`${route}/attachments?expected_version=${event.version}`, {
      headers: params.employee,
      multipart: {
        file: { name: params.attachmentName, mimeType: 'image/png', buffer: sampleImage },
      },
    }),
    200,
  );
  for (const [action, headers] of [
    ['submit', params.employee],
    ['department-approve', params.department],
    ['finance-approve', params.finance],
  ] as const) {
    event = await checked(
      await request.post(`${route}/${action}`, {
        headers,
        data: { expected_version: event.version },
      }),
      200,
    );
  }
  const receipt = await checked(
    await request.post(`${route}/handoff`, {
      headers: params.finance,
      data: {
        expected_version: event.version,
        book_id: params.bookId,
        idempotency_key: `plt08-handoff-${event.id}`,
      },
    }),
    200,
  );
  return { id: receipt.source_id as string, amount: params.amount, description: params.description };
}

test('助手从平台结算单与银行到账核对对平、缺件、冲突、未核验关系与确认解释（桌面+手机）', async ({
  page,
  request,
}) => {
  test.setTimeout(180_000);
  const employee = await personaHeaders(request, 'employee');
  const department = await personaHeaders(request, 'department');
  const finance = await personaHeaders(request, 'finance');
  const accountant = await personaHeaders(request, 'accountant');

  const context: BusinessContext = await (
    await request.get('/api/business/context', { headers: employee })
  ).json();
  const company = context.companies.find((item) => item.can_submit && item.department_id)!;
  expect(company).toBeTruthy();

  const book = await checked(
    await request.post('/api/books', {
      headers: accountant,
      data: {
        name: `PLT08 结算差额全态 ${Date.now()}`,
        company_id: company.id,
        period: '2026-10',
      },
    }),
    201,
  );
  await checked(
    await request.put(`/api/books/${book.id}/members/local-business-finance`, {
      headers: accountant,
      data: { role: 'editor' },
    }),
    200,
  );

  // 1) 真实业务单据移交进账套（source_kind='business' 且自带原件）
  const common = {
    companyId: company.id,
    departmentId: company.department_id!,
    bookId: book.id,
    employee,
    department,
    finance,
  };
  const settlement = await createBusinessSource(request, {
    ...common,
    description: '平台结算单，含服务费前金额',
    amount: '100000.00',
    direction: 'income',
    occurredOn: '2026-10-10',
    attachmentName: '虚构平台结算单原件.png',
  });
  const settlementBal = await createBusinessSource(request, {
    ...common,
    description: '平台对平结算单',
    amount: '50000.00',
    direction: 'income',
    occurredOn: '2026-10-12',
    attachmentName: '虚构对平结算单原件.png',
  });
  const fee = await createBusinessSource(request, {
    ...common,
    description: '平台服务手续费',
    amount: '350.00',
    direction: 'expense',
    occurredOn: '2026-10-10',
    attachmentName: '虚构平台手续费明细.png',
  });
  const feeMismatch = await createBusinessSource(request, {
    ...common,
    description: '金额不符手续费',
    amount: '340.00',
    direction: 'expense',
    occurredOn: '2026-10-10',
    attachmentName: '虚构不符手续费明细.png',
  });

  // 2) 真实银行流水导入（source_kind='bank'）
  await checked(
    await request.post(`/api/books/${book.id}/imports`, {
      headers: accountant,
      data: {
        source_name: '虚构银行到账流水',
        namespace: 'plt08-bank',
        bank_account: '虚构银行-8899',
        rows: [
          {
            external_id: 'bank-plt08',
            date: '2026-10-10',
            description: '平台结算款到账',
            counterparty: '虚构出行平台',
            amount: '99650.00',
            direction: 'income',
            evidence_ref: '虚构银行回单 PLT08-BANK',
            stable_transaction_id: 'plt08-bank-001',
          },
          {
            external_id: 'bank-plt08-balanced',
            date: '2026-10-12',
            description: '平台对平结算款到账',
            counterparty: '虚构出行平台',
            amount: '50000.00',
            direction: 'income',
            evidence_ref: '虚构银行回单 PLT08-BAL-B',
            stable_transaction_id: 'plt08-bank-002',
          },
        ],
      },
    }),
    200,
  );
  // 期间防线：跨期流水不得直接导入当期账套（422）
  const crossPeriodImport = await request.post(`/api/books/${book.id}/imports`, {
    headers: accountant,
    data: {
      source_name: '虚构上月手续费',
      rows: [
        {
          external_id: 'fee-plt08-prev',
          date: '2026-09-30',
          description: '上月平台手续费',
          counterparty: '虚构出行平台',
          amount: '350.00',
          direction: 'expense',
          evidence_ref: '虚构上月手续费 PLT08-FEE-PREV',
        },
      ],
    },
  });
  expect(crossPeriodImport.status()).toBe(422);

  const bookView = await (await request.get(`/api/books/${book.id}`, { headers: accountant })).json();
  const byDesc = (desc: string) => bookView.sources.find((row: any) => row.description === desc);
  const bank = byDesc('平台结算款到账');
  const bankBal = byDesc('平台对平结算款到账');
  expect(bank?.amount).toBe('99650.00');
  expect(bankBal?.amount).toBe('50000.00');

  await page.goto(`/workspace/accounting?book=${encodeURIComponent(book.id)}`);
  await choosePersona(page, 'accountant');
  const panel = page.locator('details.analysis-evidence');
  await expect(panel).toBeVisible({ timeout: 30_000 });
  await panel.locator('summary').click();
  await panel.getByLabel('核对类型').selectOption('settlement_difference');

  // A) 对平状态：显示已对平、不得误提示缺少手续费明细
  await panel.getByLabel('结算单来源 ID').fill(settlementBal.id);
  await panel.getByLabel('银行到账来源 ID').fill(bankBal.id);
  await panel.getByLabel('手续费明细 ID（可选）').fill('');
  await panel.getByRole('button', { name: '开始只读核对' }).click();
  await expect(panel).toContainText('已对平');
  await expect(panel).toContainText('当前范围已核对');
  await expect(panel).not.toContainText('缺少手续费或扣款明细');

  // B) 确实缺件状态：350.00 元差额，无手续费明细
  await panel.getByLabel('结算单来源 ID').fill(settlement.id);
  await panel.getByLabel('银行到账来源 ID').fill(bank.id);
  await panel.getByLabel('手续费明细 ID（可选）').fill('');
  await panel.getByRole('button', { name: '开始只读核对' }).click();
  await expect(panel).toContainText('结算单 100000.00 元');
  await expect(panel).toContainText('银行到账 99650.00 元');
  await expect(panel).toContainText('差额 350.00 元');
  await expect(panel).toContainText('原因待核对');
  await expect(panel).toContainText('手续费或扣款明细');
  await expect(panel.getByRole('link', { name: '打开账套原记录核对' })).toHaveAttribute(
    'href',
    /source=/,
  );
  await panel.getByText('核对依据', { exact: true }).click();
  await expect(panel).toContainText(/版本 [0-9a-f]{12}/);

  // C) 金额冲突：同时展示 340.00 元与 350.00 元金额冲突/两笔金额不一致
  await panel.getByLabel('手续费明细 ID（可选）').fill(feeMismatch.id);
  await panel.getByRole('button', { name: '开始只读核对' }).click();
  await expect(panel).toContainText('340.00');
  await expect(panel).toContainText('350.00');
  await expect(panel).toContainText('金额冲突');
  await expect(panel).toContainText('两笔金额不一致');

  // D) 未核验来源关系：同月同额 350.00 但尚未核验三者关系，不能直接宣称已解释
  await panel.getByLabel('手续费明细 ID（可选）').fill(fee.id);
  await panel.getByRole('button', { name: '开始只读核对' }).click();
  await expect(panel).toContainText('来源关系');
  await expect(panel).not.toContainText('当前范围已核对');

  // E) 经真实规费差异核验绑定结算单/到账流水/手续费三者关系后，助手确认解释
  const verified = await request.post(`/api/tax/books/${book.id}/fee-variances`, {
    headers: accountant,
    data: {
      kind: 'underpaid',
      expected_cents: 10000000,
      actual_cents: 9965000,
      settlement_source_id: settlement.id,
      bank_source_id: bank.id,
      fee_source_id: fee.id,
      evidence_ref: '虚构手续费明细 PLT08-FEE',
      idempotency_key: crypto.randomUUID(),
    },
  });
  expect(verified.status(), await verified.text()).toBe(201);

  await panel.getByRole('button', { name: '开始只读核对' }).click();
  await expect(panel).toContainText('已由手续费明细 350.00 元解释');
  await expect(panel).toContainText('当前范围已核对');

  // F) 手机 390px 视角零横向溢出
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(panel).toContainText('已由手续费明细 350.00 元解释');
  await expect(
    page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1),
  ).resolves.toBe(true);

  // G) 跳回原记录核对
  await page.setViewportSize({ width: 1366, height: 768 });
  const recordLink = panel.getByRole('link', { name: '打开账套原记录核对' });
  const target = new URL((await recordLink.getAttribute('href')) ?? '', page.url());
  expect(target.searchParams.get('book')).toBe(book.id);
  expect(target.searchParams.get('source')).toBe(bank.id);
  await recordLink.click();
  await expect(page).toHaveURL(new RegExp(`book=${book.id}`));
  await expect(page).toHaveURL(new RegExp(`source=${bank.id}`));
});
