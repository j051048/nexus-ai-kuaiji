import { expect, test } from '@playwright/test';
import { openTax, taxFullFixture } from './n04-tax-fixtures';

for (const viewport of [
  { width: 1280, height: 1000 },
  { width: 390, height: 844 },
]) {
  test(`FSC15 repair rate lifecycle is independent and reversible ${viewport.width}`, async ({
    page,
    request,
  }) => {
    test.setTimeout(150_000);
    const f = await taxFullFixture(request);
    await page.setViewportSize(viewport);
    await openTax(page, f);

    const panel = page.locator('.tax-cost-panel');
    const rateCard = panel
      .locator('details.tax-cost-rates')
      .filter({ hasText: '按车型维修费率卡' });
    await panel.scrollIntoViewIfNeeded();
    await rateCard.locator('summary').filter({ hasText: '按车型维修费率卡' }).click();
    const suffix = `${Date.now()}-${viewport.width}`;
    const vehicleType = `出租车-费率-${suffix}`;
    await rateCard.getByLabel('车型', { exact: true }).fill(vehicleType);
    await rateCard.getByLabel('费率（分）', { exact: true }).fill('5000');
    await rateCard.getByLabel('生效月', { exact: true }).fill('2026-10');
    await rateCard.getByLabel('结束月', { exact: true }).fill('2026-12');
    await rateCard.getByLabel('覆盖范围', { exact: true }).fill('基础保养（不含轮胎）');
    await rateCard.getByLabel('版本依据', { exact: true }).fill('客户核定 2026 年基础保养费率');
    await rateCard.getByRole('button', { name: '新建费率草稿', exact: true }).click();
    await expect(panel.getByText(/费率草稿 v1 已建立/)).toBeVisible({ timeout: 15000 });

    const rateRow = panel
      .locator('.tax-cost-lines article')
      .filter({ hasText: vehicleType })
      .first();
    await rateRow.getByRole('button', { name: '独立确认', exact: true }).click();
    await expect(panel.getByText(/拟定人与确认人必须不同/)).toBeVisible();

    const listed = await request.get(`/api/tax/books/${f.book.id}/repair-rates`, {
      headers: f.headers,
    });
    expect(listed.ok()).toBeTruthy();
    const draft = (await listed.json()).items.find(
      (item: { vehicle_type: string }) => item.vehicle_type === vehicleType,
    );
    const confirmed = await request.post(
      `/api/tax/books/${f.book.id}/repair-rates/${draft.id}/confirm`,
      { headers: f.reviewer, data: { expected_version: 1 } },
    );
    expect(confirmed.status()).toBe(200);
    const afterConfirm = await request.get(`/api/tax/books/${f.book.id}/repair-rates`, {
      headers: f.headers,
    });
    expect(afterConfirm.ok()).toBeTruthy();
    const confirmedRate = (await afterConfirm.json()).items.find(
      (item: { id: string }) => item.id === draft.id,
    );
    expect(confirmedRate.status).toBe('confirmed');
    await panel.getByRole('button', { name: '刷新', exact: true }).first().click();
    const managerRow = panel
      .locator('.tax-cost-lines article')
      .filter({ hasText: vehicleType })
      .first();
    await managerRow.getByRole('button', { name: '生效', exact: true }).click();
    await expect(panel.getByText(/已生效；历史版本保留/)).toBeVisible();
    await managerRow.getByRole('button', { name: '影响', exact: true }).click();
    await expect(panel.getByText(/当前没有维修来源引用该费率/)).toBeVisible();
    await managerRow.getByRole('button', { name: '停用', exact: true }).click();
    await expect(panel.getByText(/已停用；历史批次与凭证不删除/)).toBeVisible();
    await expect(managerRow.getByText(/已停用/)).toBeVisible();

    const widths = await page.evaluate(() => ({
      scroll: document.documentElement.scrollWidth,
      client: document.documentElement.clientWidth,
    }));
    expect(widths.scroll).toBeLessThanOrEqual(widths.client + 1);
    await rateCard.scrollIntoViewIfNeeded();
    await page.screenshot({
      path: `tmp_test/fsc15-rates-${viewport.width}.png`,
      fullPage: false,
    });
  });
}
