import { expect, test, type Page } from '@playwright/test';
import { mkdirSync } from 'node:fs';
import { checked, formalFixture } from './formal-books-fixtures';
import { choosePersona, openAccounting, openBookTools } from './ui-navigation';

const output = 'tmp_test/finance-agent/20261107-kernel-reuse/screenshots';
async function open(page: Page, book: { id: string; period: string }, company: { id: string }) {
  await page.goto(
    `/workspace/formal-books?book=${book.id}&company=${company.id}&period=${book.period}`,
  );
  await page.getByText('往来与期末办理（账龄、资产及计提草稿）', { exact: true }).click();
  return page.getByRole('region', { name: '往来与期末办理', exact: true });
}
async function shot(page: Page, name: string) {
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(
    true,
  );
  mkdirSync(output, { recursive: true });
  await page.screenshot({ path: `${output}/${name}.png`, fullPage: true });
}
for (const width of [1365, 390]) {
  test(`财务改良 ${width} 资产建卡到独立复核记账及尾差`, async ({ page, request }) => {
    test.setTimeout(120000);
    await page.setViewportSize({ width, height: 844 });
    const f = await formalFixture(request, true, '财务改良');
    const ledger = await checked(
      await request.get(`/api/books/${f.book.id}/ledger`, { headers: f.headers }),
      200,
    );
    for (const data of [
      { code: '6602', name: '折旧费用', category: 'expense', normal_balance: 'debit' },
      { code: '1602', name: '累计折旧', category: 'asset', normal_balance: 'credit' },
    ])
      await checked(
        await request.post('/api/opening/accounts', {
          headers: f.headers,
          data: {
            ...data,
            ledger_id: ledger.id,
            effective_from: '2026-01',
            reason: '合成本地科目',
          },
        }),
      );
    let panel = await open(page, f.book, f.company);
    await expect(panel.getByText('0.00 元')).toHaveCount(5);
    await panel.getByRole('button', { name: '资产与期末草稿', exact: true }).click();
    await panel.getByText('登记资产卡片', { exact: true }).click();
    await panel.getByLabel('资产名称', { exact: true }).fill('尾差验证设备');
    await panel.getByLabel('原值（元）', { exact: true }).fill('100.00');
    await panel.getByLabel('使用月数').fill('3');
    await panel.getByLabel('开始计提月份').fill('2026-08');
    await panel
      .getByRole('combobox', { name: '费用科目', exact: true })
      .selectOption({ label: '6602 折旧费用 · v1' });
    await panel
      .getByRole('combobox', { name: '累计折旧／摊销科目', exact: true })
      .selectOption({ label: '1602 累计折旧 · v1' });
    await panel.getByLabel('登记依据与理由').fill('虚构资料，尾差到期核对');
    await panel.getByRole('button', { name: '保存资产卡片' }).click();
    await expect(panel.getByRole('heading', { name: '尾差验证设备', exact: true })).toBeVisible();
    await panel.getByRole('button', { name: '生成或查看本期草稿' }).click();
    await expect(panel.getByText('本版本由你制单，请交另一位有复核权限的会计审核。')).toBeVisible();
    await expect(panel.getByRole('button', { name: '核对分录后复核' })).toHaveCount(0);
    const lines = panel.getByRole('region', { name: '折旧：尾差验证设备分录' });
    await expect(lines.getByRole('cell', { name: '100.00', exact: true })).toHaveCount(2);
    await shot(page, `maker-${width}`);
    await choosePersona(page, 'reviewer');
    // A full reload deliberately resets the local demo persona. Use the real
    // navigation to retain the separately authenticated reviewer's session.
    await openAccounting(page);
    await page.getByLabel('搜索企业名称').first().fill(f.tag);
    await page.getByRole('listitem').filter({ hasText: f.tag }).click();
    await openBookTools(page);
    await page.getByRole('button', { name: '正式账簿', exact: true }).click();
    await page.getByText('往来与期末办理（账龄、资产及计提草稿）', { exact: true }).click();
    panel = page.getByRole('region', { name: '往来与期末办理', exact: true });
    await panel.getByRole('button', { name: '资产与期末草稿', exact: true }).click();
    await panel.getByLabel('办理理由').fill('独立核对原值残值及尾差');
    await panel.getByRole('button', { name: '核对分录后复核' }).click();
    await expect(panel.getByText(/已复核，待记账/)).toBeVisible();
    await panel.getByLabel('办理理由').fill('已核对独立复核后的分录');
    await panel.getByRole('button', { name: '准备正式记账' }).click();
    await panel.getByRole('button', { name: '确认正式记账', exact: true }).click();
    await expect(panel.getByText(/已正式记账 · 版本/)).toBeVisible();
    await expect(panel.getByText(/已记账计提 100.00 元/)).toBeVisible();
    // A focused task hides the books until the user returns; retain the same
    // scope and verify the posted result through the actual return action.
    const postedScope = page.url();
    await page.getByRole('button', { name: '返回账簿（保留筛选）', exact: true }).click();
    await expect(page).toHaveURL(postedScope);
    await expect(page.getByRole('navigation', { name: '账簿类型', exact: true })).toBeVisible();
    await expect(page.getByRole('region', { name: '科目余额表', exact: true })).toContainText(
      '折旧费用',
    );
    const result = await checked(
      await request.get(`/api/periodic/workspace?book_id=${f.book.id}`, { headers: f.headers }),
      200,
    );
    expect(result.drafts).toHaveLength(1);
    expect(result.drafts[0].draft.created_by).not.toBe(result.drafts[0].draft.approved_by);
    expect(result.drafts[0].draft.posted_voucher_id).toBeTruthy();
    await shot(page, `posted-${width}`);
  });
  test(`财务改良 ${width} 错范围及撤权清空可办理内容`, async ({ page, request }) => {
    test.setTimeout(90000);
    await page.setViewportSize({ width, height: 844 });
    const f = await formalFixture(request, true, '财务边界');
    await page.route('**/api/subledger/aging?**', async (route) => {
      const response = await route.fetch();
      const value = await response.json();
      await route.fulfill({ json: { ...value, scope: { ...value.scope, company_id: 'foreign' } } });
    });
    const panel = await open(page, f.book, f.company);
    await expect(panel.getByRole('alert')).toContainText('数据或范围不一致');
    await expect(panel.locator('.finance-aging-summary')).toHaveCount(0);
    await panel.getByRole('button', { name: '资产与期末草稿', exact: true }).click();
    await expect(panel.getByText('登记资产卡片', { exact: true })).toBeVisible();
    await page.route('**/api/periodic/workspace?**', (route) =>
      route.fulfill({ status: 403, json: { detail: '权限已撤销' } }),
    );
    await panel.getByRole('button', { name: '重新读取办理结果' }).click();
    // The global authorization handler unmounts this entire scope first.
    await expect(
      page
        .getByRole('region', { name: '当前登录身份', exact: true })
        .getByText('权限待重新确认', { exact: true }),
    ).toBeVisible();
    await expect(panel).toHaveCount(0);
    await expect(page.getByRole('button', { name: '保存资产卡片' })).toHaveCount(0);
    await shot(page, `revoked-${width}`);
  });
}
