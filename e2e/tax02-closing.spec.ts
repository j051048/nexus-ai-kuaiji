import { expect, test } from '@playwright/test';
import { checked, formalFixture } from './formal-books-fixtures';

for (const viewport of [
  { width: 1365, height: 900 },
  { width: 390, height: 844 },
]) {
  test(`TAX02 ${viewport.width} 生成双向损益结转草稿并保留辅助`, async ({ page, request }) => {
    test.setTimeout(120_000);
    const f = await formalFixture(request, false, 'TAX02结转');
    const ledger = await checked(
      await request.get(`/api/books/${f.book.id}/ledger`, { headers: f.headers }),
      200,
    );
    const accounts: Record<string, any> = {};
    for (const item of [
      { code: '3103', name: '本年利润', category: 'equity', normal_balance: 'credit' },
      { code: '500101', name: '主营业务收入', category: 'income', normal_balance: 'credit' },
    ]) {
      accounts[item.code] = await checked(
        await request.post('/api/opening/accounts', {
          headers: f.headers,
          data: {
            ...item,
            ledger_id: ledger.id,
            effective_from: '2026-01',
            effective_to: '2026-12',
            reason: 'TAX02 真实浏览器结转科目',
          },
        }),
      );
    }
    accounts['6602.02'] = await checked(
      await request.post('/api/opening/accounts', {
        headers: f.headers,
        data: {
          ledger_id: ledger.id,
          code: '6602.02',
          name: '管理费用—办公费',
          category: 'expense',
          normal_balance: 'debit',
          effective_from: '2026-01',
          effective_to: '2026-12',
          reason: 'TAX02 真实浏览器结转科目',
        },
      }),
    );
    const voucher = async (key: string, lines: any[]) =>
      checked(
        await request.post('/api/posting/manual-requests', {
          headers: f.headers,
          data: {
            book_id: f.book.id,
            command_key: key,
            voucher_date: '2026-10-31',
            description: 'TAX02 结转前余额',
            reason: '真实浏览器结转夹具',
            lines,
          },
        }),
      );
    for (const [key, lines] of [
      [
        'tax02-browser-income',
        [
          { account_id: f.accounts[0].id, account_version: 1, debit: '100.00', credit: '0.00' },
          {
            account_id: accounts['500101'].id,
            account_version: 1,
            debit: '0.00',
            credit: '100.00',
            auxiliary_values: { project: 'P1' },
          },
        ],
      ],
      [
        'tax02-browser-expense',
        [
          { account_id: f.accounts[0].id, account_version: 1, debit: '10.00', credit: '0.00' },
          {
            account_id: accounts['6602.02'].id,
            account_version: 1,
            debit: '0.00',
            credit: '10.00',
            auxiliary_values: { project: 'P1' },
          },
        ],
      ],
    ] as const) {
      const prepared = await voucher(key, lines as any);
      const approved = await checked(
        await request.post(`/api/posting/manual-requests/${prepared.request.id}/approve`, {
          headers: f.reviewer,
          data: {
            expected_version: prepared.request.version,
            expected_policy_version: prepared.request.policy_version,
            reason: '独立复核真实浏览器结转夹具',
          },
        }),
        200,
      );
      await checked(
        await request.post(`/api/posting/manual-requests/${prepared.request.id}/post`, {
          headers: f.headers,
          data: { expected_version: approved.request.version, command_key: key },
        }),
      );
    }

    await page.setViewportSize(viewport);
    await page.goto(
      `/workspace/formal-books?book=${f.book.id}&company=${f.company.id}&period=${f.book.period}`,
    );
    await page.getByText('往来与期末办理（账龄、资产及计提草稿）', { exact: true }).click();
    const panel = page.getByRole('region', { name: '往来与期末办理', exact: true });
    await panel.getByRole('button', { name: '资产与期末草稿', exact: true }).click();
    await expect(panel.getByRole('region', { name: '期末结转', exact: true })).toBeVisible();
    await panel.getByLabel('本年利润科目').selectOption(accounts['3103'].id);
    await panel.getByRole('button', { name: '生成期末结转草稿', exact: true }).click();
    await expect(panel.getByText('已完成本次操作，请核对下方最新记录。')).toBeVisible();
    await expect(panel).toContainText('110.00', { timeout: 15000 });
    await page.screenshot({
      path: `tmp_test/tax02-browser-${viewport.width}.png`,
      fullPage: true,
    });
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(
      true,
    );
  });
}
