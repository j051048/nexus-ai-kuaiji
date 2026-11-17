import { expect, test } from '@playwright/test';
import { openTax, taxFullFixture } from './n04-tax-fixtures';

for (const viewport of [
  { width: 1280, height: 1000 },
  { width: 390, height: 844 },
]) {
  test(`FSC16 package contract lifecycle and quota summary ${viewport.width}`, async ({
    page,
    request,
  }) => {
    test.setTimeout(150_000);
    const f = await taxFullFixture(request);
    await page.setViewportSize(viewport);
    await openTax(page, f);

    const panel = page.locator('.tax-cost-panel');
    await panel.scrollIntoViewIfNeeded();
    const packageBlock = panel.locator('details').filter({ hasText: '维修包干合同与额度' });
    if (!(await packageBlock.evaluate((node) => (node as HTMLDetailsElement).open))) {
      await packageBlock.locator('summary').click();
    }
    const suffix = `${Date.now()}-${viewport.width}`;
    const contractNo = `PKG-E2E-${suffix}`;
    await packageBlock.getByLabel('合同号', { exact: true }).fill(contractNo);
    await packageBlock.getByLabel('合同额度（分）', { exact: true }).fill('10000');
    await packageBlock.getByLabel('覆盖车辆（逗号分隔）', { exact: true }).fill('V1,V2');
    await packageBlock.getByLabel('开始月', { exact: true }).fill('2026-10');
    await packageBlock.getByLabel('结束月', { exact: true }).fill('2026-12');
    await packageBlock.getByLabel('客户依据', { exact: true }).fill('客户核定年度包干合同');
    await packageBlock.getByRole('button', { name: '新建包干合同草稿', exact: true }).click();
    await expect(panel.getByText(/包干合同草稿已建/)).toBeVisible({ timeout: 15_000 });

    const listed = await request.get(`/api/tax/books/${f.book.id}/repair-packages`, {
      headers: f.headers,
    });
    expect(listed.ok()).toBeTruthy();
    const created = (await listed.json()).items.find(
      (item: { contract_no: string }) => item.contract_no === contractNo,
    );
    expect(created).toBeTruthy();
    const confirmed = await request.post(
      `/api/tax/books/${f.book.id}/repair-packages/${created.id}/confirm`,
      { headers: f.reviewer, data: { expected_version: 1 } },
    );
    expect(confirmed.status()).toBe(200);

    await panel.getByRole('button', { name: '刷新', exact: true }).first().click();
    if (!(await packageBlock.evaluate((node) => (node as HTMLDetailsElement).open))) {
      await packageBlock.locator('summary').click();
    }
    const card = packageBlock.locator('article').filter({ hasText: contractNo }).first();
    await card.getByRole('button', { name: '生效', exact: true }).click();
    await expect(panel.getByText(/包干合同已生效/)).toBeVisible({ timeout: 15_000 });
    await card.getByRole('button', { name: '查看额度', exact: true }).click();
    await expect(panel.getByText(/剩余 ¥100\.00/)).toBeVisible();

    await card.getByRole('button', { name: '登记占用', exact: true }).click();
    await card.getByLabel('来源键', { exact: true }).fill(`FP-PKG-${suffix}`);
    await card.getByLabel('占用金额（元）', { exact: true }).fill('30');
    await card.getByRole('button', { name: '确认占用', exact: true }).click();
    await expect(panel.getByText(/包干占用已登记/)).toBeVisible({ timeout: 15_000 });
    await card.getByRole('button', { name: '查看额度', exact: true }).click();
    await expect(panel.getByText(/已占用 ¥30\.00/)).toBeVisible();
    await expect(panel.getByText(/剩余 ¥70\.00/)).toBeVisible();

    await packageBlock.getByRole('button', { name: '释放', exact: true }).first().click();
    await packageBlock.getByLabel('释放金额（元）', { exact: true }).fill('10');
    await packageBlock.getByRole('button', { name: '确认释放', exact: true }).click();
    await expect(panel.getByText(/包干占用已释放/)).toBeVisible({ timeout: 15_000 });
    await expect(panel.getByText(/剩余 ¥80\.00/)).toBeVisible();

    const widths = await page.evaluate(() => ({
      scroll: document.documentElement.scrollWidth,
      client: document.documentElement.clientWidth,
    }));
    expect(widths.scroll).toBeLessThanOrEqual(widths.client + 1);
    await page.screenshot({
      path: `tmp_test/fsc16-packages-${viewport.width}.png`,
      fullPage: false,
    });
  });
}
