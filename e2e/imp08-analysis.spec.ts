import { test, expect } from '@playwright/test';
import { choosePersona } from './ui-navigation';

test('IMP08 analysis panel shows deterministic facts, coverage and an evidence link without writes', async ({
  page,
}) => {
  await page.goto('/');
  await choosePersona(page, 'accountant');
  await page.route(/\/api\/assistant\/analyses$/, async (route) => {
    const response = await route.fetch();
    expect(response.ok()).toBeTruthy();
    const actual = await response.json();
    await route.fulfill({
      json: {
        ...actual,
        status: 'partial',
        summary: '测试展示夹具',
        facts: {
          ...actual.facts,
          current_total: '150.00',
          previous_total: null,
          difference: null,
          current_rows: [],
          previous_rows: [],
        },
        coverage: {
          ...actual.coverage,
          detail_rows: 0,
          rows_read: 0,
          total_rows: 2,
          detail_complete: false,
          has_more: true,
        },
        missing: ['previous_period'],
        handoff: { reason: '分析覆盖不完整，需要会计在业务入口核对' },
      },
    });
  });
  await page.getByRole('button', { name: '客户企业' }).click();
  await page.getByText('澄光科技（虚构示例）').last().click();
  const writes: string[] = [];
  page.on('request', (request) => {
    if (['PUT', 'PATCH', 'DELETE'].includes(request.method())) writes.push(request.method());
  });
  await page.waitForTimeout(1500);
  await page.getByText('只读差异核对').click();
  await page.getByRole('button', { name: '开始只读核对' }).click();
  await expect(page.getByText(/本期 150.00 元/)).toBeVisible();
  await expect(page.getByText(/总额 2 行，当前明细 0 行/)).toBeVisible();
  await expect(page.getByText(/未检查：上期账套/)).toBeVisible();
  await expect(page.getByText(/核对本身不构成复核/)).toBeVisible();
  await expect(page.getByRole('link', { name: '打开账套原记录核对' })).toHaveAttribute(
    'href',
    /book=.*period=.*company=/,
  );
  await page.screenshot({ path: test.info().outputPath('analysis.png'), fullPage: true });
  expect(writes).toEqual([]);
});
