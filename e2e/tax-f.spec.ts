import { expect, test } from '@playwright/test';
import { checked } from './formal-fixtures';
import { openTax, taxFullFixture } from './n04-tax-fixtures';

async function prepare(request: Parameters<typeof taxFullFixture>[0], f: Awaited<ReturnType<typeof taxFullFixture>>) {
  await checked(
    await request.post('/api/opening/accounts', {
      headers: f.headers,
      data: {
        ledger_id: f.ledger.id,
        code: '2203',
        name: '虚构预收租赁款',
        category: 'liability',
        normal_balance: 'credit',
        effective_from: '2026-01',
        effective_to: '2026-12',
        reason: '租赁预收与收入分离验收',
      },
    }),
  );
}

for (const viewport of [
  { width: 1280, height: 1000 },
  { width: 390, height: 844 },
]) {
  test(`TAX13-15 lease, tax basis and profit drilldown work in the browser ${viewport.width}`, async ({
    page,
    request,
  }) => {
    test.setTimeout(120_000);
    const f = await taxFullFixture(request);
    await prepare(request, f);
    await page.setViewportSize(viewport);
    await openTax(page, f);

    const panel = page.locator('section[aria-label="租赁、税费与利润下钻"]');
    await panel.scrollIntoViewIfNeeded();
    await expect(panel.getByRole('heading', { name: '租赁、税费与利润下钻' })).toBeVisible();

    await panel.getByLabel('客户', { exact: true }).fill('虚构租赁客户-界面');
    await panel.getByLabel('车辆外部编号', { exact: true }).fill('V-LEASE-UI');
    await panel.getByLabel('开始月', { exact: true }).fill(f.book.period);
    await panel.getByLabel('结束月', { exact: true }).fill(f.book.period);
    await panel.getByLabel('月租', { exact: true }).fill('1000.00');
    await panel.getByLabel('预收科目', { exact: true }).fill('2241');
    await panel.getByRole('button', { name: '登记租赁合同' }).click();
    await expect(panel.getByText('租赁合同已登记，收款与收入分别确认后才生成凭证。')).toBeVisible();
    await expect(panel.getByRole('button', { name: /虚构租赁客户-界面/ })).toBeVisible();

    await panel.getByRole('button', { name: '确认本期业务' }).click();
    await expect(panel.getByText('业务确认已登记。')).toBeVisible();
    await panel.getByRole('button', { name: '确认本期收款' }).click();
    await expect(panel.getByText('收款事实已登记。')).toBeVisible();
    await panel.getByRole('button', { name: '确认本期收入' }).click();
    await expect(panel.getByText('当期租赁收入已生成草稿；复核后导出。')).toBeVisible();
    await expect(panel.getByText('已确认', { exact: true }).first()).toBeVisible();
    await expect(panel.getByText('¥1,000.00').first()).toBeVisible();

    await panel.getByRole('button', { name: '生成税费核定草稿' }).click();
    await expect(panel.getByText('税费核定草稿已生成，需另一名有复核权限的会计复核。')).toBeVisible();
    await expect(panel.locator('.tax-f-basis strong').first()).toContainText('待复核');
    const listed = await checked(
      await request.get(`/api/tax/books/${f.book.id}/tax-basis?period=${f.book.period}`, {
        headers: f.headers,
      }),
      200,
    );
    const draft = listed.items.find((row: { status: string }) => row.status === 'draft');
    expect(draft).toBeTruthy();
    await checked(
      await request.post(`/api/tax/books/${f.book.id}/tax-basis/${draft.id}/review`, {
        headers: f.reviewer,
        data: {
          expected_version: draft.version,
          reason: '独立核对政策、基数、调整与人工依据',
        },
      }),
      200,
    );
    await panel.locator('header').first().getByRole('button', { name: '刷新' }).click();
    await expect(panel.locator('.tax-f-basis strong').first()).toContainText('已复核');

    await expect(panel.getByRole('cell', { name: '营业收入' })).toBeVisible();
    await expect(panel.getByText('¥1,000.00').last()).toBeVisible();
    await expect(panel.getByRole('cell', { name: '所得税费用' })).toBeVisible();
    await expect(panel.locator('.tax-f-totals')).toContainText('净利润');
    await panel.getByRole('button', { name: '查看依据' }).first().click();
    await expect(panel.getByText(/下钻：/)).toBeVisible();

    const fleet = panel.locator('.fleet-metrics');
    await expect(fleet.getByRole('heading', { name: '单车贡献、未分配成本与驾驶员欠款' })).toBeVisible();
    await expect(fleet.locator('.fleet-summary')).toContainText('净利润');
    await expect(fleet.locator('.fleet-summary')).toContainText('未知');
    await expect(fleet.locator('.fleet-note').first()).toContainText('共用费用未分配');

    await page.screenshot({
      path: `tmp_test/tax-f-${viewport.width}.png`,
      fullPage: true,
    });
  });
}
