import { expect, test } from '@playwright/test';
import { openTax, taxFullFixture } from './n04-tax-fixtures';

for (const viewport of [
  { width: 1280, height: 1000 },
  { width: 390, height: 844 },
]) {
  test(`K05 OA snapshot and material request ${viewport.width}`, async ({ page, request }) => {
    test.setTimeout(120_000);
    const f = await taxFullFixture(request);
    await page.setViewportSize(viewport);
    await openTax(page, f);
    const panel = page.locator('section[aria-label="OA 审批快照与补件材料"]');
    await panel.scrollIntoViewIfNeeded();
    await expect(panel.getByRole('heading', { name: 'OA 审批快照 · 只补材料' })).toBeVisible();
    await panel.getByLabel('OA 业务编号').fill('OA-UI-K05-1');
    await panel.getByLabel('事项摘要').fill('客户拜访交通费补件');
    await panel.getByLabel('审批人').fill('虚构 OA 审批人');
    await panel.getByLabel('金额').fill('128.50');
    await panel.getByRole('button', { name: '登记并冻结快照' }).click();
    await expect(panel.getByText('客户拜访交通费补件')).toBeVisible();
    await expect(panel.getByText(/OA-UI-K05-1 · v1/)).toBeVisible();
    await panel.getByRole('button', { name: '生成补件材料请求' }).click();
    await expect(panel.getByText('待补材料')).toBeVisible();
    await expect(panel.getByText('补交审批依据与原始附件')).toBeVisible();
    const box = await panel.boundingBox();
    expect(box!.width).toBeLessThanOrEqual(viewport.width);
    await panel.scrollIntoViewIfNeeded();
    await page.screenshot({ path: `tmp_test/tax-k-oa-${viewport.width}.png`, fullPage: false });
  });
}
