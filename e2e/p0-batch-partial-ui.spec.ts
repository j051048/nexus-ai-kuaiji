import { expect, test } from '@playwright/test';
import { choosePersona } from './ui-navigation';

test.setTimeout(90_000);

test('P0 批量部分失败逐项展示（前端 mock，状态机由后端单测覆盖）', async ({ page }) => {
  await page.route('**/api/firm/batch-operations/monthly-open', async (route) => {
    await route.fulfill({
      status: 200,
      json: {
        id: 'synthetic-batch',
        period: '2026-11',
        status: 'completed',
        succeeded: 1,
        skipped: 0,
        failed: 1,
        progress: { total: 2, completed: 2, percent: 100 },
        items: [
          {
            id: 'item-ok',
            company_id: 'company-a',
            company_name: '合成客户甲',
            status: 'succeeded',
            error_code: '',
            error_detail: '',
            result: { book_id: 'book-a', message: '' },
          },
          {
            id: 'item-fail',
            company_id: 'company-b',
            company_name: '合成客户乙',
            status: 'failed',
            error_code: 'company_blocked',
            error_detail: '合成失败：本月长期账套尚未建立',
            result: null,
          },
        ],
      },
    });
  });
  await page.route('**/api/firm/batch-operations/monthly-open/preview', async (route) => {
    await route.fulfill({
      status: 200,
      json: {
        period: '2026-11',
        ready: 2,
        skipped: 0,
        blocked: 0,
        results: [
          { company_id: 'company-a', company_name: '合成客户甲', status: 'ready' },
          { company_id: 'company-b', company_name: '合成客户乙', status: 'ready' },
        ],
        notes: [],
      },
    });
  });
  let retried: Record<string, unknown> | null = null;
  await page.route('**/api/firm/batch-operations/**/retry', async (route) => {
    retried = route.request().postDataJSON() as Record<string, unknown>;
    await route.fulfill({
      status: 200,
      json: {
        id: 'synthetic-batch',
        period: '2026-11',
        status: 'completed',
        succeeded: 2,
        skipped: 0,
        failed: 0,
        progress: { total: 2, completed: 2, percent: 100 },
        items: [
          {
            id: 'item-ok',
            company_id: 'company-a',
            company_name: '合成客户甲',
            status: 'succeeded',
            error_detail: '',
            result: { book_id: 'book-a' },
          },
          {
            id: 'item-retry',
            company_id: 'company-b',
            company_name: '合成客户乙',
            status: 'succeeded',
            error_detail: '',
            result: { book_id: 'book-b' },
          },
        ],
      },
    });
  });
  await page.goto('/', { waitUntil: 'domcontentloaded' });
  await choosePersona(page, 'accountant');
  const nav = page.getByRole('navigation', { name: '主导航' });
  await nav.getByRole('button', { name: '月度交付', exact: true }).click();
  await page.getByRole('button', { name: '批量开期', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: '月初批量开期', exact: true });
  await dialog.getByLabel('批量开期操作依据').fill('合成验证：部分成功展示');
  // 执行前必须先看到服务端预检结果（P02）。
  await dialog.getByRole('button', { name: '预检范围', exact: true }).click();
  await expect(dialog.getByText('将开期', { exact: false })).toBeVisible();
  await dialog.getByRole('button', { name: '开始批量开期', exact: true }).click();
  await expect(
    dialog.getByText('已开期 1 家 · 已存在 0 家 · 待人工处理 1 家 · 进度 2/2'),
  ).toBeVisible({ timeout: 30_000 });
  await expect(dialog.getByText('合成客户甲')).toBeVisible();
  await expect(dialog.getByText('合成客户乙')).toBeVisible();
  await expect(dialog.getByText('合成失败：本月长期账套尚未建立')).toBeVisible();
  await dialog.getByRole('button', { name: '只重试失败项', exact: true }).click();
  await expect(
    dialog.getByText('已开期 2 家 · 已存在 0 家 · 待人工处理 0 家 · 进度 2/2'),
  ).toBeVisible({ timeout: 30_000 });
  expect(retried).toMatchObject({ reason: '只重试失败的客户开期' });
  expect((retried as Record<string, unknown>).idempotency_key).toBeTruthy();
});
