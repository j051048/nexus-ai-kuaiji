import { expect, test, type APIRequestContext, type Page } from '@playwright/test';
import { checked, formalFixture } from './formal-books-fixtures';
import { pinPersona } from './n04-tax-fixtures';

test.setTimeout(150_000);
test.use({ actionTimeout: 15_000 });

async function withDraft(request: APIRequestContext) {
  const f = await formalFixture(request);
  const period = f.book.period;
  const imported = await request.post(`/api/books/${f.book.id}/imports`, {
    headers: f.headers,
    data: {
      source_name: 'B02 上下文切换夹具',
      rows: [
        {
          external_id: 'b02-context-1',
          date: `${period}-03`,
          description: '办公用品（虚构）',
          counterparty: '虚构供应商甲',
          amount: '100.00',
          direction: 'expense',
          evidence_ref: '明确虚构的归档凭据 B02-1',
        },
      ],
    },
  });
  expect(imported.status(), await imported.text()).toBe(200);
  expect(
    (await request.post(`/api/books/${f.book.id}/suggestions`, { headers: f.headers })).status(),
  ).toBe(200);
  return f;
}

async function openDirtyDrawer(page: Page, bookId: string) {
  await page.goto(`/workspace/review?book=${bookId}&period=2026-10`);
  await page.getByLabel('选择本页凭证', { exact: true }).check();
  await page.getByRole('button', { name: '查看证据', exact: true }).first().click();
  const drawer = page.getByRole('dialog', { name: '原件与凭证，一起核对', exact: true });
  const summary = drawer.getByLabel('同屏凭证摘要', { exact: true });
  await expect(summary).toBeVisible();
  await summary.fill('未保存的说明文字');
  return { drawer, summary };
}

test('B02 未保存编辑时切换期间：取消后原期间与表单内容都保留', async ({ page, request }) => {
  const f = await withDraft(request);
  await pinPersona(page, 'accountant');
  const { summary } = await openDirtyDrawer(page, f.book.id);
  const periodInput = page.getByLabel('当前会计期间', { exact: true });
  await expect(periodInput).toHaveValue('2026-10');

  await periodInput.fill('2026-11');
  const dialog = page.getByRole('dialog', { name: '切换会计期间？', exact: true });
  await expect(dialog).toBeVisible();
  await expect(dialog).toContainText('未保存');
  await expect(dialog).toContainText('复核抽屉编辑');
  await dialog.getByRole('button', { name: '留在当前单位/期间', exact: true }).click();

  // 取消 = 不切换：期间仍是原值，抽屉里的未保存文字也没有丢。
  await expect(page.getByLabel('当前会计期间', { exact: true })).toHaveValue('2026-10');
  await expect(summary).toHaveValue('未保存的说明文字');

  // 确认丢弃后才真正切换（证明守卫放行路径也通）。
  await page.getByLabel('当前会计期间', { exact: true }).fill('2026-11');
  await page
    .getByRole('dialog', { name: '切换会计期间？', exact: true })
    .getByRole('button', { name: '丢弃修改并切换', exact: true })
    .click();
  await expect(page.getByLabel('当前会计期间', { exact: true })).toHaveValue('2026-11');
});

test('B02 旧期间延迟响应绝不覆盖新期间的锁期状态', async ({ page, request }) => {
  const f = await withDraft(request);
  await pinPersona(page, 'accountant');
  let markStaleStarted!: () => void;
  const staleStarted = new Promise<void>((resolve) => {
    markStaleStarted = resolve;
  });
  // 旧期间的回执延迟 1.5s 才到，且谎报“已锁期”；新期间立即返回空。
  await page.route('**/api/b04-close?**', async (route) => {
    const url = new URL(route.request().url());
    if (url.searchParams.get('period') === '2026-10') {
      markStaleStarted();
      await new Promise((resolve) => setTimeout(resolve, 1500));
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          closes: [{ id: 'stale-close', period: '2026-10', status: 'locked', version: 1 }],
        }),
      });
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ closes: [] }),
    });
  });
  await page.goto(
    `/workspace/formal-books?book=${f.book.id}&company=${f.company.id}&period=2026-10`,
  );
  const lock = page.locator('.workspace-context-lock');
  // 确认旧期间请求已在途（仍在 1.5s 延迟飞行中），立即切到新期间制造真实竞态。
  await staleStarted;
  await page.getByLabel('当前会计期间', { exact: true }).fill('2026-11');
  await expect(page.getByLabel('当前会计期间', { exact: true })).toHaveValue('2026-11');
  await expect(lock).toHaveText('未锁期');
  // 等过旧请求延迟到达的时间点：即使旧回执迟到，也不能把新期间画成已锁期。
  await page.waitForTimeout(2000);
  await expect(lock).not.toHaveText('已锁期');
  await expect(lock).toHaveText('未锁期');
});

/** 造到「预检已独立复核、可锁期」为止（不执行锁期），供不可逆二次输入用例使用。 */
async function preLockableClose(request: APIRequestContext) {
  const f = await formalFixture(request, true);
  await checked(
    await request.post('/api/subledger/control-applicability', {
      headers: f.headers,
      data: {
        book_id: f.book.id,
        status: 'not_applicable',
        effective_from: '2026-10',
        effective_to: '2026-10',
        reason: '虚构企业本月无往来余额、无历史事项与控制科目配置',
      },
    }),
  );
  const report = await checked(
    await request.post('/api/reports', {
      headers: f.headers,
      data: { book_id: f.book.id, period: f.book.period },
    }),
  );
  await checked(
    await request.post(`/api/reports/${report.report.id}/validate`, { headers: f.headers }),
    200,
  );
  const command = { expected_version: 1, reason: '本地虚构无业务月结独立复核' };
  await checked(
    await request.post(`/api/reports/${report.report.id}/review`, {
      headers: f.reviewer,
      data: command,
    }),
    200,
  );
  const close = await checked(
    await request.post('/api/b04-close/preflight', {
      headers: f.headers,
      data: {
        book_id: f.book.id,
        period: f.book.period,
        report_id: report.report.id,
        idempotency_key: `b02-close-${crypto.randomUUID()}`,
        reason: '本地虚构零业务月结预检',
      },
    }),
  );
  expect(close.close.status, JSON.stringify(close.checks)).toBe('preflight_passed');
  await checked(
    await request.post(`/api/b04-close/${close.close.id}/review`, {
      headers: f.reviewer,
      data: command,
    }),
    200,
  );
  return f;
}

test('B02 不可逆锁期必须二次输入期间：输错禁用、输对放行', async ({ page, request }) => {
  const f = await preLockableClose(request);
  await pinPersona(page, 'accountant');
  await page.goto(
    `/workspace/formal-books?book=${f.book.id}&company=${f.company.id}&period=${f.book.period}`,
  );
  await page.getByText('正式月结与报表发布（对账、独立复核、锁期）', { exact: true }).click();
  await page.getByLabel('办理说明（至少 3 字）').fill('本地虚构锁期二次输入验收');
  await page.getByLabel('本次报表版本').selectOption({ index: 1 });
  const lockButton = page.getByRole('button', { name: '确认正式锁期', exact: true });
  await expect(lockButton).toBeVisible({ timeout: 30_000 });
  await lockButton.click();

  const dialog = page.getByRole('dialog', { name: '确认正式锁期', exact: true });
  await expect(dialog).toBeVisible();
  await expect(dialog).toContainText('不支持重开');
  const typed = dialog.locator('.confirm-phrase input');
  const confirm = dialog.getByRole('button', { name: '确认正式锁期', exact: true });
  await expect(confirm).toBeDisabled();
  await typed.fill('2026-13');
  await expect(confirm).toBeDisabled();
  await typed.fill('2026-10');
  await expect(confirm).toBeEnabled();
  await confirm.click();
  await expect(page.getByText(/本月已正式锁期/)).toBeVisible();
});
