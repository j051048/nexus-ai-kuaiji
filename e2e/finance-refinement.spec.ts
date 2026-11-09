import { expect, test } from '@playwright/test';
import { checked, formalFixture } from './formal-books-fixtures';
import { mkdirSync } from 'node:fs';

for (const width of [1365, 390]) {
  test(`财务聚焦 ${width} 当前办理与返回保留查询`, async ({ page, request }) => {
    test.setTimeout(120000);
    const f = await formalFixture(request, false, '聚焦办理');
    await page.setViewportSize({ width, height: 844 });
    await page.goto(
      `/workspace/formal-books?book=${f.book.id}&company=${f.company.id}&period=${f.book.period}`,
    );
    await expect(page.getByRole('navigation', { name: '账簿类型', exact: true })).toBeVisible();
    await expect(page.getByRole('navigation', { name: '做账五步流水线', exact: true })).toHaveCSS(
      'display',
      'flex',
    );
    await page.getByText('往来与期末办理（账龄、资产及计提草稿）', { exact: true }).click();
    await expect(page.getByRole('navigation', { name: '账簿类型', exact: true })).not.toBeVisible();
    await page.getByRole('button', { name: '返回账簿（保留筛选）', exact: true }).click();
    await expect(page.getByRole('navigation', { name: '账簿类型', exact: true })).toBeVisible();
    await page.getByText('往来与期末办理（账龄、资产及计提草稿）', { exact: true }).click();
    const panel = page.getByRole('region', { name: '往来与期末办理', exact: true });
    await panel.getByRole('button', { name: '资产与期末草稿', exact: true }).click();
    await expect(panel.getByText('正在读取资产和期末草稿…', { exact: true })).not.toBeVisible();
    mkdirSync('tmp_test/finance-refinement/20261109/screenshots', { recursive: true });
    await page.screenshot({
      path: `tmp_test/finance-refinement/20261109/screenshots/task-${width}.png`,
      fullPage: true,
    });
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(
      true,
    );
  });
  test(`资产差异 ${width} 人工确认到独立复核后记账`, async ({ page, request }) => {
    test.setTimeout(120000);
    const f = await formalFixture(request, false, '资产核对');
    const ledger = await checked(
      await request.get(`/api/books/${f.book.id}/ledger`, { headers: f.headers }),
      200,
    );
    const accounts = [];
    for (const [code, name, category, normal_balance] of [
      ['6602.09', '虚构设备折旧费用', 'expense', 'debit'],
      ['1602', '虚构累计折旧', 'asset', 'credit'],
    ])
      accounts.push(
        await checked(
          await request.post('/api/opening/accounts', {
            headers: f.headers,
            data: {
              ledger_id: ledger.id,
              code,
              name,
              category,
              normal_balance,
              effective_from: '2026-01',
              reason: '本地资产操作验收',
            },
          }),
        ),
      );
    const asset = await checked(
      await request.post('/api/periodic/assets', {
        headers: f.headers,
        data: {
          book_id: f.book.id,
          kind: 'fixed',
          name: '人工核对设备',
          cost: '100.00',
          residual: '0.00',
          life_months: 3,
          start_period: '2026-10',
          expense_account_id: accounts[0].id,
          expense_account_version: 1,
          accum_account_id: accounts[1].id,
          accum_account_version: 1,
          reason: '本地虚构资产原始依据',
        },
      }),
    );
    await checked(
      await request.post(`/api/periodic/assets/${asset.id}/generate`, { headers: f.headers }),
    );
    await page.setViewportSize({ width, height: 844 });
    await page.goto(
      `/workspace/formal-books?book=${f.book.id}&company=${f.company.id}&period=${f.book.period}&finance=periodic`,
    );
    await page.getByRole('button', { name: '核对历史差异与科目版本', exact: true }).click();
    const panel = page.getByRole('region', { name: '资产差异核对', exact: true });
    await expect(panel).toContainText('33.33 元');
    await panel.getByLabel('核对依据与理由', { exact: true }).fill('已核对原始资产和本期差额');
    await panel.getByRole('button', { name: '确认差额并生成待复核草稿', exact: true }).click();
    await expect(panel).toContainText('核对草稿已生成');
    await panel.getByRole('button', { name: '返回资产与期末草稿', exact: true }).click();
    await expect(
      page.getByText('本版本由你制单，请交另一位有复核权限的会计审核。', { exact: true }),
    ).toBeVisible();
    const state = await checked(
      await request.get(`/api/periodic/workspace?book_id=${f.book.id}`, { headers: f.headers }),
      200,
    );
    expect(state.drafts).toHaveLength(1);
    const draft = state.drafts[0].draft;
    expect(
      (
        await request.post(`/api/periodic/drafts/${draft.id}/approve`, {
          headers: f.headers,
          data: { expected_version: draft.version, reason: '自审必须拒绝' },
        })
      ).status(),
    ).toBe(403);
    await checked(
      await request.post(`/api/periodic/drafts/${draft.id}/approve`, {
        headers: f.reviewer,
        data: { expected_version: draft.version, reason: '另一位会计独立核对本期差额' },
      }),
      200,
    );
    await page.reload();
    await page.getByLabel('办理理由', { exact: true }).fill('依据独立复核办理正式记账');
    await page.getByLabel('记账日期', { exact: true }).fill('2026-10-28');
    await page.getByRole('button', { name: '准备正式记账', exact: true }).click();
    await page.getByRole('button', { name: '确认正式记账', exact: true }).click();
    await expect(page.getByText('已正式记账', { exact: false }).first()).toBeVisible();
    const done = await checked(
      await request.get(`/api/periodic/workspace?book_id=${f.book.id}`, { headers: f.headers }),
      200,
    );
    expect(done.drafts[0].draft.status).toBe('posted');
    expect(done.assets[0].posted_cents).toBe(3333);
    mkdirSync('tmp_test/finance-refinement/20261109/screenshots', { recursive: true });
    await page.screenshot({
      path: `tmp_test/finance-refinement/20261109/screenshots/asset-${width}.png`,
      fullPage: true,
    });
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(
      true,
    );
  });
}
