import { expect, test } from '@playwright/test';
import { checked } from './formal-fixtures';
import { openTax, taxFullFixture } from './n04-tax-fixtures';

const DEPS = [
  'income_336_335',
  'prior_period_residue',
  'imbalance_832_19',
  'rent_3550',
  'insurance_formula',
  'tax_base_5990',
  'tax_rounding',
  'tplus_template',
  'source_files',
  'payroll_scope',
];

for (const viewport of [
  { width: 1280, height: 1000 },
  { width: 390, height: 844 },
]) {
  test(`FSC13 closing authority blocks unconfirmed and records external evidence ${viewport.width}`, async ({
    page,
    request,
  }) => {
    test.setTimeout(120_000);
    const f = await taxFullFixture(request);
    await page.setViewportSize(viewport);
    await openTax(page, f);

    const panel = page.locator('section[aria-label="结转权威与外部证据"]');
    await panel.scrollIntoViewIfNeeded();
    await expect(panel.getByRole('heading', { name: '结转权威与外部证据' })).toBeVisible();
    await expect(panel).toContainText('未确认');

    await panel.getByLabel('外部编号').fill('CLOSE-UI-1');
    await panel.getByLabel('金额（元）').fill('100.00');
    await panel.getByRole('button', { name: '登记外部证据' }).click();
    await expect(panel.getByText('试点配置中的结转权威模式尚未确认')).toBeVisible({
      timeout: 15_000,
    });

    await checked(
      await request.post(`/api/tax/pilot/companies/${f.company.id}/draft`, {
        headers: f.headers,
        data: {
          expected_version: 0,
          config: {
            authority_mode: 'internal',
            source_of_truth: '客户确认的月度资料',
            notes: 'FSC13 浏览器',
          },
          dependencies: DEPS.map((code) => ({
            code,
            status: 'not_applicable',
            evidence_ref: 'FSC13 合成浏览器资料',
          })),
        },
      }),
      201,
    );
    await checked(
      await request.post(`/api/tax/pilot/companies/${f.company.id}/confirm`, {
        headers: f.headers,
        data: { expected_version: 1 },
      }),
      200,
    );

    await panel.getByRole('button', { name: '刷新' }).click();
    await expect(panel).toContainText('内部账簿');
    await panel.getByLabel('外部编号').fill('CLOSE-UI-1');
    await panel.getByLabel('金额（元）').fill('100.00');
    await panel.getByRole('button', { name: '登记外部证据' }).click();
    await expect(panel).toContainText('仅核对（内部权威）');
    await expect(panel.getByRole('table')).toContainText('CLOSE-UI-1');
    await panel.scrollIntoViewIfNeeded();
    const metrics = await panel.evaluate((el) => ({
      width: el.getBoundingClientRect().width,
      parentWidth: el.parentElement!.getBoundingClientRect().width,
      scrollWidth: document.documentElement.scrollWidth,
      clientWidth: document.documentElement.clientWidth,
      thead: getComputedStyle(el.querySelector('thead')!).display,
    }));
    expect(metrics.width).toBeLessThanOrEqual(metrics.parentWidth + 1);
    expect(metrics.scrollWidth).toBeLessThanOrEqual(metrics.clientWidth + 1);
    if (viewport.width < 640) expect(metrics.thead).toBe('none');
    await page.screenshot({
      path: `tmp_test/fsc13-closing-${viewport.width}.png`,
      fullPage: false,
    });

    const summary = await checked(
      await request.get(`/api/periodic/closing/summary?book_id=${f.book.id}`, {
        headers: f.headers,
      }),
      200,
    );
    expect(summary.authority.mode).toBe('internal');
    expect(summary.evidence).toHaveLength(1);
    expect(summary.double_count_blocked).toBe(false);
  });
}
