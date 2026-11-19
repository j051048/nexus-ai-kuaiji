import { expect, test } from '@playwright/test';
import { formalFixture } from './formal-fixtures';
import { openTax, pinPersona } from './n04-tax-fixtures';

test.setTimeout(120_000);
test.use({ actionTimeout: 15_000 });

for (const viewport of [
  { width: 1366, height: 768, name: '1366' },
  { width: 390, height: 844, name: '390' },
]) {
  test(`PLT04-B 规费差异 ${viewport.name} 登记后按政策原因处理`, async ({ page, request }) => {
    const f = await formalFixture(request, true, 'PLT04差异企业', {
      feeVarianceReasons: [
        {
          code: 'bank_fee',
          label: '银行手续费',
          blocks_close: false,
          debit_account: '1002',
          credit_account: '6602.02',
        },
      ],
    });
    await request.put(`/api/books/${f.book.id}/members/local-business-finance`, {
      headers: f.headers,
      data: { role: 'editor' },
    });
    const errors: string[] = [];
    page.on('pageerror', (error) => {
      errors.push(error.message);
      console.log('PAGEERROR', error.message);
    });
    page.on('console', (message) => {
      if (message.type() === 'error') console.log('CONSOLE', message.text());
    });
    await pinPersona(page, 'accountant');
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    await openTax(page, f);
    const panel = page.getByRole('region', { name: '规费差异', exact: true });
    await expect(panel).toBeVisible();
    await panel.locator('summary').filter({ hasText: '登记差异' }).click();
    const form = panel.getByRole('form', { name: '登记规费差异' });
    await form.getByLabel('差异类型').selectOption('unmatched');
    await form.getByLabel('应收金额（元）').fill('0.00');
    await form.getByLabel('实际到账（元）').fill('5.00');
    await form.getByLabel('核查依据').fill('合成银行流水 2026-10-18');
    await form.getByRole('button', { name: '登记差异', exact: true }).click();
    await expect(form.getByRole('status')).toContainText('差异已登记', { timeout: 15_000 });
    const item = panel.locator('li').filter({ hasText: '无对应应收' });
    await expect(item).toContainText('差额 5.00');
    await expect(item).toContainText('待处理');
    await expect(item).toContainText('待查');

    await page.goto('/workspace/home');
    const task = page.locator('article.workspace-task').filter({ hasText: '核对到账差额' }).first();
    await expect(task).toBeVisible();
    await page.screenshot({
      path: `tmp_test/plt04c-task-card-${viewport.name}.png`,
      fullPage: true,
    });
    await task.getByRole('button', { name: /核对到账差额|打开处理任务/ }).click();
    await expect(page.getByRole('button', { name: '税务底稿' })).toHaveAttribute(
      'aria-pressed',
      'true',
    );
    await expect(panel.locator('li.is-target')).toHaveCount(1);

    await item.getByRole('button', { name: '开始处理', exact: true }).click();
    await expect(item).toContainText('处理中');

    const transfer = item.locator('details').filter({ hasText: '转交处理' });
    await transfer.locator('summary').click();
    await transfer.getByRole('combobox').selectOption({ index: 1 });
    await transfer.getByPlaceholder('至少 3 个字').fill('转交财务复核到账事实');
    await transfer.getByRole('button', { name: '确认转交', exact: true }).click();
    await expect(item).toContainText('当前处理人');
    await expect(item).toContainText('处理中');

    await item.getByRole('combobox').first().selectOption('bank_fee');
    await item
      .getByPlaceholder('原件编号、流水号或核查说明', { exact: true })
      .fill('合成银行流水与客户说明已人工核对，作为手续费处理');
    await item.getByRole('button', { name: '保存处理', exact: true }).click();
    await expect(item).toContainText('已处理');
    await expect(item).toContainText('银行手续费');
    await expect(panel.getByRole('alert')).toHaveCount(0);

    await page.screenshot({
      path: `tmp_test/plt04b-fee-variance-${viewport.name}.png`,
      fullPage: true,
    });
    const draft = item.getByRole('button', { name: '打开差异草稿', exact: true });
    await expect(draft).toBeVisible();
    await draft.click();
    await expect(page.getByText('原件与凭证，一起核对', { exact: true })).toBeVisible();
    expect(errors).toEqual([]);
  });
}
