import { expect, test } from '@playwright/test';
import { openTax, taxFullFixture } from './n04-tax-fixtures';

test.setTimeout(120_000);

for (const viewport of [
  { width: 1280, height: 900 },
  { width: 390, height: 844 },
]) {
  test(`TAX01 ${viewport.width} confirms every customer dependency and binds a preview`, async ({ page, request }) => {
    const f = await taxFullFixture(request);
    await page.setViewportSize(viewport);
    const panel = await openTax(page, f);
    const details = panel.locator('details.tax-pilot');
    await details.locator('summary').click();
    const labels = [
      '336/335 行收入明细逐行参与/排除',
      '旧期间残留行处理',
      '832.19 摊销不平来源',
      '租赁收入 3,550 差额',
      '保险月摊算法与取整',
      '5,990 计税基数出处',
      '税额分位差处理',
      'T+ 版本、模板与回执',
      '完整月源文件与相邻期',
      '工资/社保数据范围',
    ];
    for (const label of labels) {
      await details.getByLabel(label, { exact: true }).selectOption('confirmed');
      await details.getByLabel(`${label} 证据`, { exact: true }).fill(`客户确认 ${label}`);
    }
    await details.getByRole('button', { name: '保存草稿' }).click();
    await expect(details.getByText('已保存试点草稿')).toBeVisible();
    await details.getByRole('button', { name: '确认当前边界' }).click();
    await expect(details.getByText('边界已由当前负责人确认。')).toBeVisible();
    await details.getByRole('button', { name: '生成绑定预览' }).click();
    await expect(details.getByText(/预览 .* 绑定指纹/)).toBeVisible();
    await expect(details.getByText('未确认）')).toHaveCount(0);
    await page.screenshot({ path: `tmp_test/tax01-pilot-${viewport.width}.png`, fullPage: true });
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
  });
}
