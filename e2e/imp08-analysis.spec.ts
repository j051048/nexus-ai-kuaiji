import { test, expect } from '@playwright/test';
import { choosePersona } from './ui-navigation';

test('IMP08 analysis panel shows deterministic facts, coverage and an evidence link without writes', async ({
  page,
}) => {
  await page.goto('/');
  await choosePersona(page, 'accountant');
  page.on('request', (r) => { if (r.url().includes('analyses')) console.log('ANALYSES_REQ', r.method(), r.url()); });
  await page.route(/\/api\/assistant\/analyses\/capability/, (route) =>
    route.fulfill({
      json: {
        book_id: new URL(route.request().url()).searchParams.get('book_id'),
        enabled: true,
        kinds: ['expense_bridge'],
        real_model_evaluated: false,
      },
    }),
  );
  await page.route(/\/api\/assistant\/analyses$/, (route) =>
    route.fulfill({
      json: {
        kind: 'expense_bridge',
        status: 'partial',
        summary: '6602.02 管理费用：本期 150.00 分，差额未计算；明细见引用记录。',
        facts: {
          current_total: '150.00',
          previous_total: null,
          difference: null,
          fact_ids: ['expense_bridge:current_total_cents'],
        },
        coverage: { scope: 'current_only', rows_read: 2, has_more: false },
        missing: ['previous_period'],
        handoff: { role: 'accountant', reason: '分析覆盖不完整，需要会计在业务入口核对' },
        fingerprint: 'a'.repeat(64),
      },
    }),
  );
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
  await expect(page.getByText(/未检查：上期账套/)).toBeVisible();
  await expect(page.getByText(/核对本身不构成复核/)).toBeVisible();
  await expect(page.getByRole('link', { name: '打开账套原记录核对' })).toHaveAttribute(
    'href',
    /book=.*period=.*company=/,
  );
  await page.screenshot({ path: 'tmp_test/imp/imp-20261106-a/imp08-analysis.png', fullPage: true });
  expect(writes).toEqual([]);
});
