import { expect, test } from '@playwright/test';
import { checked } from './formal-fixtures';
import { openTax, pinPersona, taxFullFixture } from './n04-tax-fixtures';

async function prepare(
  request: Parameters<typeof taxFullFixture>[0],
  f: Awaited<ReturnType<typeof taxFullFixture>>,
) {
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

    const suffix = `${Date.now()}-${viewport.width}`;
    const customer = `虚构租赁客户-界面-${suffix}`;
    const vehicle = `V-LEASE-${suffix}`;
    const invoice = `INV-LEASE-${suffix}`;
    await panel.getByLabel('客户', { exact: true }).fill(customer);
    await panel.getByLabel('车辆外部编号', { exact: true }).fill(vehicle);
    await panel.getByLabel('开始月', { exact: true }).fill(f.book.period);
    await panel.getByLabel('结束月', { exact: true }).fill(f.book.period);
    await panel.getByLabel('月租', { exact: true }).fill('1000.00');
    await panel.getByLabel('预收科目', { exact: true }).fill('2241');
    await panel.getByRole('button', { name: '登记租赁合同' }).click();
    await expect(panel.getByText('租赁合同已登记，收款与收入分别确认后才生成凭证。')).toBeVisible({
      timeout: 20000,
    });
    await expect(panel.getByRole('button', { name: new RegExp(customer) })).toBeVisible();
    const leases = await checked(
      await request.get(`/api/tax/books/${f.book.id}/leases`, { headers: f.headers }),
      200,
    );
    const leaseId = leases.items.find(
      (row: { customer_name: string }) => row.customer_name === customer,
    ).id;

    await panel.getByRole('button', { name: '确认本期业务' }).click();
    await expect(panel.getByText('业务确认已登记。')).toBeVisible();
    await panel.getByRole('button', { name: '确认本期收款' }).click();
    await expect(panel.getByText('收款事实已登记。')).toBeVisible();
    await panel.getByLabel('发票号', { exact: true }).fill(invoice);
    await panel.getByRole('button', { name: '确认本期发票' }).click();
    await expect(panel.getByText('发票事实已登记。')).toBeVisible();
    await expect(panel.getByRole('list', { name: '租赁确认事实' })).toContainText(invoice);
    await panel.getByRole('button', { name: '确认本期收入' }).click();
    await expect(panel.getByText('当期租赁收入已生成草稿；复核后导出。')).toBeVisible();
    await expect(panel.getByText('已确认', { exact: true }).first()).toBeVisible();
    await expect(panel.getByText('¥1,000.00').first()).toBeVisible();

    await panel.getByRole('button', { name: '生成税费核定草稿' }).click();
    await expect(
      panel.getByText('税费核定草稿已生成，需另一名有复核权限的会计复核。'),
    ).toBeVisible();
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
    await expect(
      fleet.getByRole('heading', { name: '单车贡献、未分配成本与驾驶员欠款' }),
    ).toBeVisible();
    await expect(fleet.locator('.fleet-summary')).toContainText('净利润');
    await expect(fleet.locator('.fleet-summary')).toContainText('未知');
    await expect(fleet.locator('.fleet-note').first()).toContainText('共用费用未分配');

    await pinPersona(page, 'reviewer');
    await openTax(page, f);
    const reviewerPanel = page.getByRole('region', { name: '税务底稿', exact: true });
    await reviewerPanel.getByRole('button', { name: new RegExp(customer) }).click();
    await reviewerPanel.getByRole('button', { name: '复核租赁凭证' }).click();
    const drawer = page.getByRole('dialog', { name: '原件与凭证，一起核对', exact: true });
    await expect(drawer).toBeVisible();
    await drawer.getByRole('checkbox', { name: '我已核对同屏原件与分录', exact: true }).check();
    await drawer.getByRole('button', { name: '确认审核', exact: true }).click();
    await expect(drawer.getByText('已审核，待导出', { exact: true })).toBeVisible();
    await page.screenshot({
      path: `tmp_test/tax-f-lease-review-${viewport.width}.png`,
      fullPage: false,
    });
    await drawer.getByRole('button', { name: '关闭同屏复核' }).click();
    const downloadPromise = page.waitForEvent('download');
    await page.getByRole('button', { name: '导出租赁凭证' }).click();
    const download = await downloadPromise;
    expect(download.suggestedFilename()).toMatch(/^vouchers-.*\.csv$/);
    await expect(page.getByText('租赁凭证已导出。', { exact: false })).toBeVisible();
    await page.getByLabel('红字发票号').fill('RED-UI-1');
    await page.getByRole('button', { name: '登记发票红冲' }).click();
    await expect(page.getByText('发票红冲事实已登记', { exact: false })).toBeVisible();
    await expect(page.getByLabel('发票红冲事实')).toContainText('RED-UI-1');

    await checked(
      await request.post(`/api/tax/books/${f.book.id}/leases/${leaseId}/amend`, {
        headers: f.headers,
        data: {
          expected_version: 1,
          end_period: `${f.book.period.slice(0, 7).slice(0, 4)}-11`,
          monthly: '1000.00',
          reason: '合同续签一个月，保留已确认期间',
        },
      }),
      200,
    );
    await pinPersona(page, 'accountant');
    await openTax(page, f);
    const managerPanel = page.getByRole('region', { name: '税务底稿', exact: true });
    await managerPanel.getByRole('button', { name: '刷新' }).first().click();
    const stale = managerPanel.locator('.tax-f-stale');
    await expect(stale).toContainText('合同已改版');
    await expect(managerPanel.getByRole('button', { name: '导出租赁凭证' })).toBeDisabled();
    await stale.scrollIntoViewIfNeeded();
    await page.screenshot({
      path: `tmp_test/tax-f-lease-stale-${viewport.width}.png`,
      fullPage: false,
    });
    const approveResponse = page.waitForResponse((response) => response.url().includes('/approve'));
    await stale.getByRole('button', { name: '确认当前版本' }).click();
    expect((await approveResponse).status()).toBe(200);
    await expect(managerPanel.locator('.tax-f-stale')).toHaveCount(0);
    await expect(managerPanel.getByRole('button', { name: '导出租赁凭证' })).toBeEnabled();

    await page.screenshot({
      path: `tmp_test/tax-f-${viewport.width}.png`,
      fullPage: true,
    });
  });
}
