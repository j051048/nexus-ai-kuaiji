import { expect, test } from '@playwright/test';
import { checked, formalFixture } from './formal-books-fixtures';
import { pinReviewer } from './independent-review';

const PAIRS = 258;

async function largeDraft(request: Parameters<typeof formalFixture>[0]) {
  const f = await formalFixture(request, false, 'TAX05大凭证虚构');
  const imported = await checked(
    await request.post(`/api/books/${f.book.id}/imports`, {
      headers: f.headers,
      data: {
        source_name: '合成大凭证流水',
        rows: [
          {
            external_id: 'LV-516',
            date: '2026-10-05',
            description: '批量办公费用（合成）',
            counterparty: '虚构供应商',
            amount: '25800.00',
            direction: 'expense',
            evidence_ref: 'synthetic://tax05-large-voucher',
          },
        ],
      },
    }),
    200,
  );
  expect(imported.imported).toBe(1);
  const suggested = await checked(
    await request.post(`/api/books/${f.book.id}/suggestions`, { headers: f.headers }),
    200,
  );
  expect(suggested.created).toBe(1);
  const library = await checked(
    await request.get(`/api/books/${f.book.id}`, { headers: f.headers }),
    200,
  );
  const source = library.sources.find(
    (row: { external_id: string }) => row.external_id === 'LV-516',
  );
  const draft = library.drafts.find((row: { source_id: string }) => row.source_id === source?.id);
  const draftId = draft?.id;
  expect(draftId).toBeTruthy();
  const before = await checked(
    await request.get(`/api/books/${f.book.id}/drafts/${draftId}`, { headers: f.headers }),
    200,
  );
  const lines = Array.from({ length: PAIRS }, (_, index) => [
    {
      account_code: '2202',
      debit: '100.00',
      credit: '0.00',
      auxiliary_values: { counterparty: '虚构供应商' },
    },
    {
      account_code: '1002',
      debit: '0.00',
      credit: '100.00',
      auxiliary_values: {},
    },
  ]).flat();
  const updated = await checked(
    await request.patch(`/api/books/${f.book.id}/drafts/${draftId}`, {
      headers: f.headers,
      data: {
        expected_version: before.version,
        description: '合成 258 行对 / 516 行受控大凭证',
        lines,
        correction_reason: 'TAX05 受控大凭证浏览器验收',
      },
    }),
    200,
  );
  expect(updated.lines).toHaveLength(PAIRS * 2);
  expect(updated.issues).toEqual([]);
  return { f, draftId };
}

for (const viewport of [
  { width: 1280, height: 1000 },
  { width: 390, height: 844 },
  { width: 390, height: 500 },
]) {
  test(`TAX05 large voucher pages and locates 516 lines, then independent review ${viewport.width}x${viewport.height}`, async ({
    page,
    request,
  }) => {
    test.setTimeout(120_000);
    const { f, draftId } = await largeDraft(request);
    await page.setViewportSize(viewport);
    await pinReviewer(page);
    await page.goto(
      `/workspace/review?review_book=${encodeURIComponent(f.book.id)}&review_draft=${encodeURIComponent(draftId)}`,
    );
    const drawer = page.getByRole('dialog', { name: '原件与凭证，一起核对', exact: true });
    await expect(drawer).toBeVisible();
    const grid = drawer.getByRole('table', { name: '同屏凭证分录表' });
    const evidence = await drawer.locator('.inline-review-evidence-column').boundingBox();
    const facts = await drawer.locator('.inline-review-facts').boundingBox();
    if (viewport.width >= 1000) {
      expect(facts?.x ?? 0).toBeGreaterThan((evidence?.x ?? 0) + (evidence?.width ?? 0) * 0.8);
    } else {
      expect(facts?.y ?? 0).toBeGreaterThan(evidence?.y ?? 0);
    }
    await expect(grid.locator('tbody tr.journal-grid-line')).toHaveCount(50);
    await expect(drawer.getByText('第 1–50 行 / 共 516 行', { exact: true })).toBeVisible();
    await expect(drawer.getByTestId('journal-totals')).toContainText('本页 借 ¥2,500.00');
    await expect(drawer.getByTestId('journal-totals')).toContainText('全量 借 ¥25,800.00');
    await drawer.getByLabel('定位分录行号').fill('516');
    if (viewport.height === 500) {
      await drawer.getByLabel('定位分录行号').focus();
      const [locate, footer] = await Promise.all([
        drawer.getByLabel('定位分录行号').boundingBox(),
        drawer.locator('.inline-review-footer').boundingBox(),
      ]);
      expect(locate!.y + locate!.height).toBeLessThanOrEqual(footer!.y + 1);
    }
    await drawer.getByRole('button', { name: '定位', exact: true }).click();
    await expect(drawer.getByText('第 501–516 行 / 共 516 行', { exact: true })).toHaveText(
      '第 501–516 行 / 共 516 行',
    );
    await expect(drawer.getByTestId('journal-totals')).toContainText('本页 借 ¥800.00');
    await expect(grid.locator('tbody tr.journal-grid-line').last()).toContainText('516');
    await expect(grid.locator('tbody tr.journal-grid-line').last()).toBeVisible();
    await drawer.locator('.journal-grid-pager').screenshot({
      path: `tmp_test/tax05-pager-${viewport.width}.png`,
    });
    await drawer.getByRole('button', { name: '紧凑行距' }).click();
    await expect(drawer.locator('.journal-entry-grid')).toHaveClass(/density-compact/);
    await expect(drawer.getByRole('button', { name: '舒适行距' })).toHaveAttribute('aria-pressed', 'true');
    expect(await page.evaluate(() => localStorage.getItem('journal-grid-density'))).toBe('compact');
    await drawer.getByRole('button', { name: '舒适行距' }).click();
    await expect(drawer.locator('.journal-entry-grid')).toHaveClass(/density-comfortable/);
    await drawer.getByLabel('凭证列宽').selectOption('account');
    await expect(drawer.locator('.journal-entry-grid')).toHaveClass(/width-account/);
    expect(await page.evaluate(() => localStorage.getItem('journal-grid-width'))).toBe('account');
    await expect(drawer.getByRole('button', { name: '下一页分录', exact: true })).toBeDisabled();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(
      true,
    );

    await drawer.getByLabel('我已核对同屏原件与分录', { exact: true }).check();
    await drawer.getByRole('button', { name: '确认审核', exact: true }).click();
    await expect(drawer.getByText('已审核，待导出', { exact: true })).toBeVisible();
    await page.screenshot({
      path: `tmp_test/tax05-large-voucher-${viewport.width}.png`,
      fullPage: true,
    });
    void f;
  });
}

test('TAX05 stale draft version is rejected by the reviewer', async ({ page, request }) => {
  test.setTimeout(90_000);
  const { f, draftId } = await largeDraft(request);
  await page.setViewportSize({ width: 1280, height: 900 });
  await pinReviewer(page);
  await page.goto(
    `/workspace/review?review_book=${encodeURIComponent(f.book.id)}&review_draft=${encodeURIComponent(draftId)}`,
  );
  const drawer = page.getByRole('dialog', { name: '原件与凭证，一起核对', exact: true });
  await expect(drawer).toBeVisible();
  const current = await checked(
    await request.get(`/api/books/${f.book.id}/drafts/${draftId}`, { headers: f.headers }),
    200,
  );
  await checked(
    await request.patch(`/api/books/${f.book.id}/drafts/${draftId}`, {
      headers: f.headers,
      data: {
        expected_version: current.version,
        description: '另一会话已修改此凭证',
        lines: current.lines.map((line: Record<string, unknown>) => ({
          account_code: line.account_code,
          debit: line.debit,
          credit: line.credit,
          auxiliary_values: line.auxiliary_values ?? {},
        })),
        correction_reason: 'TAX05 版本冲突夹具',
      },
    }),
    200,
  );
  await drawer.getByLabel('我已核对同屏原件与分录', { exact: true }).check();
  await drawer.getByRole('button', { name: '确认审核', exact: true }).click();
  await expect(drawer.locator('.inline-error')).toContainText(/版本|变化|刷新/);
  await expect(drawer.getByText('已审核，待导出', { exact: true })).toHaveCount(0);
});


test('TAX05 access denied never renders another scope draft rows', async ({ page, request }) => {
  const { f, draftId } = await largeDraft(request);
  await page.route('**/api/books/*/drafts/*', async (route) => {
    if (route.request().method() === 'GET') {
      await route.fulfill({
        status: 403,
        contentType: 'application/json',
        body: JSON.stringify({ detail: '无权访问该账套凭证' }),
      });
      return;
    }
    await route.continue();
  });
  await page.setViewportSize({ width: 1280, height: 900 });
  await pinReviewer(page);
  await page.goto(
    `/workspace/review?review_book=${encodeURIComponent(f.book.id)}&review_draft=${encodeURIComponent(draftId)}`,
  );
  const drawer = page.getByRole('dialog', { name: '原件与凭证，一起核对', exact: true });
  await expect(drawer.getByText(/无权访问|权限|重新确认/)).toBeVisible();
  await expect(drawer.locator('.journal-grid tbody tr')).toHaveCount(0);
});
