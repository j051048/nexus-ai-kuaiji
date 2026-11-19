import { expect, test } from '@playwright/test';
import { choosePersona } from './ui-navigation';
import { checked, formalFixture } from './formal-fixtures';

test.use({ viewport: { width: 1366, height: 768 } });

async function source(
  request: Parameters<typeof checked>[0] extends never ? never : any,
  bookId: string,
  headers: Record<string, string>,
  data: Record<string, unknown>,
) {
  return checked(await request.post(`/api/books/${bookId}/imports`, { headers, data }), 200);
}

test('助手从平台结算单与银行到账核对 350.00 元差额、原因、依据和入口', async ({
  page,
  request,
}) => {
  const f = await formalFixture(request, true, 'PLT08 结算差额');
  await source(request, f.book.id, f.headers, {
    source_name: '虚构平台结算单',
    rows: [
      {
        external_id: 'settlement-plt08',
        date: '2026-10-10',
        description: '平台结算单，含服务费前金额',
        counterparty: '虚构出行平台',
        amount: '100000.00',
        direction: 'income',
        evidence_ref: '虚构平台结算单原件 PLT08-SETTLE',
      },
    ],
  });
  await source(request, f.book.id, f.headers, {
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
    ],
  });
  await source(request, f.book.id, f.headers, {
    source_name: '虚构平台手续费明细',
    rows: [
      {
        external_id: 'fee-plt08',
        date: '2026-10-10',
        description: '平台服务手续费',
        counterparty: '虚构出行平台',
        amount: '350.00',
        direction: 'expense',
        evidence_ref: '虚构手续费明细 PLT08-FEE',
      },
    ],
  });
  const book = await (await request.get(`/api/books/${f.book.id}`, { headers: f.headers })).json();
  const settlement = book.sources.find(
    (row: any) => row.description === '平台结算单，含服务费前金额',
  );
  const bank = book.sources.find((row: any) => row.description === '平台结算款到账');
  const fee = book.sources.find((row: any) => row.description === '平台服务手续费');
  expect(settlement?.amount).toBe('100000.00');
  expect(bank?.amount).toBe('99650.00');
  expect(fee?.amount).toBe('350.00');

  await page.goto(`/workspace/accounting?book=${encodeURIComponent(f.book.id)}`);
  await choosePersona(page, 'accountant');
  const panel = page.locator('details.analysis-evidence');
  await expect(panel).toBeVisible({ timeout: 30_000 });
  await panel.locator('summary').click();
  await panel.getByLabel('核对类型').selectOption('settlement_difference');
  await panel.getByLabel('结算单来源 ID').fill(settlement.id);
  await panel.getByLabel('银行到账来源 ID').fill(bank.id);
  await panel.getByRole('button', { name: '开始只读核对' }).click();
  await expect(panel).toContainText('结算单 100000.00 元');
  await expect(panel).toContainText('银行到账 99650.00 元');
  await expect(panel).toContainText('差额 350.00 元');
  await expect(panel).toContainText('原因待核对');
  await expect(panel).toContainText('手续费或扣款明细');
  await expect(panel).toContainText('虚构平台结算单');
  await expect(panel).toContainText('虚构银行到账流水');
  await expect(panel).toContainText('会计');
  await expect(panel.getByRole('link', { name: '打开账套原记录核对' })).toHaveAttribute(
    'href',
    /source=bank-plt08|source=/,
  );
  await panel.getByText('核对依据', { exact: true }).click();
  await expect(panel).toContainText(/版本 [0-9a-f]{12}/);
  await page.screenshot({
    path: 'tmp_test/plt08-settlement-difference-partial-1366.png',
    fullPage: true,
  });

  await panel.getByLabel('手续费明细 ID（可选）').fill(fee.id);
  await panel.getByRole('button', { name: '开始只读核对' }).click();
  await expect(panel).toContainText('已由手续费明细 350.00 元解释');
  await expect(panel).toContainText('虚构手续费明细');
  await expect(panel).toContainText('当前范围已核对');
  if (!(await panel.getByText('证据摘要：', { exact: false }).isVisible())) {
    await panel.getByText('核对依据', { exact: true }).click();
  }
  await page.screenshot({ path: 'tmp_test/plt08-settlement-difference-1366.png', fullPage: true });

  await page.setViewportSize({ width: 390, height: 844 });
  await expect(panel).toContainText('已由手续费明细 350.00 元解释');
  await expect(
    page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1),
  ).resolves.toBe(true);
  await page.screenshot({ path: 'tmp_test/plt08-settlement-difference-390.png', fullPage: true });

  await page.setViewportSize({ width: 1366, height: 768 });
  const recordLink = panel.getByRole('link', { name: '打开账套原记录核对' });
  const target = new URL((await recordLink.getAttribute('href')) ?? '', page.url());
  expect(target.searchParams.get('book')).toBe(f.book.id);
  expect(target.searchParams.get('source')).toBe(bank.id);
  await recordLink.click();
  await expect(page).toHaveURL(new RegExp(`book=${f.book.id}`));
  await expect(page).toHaveURL(new RegExp(`source=${bank.id}`));
  await expect(page.getByText('PLT08 结算差额', { exact: false }).first()).toBeVisible();
});
